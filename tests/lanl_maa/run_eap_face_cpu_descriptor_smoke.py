#!/usr/bin/env python3

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import tempfile

from run_branson_descriptor_staging_smoke import (
    check_equal,
    read_stats,
)

DATA_VADDR = 0x1000000000
DATA_PADDR = 0x02000000
DATA_BYTES = 0x100000
CONTROL_VADDR = 0x1000200000
CONTROL_PADDR = 0x04000000
CONTROL_BYTES = 0x1000
DESCRIPTOR_OFFSET = 0x0000
FACE_OFFSET = 0x1000
CELL_OFFSET = 0x2000
OUTPUT_OFFSET = 0x4000
COMPLETION_OFFSET = 0x6000
CELLS = 16
FACES = 32


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def double_bits(value):
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def dataset():
    cells = []
    for cell in range(CELLS):
        cells.append(
            (
                1.0 + (cell % 4) * 0.25,
                0.5 + (cell % 5) * 0.25,
                -8.0 + cell * 0.75,
                6.0 - cell * 0.5,
            )
        )

    faces = []
    for ordinal in range(FACES):
        if ordinal % 7 == 0:
            faces.append((0x7FFFFFFF, 0x7FFFFFFF, False))
            continue
        if ordinal % 4 == 1:
            low = ordinal % 4
            high = (ordinal + 1) % 4
        else:
            low = (ordinal * 5 + 3) % CELLS
            high = (ordinal * 7 + 1) % CELLS
        if low == high:
            high = (high + 1) % CELLS
        faces.append((low, high, True))
    return cells, faces


def packed_faces(faces):
    return [
        low | (high << 31) | ((1 << 62) if active else 0)
        for low, high, active in faces
    ]


def c_array(values):
    return ",\n".join(f"    UINT64_C(0x{value:016x})" for value in values)


def generate_source(path, cells, faces, bad_value=False):
    cell_words = [double_bits(value) for record in cells for value in record]
    if bad_value:
        poison_cell = next(
            high for low, high, active in reversed(faces) if active
        )
        cell_words[poison_cell * 4] = 0x7FF0000000000000
    face_words = packed_faces(faces)
    active_faces = sum(active for _, _, active in faces)
    source = f"""
#include <stdint.h>

#define DATA_VADDR UINT64_C(0x{DATA_VADDR:x})
#define DATA_PADDR UINT64_C(0x{DATA_PADDR:x})
#define CONTROL_VADDR UINT64_C(0x{CONTROL_VADDR:x})
#define DESCRIPTOR_OFFSET UINT64_C(0x{DESCRIPTOR_OFFSET:x})
#define FACE_OFFSET UINT64_C(0x{FACE_OFFSET:x})
#define CELL_OFFSET UINT64_C(0x{CELL_OFFSET:x})
#define OUTPUT_OFFSET UINT64_C(0x{OUTPUT_OFFSET:x})
#define COMPLETION_OFFSET UINT64_C(0x{COMPLETION_OFFSET:x})
#define CELLS UINT64_C({CELLS})
#define FACES UINT64_C({FACES})
#define ACTIVE_FACES UINT64_C({active_faces})
#define OUTPUT_WORDS (UINT64_C(4) * CELLS)
#define EXPECT_BAD_VALUE UINT64_C({int(bad_value)})

static const uint64_t face_words[FACES] = {{
{c_array(face_words)}
}};

static const uint64_t cell_words[UINT64_C(4) * CELLS] = {{
{c_array(cell_words)}
}};

static double expected_outputs[OUTPUT_WORDS];

static double
bits_to_double(uint64_t bits)
{{
    union {{ uint64_t bits; double value; }} converted;
    converted.bits = bits;
    return converted.value;
}}

static uint64_t
double_to_bits(double value)
{{
    union {{ uint64_t bits; double value; }} converted;
    converted.value = value;
    return converted.bits;
}}

static void
fence(void)
{{
    __asm__ volatile("mfence" ::: "memory");
}}

static void __attribute__((noreturn))
finish(uint64_t code)
{{
    __asm__ volatile(
        "syscall"
        :
        : "a"(UINT64_C(60)), "D"(code)
        : "rcx", "r11", "memory");
    __builtin_unreachable();
}}

void __attribute__((noreturn))
_start(void)
{{
    volatile uint64_t *descriptor = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + DESCRIPTOR_OFFSET);
    volatile uint64_t *face_vector = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + FACE_OFFSET);
    volatile uint64_t *cell_records = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + CELL_OFFSET);
    volatile double *outputs = (volatile double *)(uintptr_t)(
        DATA_VADDR + OUTPUT_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);

    for (uint64_t word = 0; word < UINT64_C(4) * CELLS; ++word) {{
        cell_records[word] = cell_words[word];
    }}
    for (uint64_t face = 0; face < FACES; ++face) {{
        face_vector[face] = face_words[face];
    }}
    for (uint64_t cell = 0; cell < CELLS; ++cell) {{
        expected_outputs[cell] = bits_to_double(UINT64_C(0x7ff0000000000000));
        expected_outputs[CELLS + cell] =
            bits_to_double(UINT64_C(0xfff0000000000000));
        expected_outputs[UINT64_C(2) * CELLS + cell] =
            bits_to_double(UINT64_C(0x7ff0000000000000));
        expected_outputs[UINT64_C(3) * CELLS + cell] =
            bits_to_double(UINT64_C(0xfff0000000000000));
    }}
    for (uint64_t face = 0; !EXPECT_BAD_VALUE && face < FACES; ++face) {{
        const uint64_t packed = face_words[face];
        if ((packed & (UINT64_C(1) << 62)) == 0) {{
            continue;
        }}
        const uint64_t low = packed & ((UINT64_C(1) << 31) - 1);
        const uint64_t high =
            (packed >> 31) & ((UINT64_C(1) << 31) - 1);
        const double high_half_low = bits_to_double(cell_words[high * 4]);
        const double low_value_high =
            bits_to_double(cell_words[low * 4 + 3]);
        const double low_half_high =
            bits_to_double(cell_words[low * 4 + 1]);
        const double high_value_low =
            bits_to_double(cell_words[high * 4 + 2]);
        const double value =
            (high_half_low * low_value_high +
             low_half_high * high_value_low) /
            (high_half_low + low_half_high);
        if (value < expected_outputs[high]) {{
            expected_outputs[high] = value;
        }}
        if (value > expected_outputs[CELLS + high]) {{
            expected_outputs[CELLS + high] = value;
        }}
        if (value < expected_outputs[UINT64_C(2) * CELLS + low]) {{
            expected_outputs[UINT64_C(2) * CELLS + low] = value;
        }}
        if (value > expected_outputs[UINT64_C(3) * CELLS + low]) {{
            expected_outputs[UINT64_C(3) * CELLS + low] = value;
        }}
    }}
    for (uint64_t cell = 0; cell < CELLS; ++cell) {{
        outputs[cell] = bits_to_double(UINT64_C(0x7ff0000000000000));
        outputs[CELLS + cell] =
            bits_to_double(UINT64_C(0xfff0000000000000));
        outputs[UINT64_C(2) * CELLS + cell] =
            bits_to_double(UINT64_C(0x7ff0000000000000));
        outputs[UINT64_C(3) * CELLS + cell] =
            bits_to_double(UINT64_C(0xfff0000000000000));
    }}
    for (uint64_t word = 0; word < 4; ++word) {{
        completion[word] = 0;
    }}

    descriptor[0] = UINT64_C(0x0004000131414d4c);
    descriptor[1] = FACES;
    descriptor[2] = DATA_PADDR + FACE_OFFSET;
    descriptor[3] = DATA_PADDR + CELL_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = DATA_PADDR + OUTPUT_OFFSET;
    descriptor[6] = CELLS;
    descriptor[7] = 0;
    fence();

    if ((control[UINT64_C(0x128) / 8] & (UINT64_C(1) << 4)) == 0) {{
        finish(UINT64_C(15));
    }}
    control[0] = 0;
    fence();
    uint64_t status = 0;
    for (uint64_t spin = 0; spin < UINT64_C(1000000); ++spin) {{
        status = control[UINT64_C(0x110) / 8];
        if (status == UINT64_C(4) || status == UINT64_C(8)) {{
            break;
        }}
        if (status != UINT64_C(1) && status != UINT64_C(2)) {{
            finish(UINT64_C(12));
        }}
    }}
    if (EXPECT_BAD_VALUE) {{
        if (status != UINT64_C(8) ||
            control[UINT64_C(0x120) / 8] != UINT64_C(18)) {{
            finish(UINT64_C(16));
        }}
        fence();
        for (uint64_t cell = 0; cell < CELLS; ++cell) {{
            if (double_to_bits(outputs[cell]) !=
                    UINT64_C(0x7ff0000000000000) ||
                double_to_bits(outputs[CELLS + cell]) !=
                    UINT64_C(0xfff0000000000000) ||
                double_to_bits(outputs[UINT64_C(2) * CELLS + cell]) !=
                    UINT64_C(0x7ff0000000000000) ||
                double_to_bits(outputs[UINT64_C(3) * CELLS + cell]) !=
                    UINT64_C(0xfff0000000000000)) {{
                finish(UINT64_C(17));
            }}
        }}
        if (completion[0] != 0 || completion[1] != 0 ||
            completion[2] != 0 || completion[3] != 0) {{
            finish(UINT64_C(18));
        }}
        finish(0);
    }}
    if (status != UINT64_C(4)) {{
        finish(UINT64_C(20) + control[UINT64_C(0x120) / 8]);
    }}
    if (control[UINT64_C(0x118) / 8] != 0) {{
        finish(UINT64_C(14));
    }}
    fence();

    for (uint64_t word = 0; word < OUTPUT_WORDS; ++word) {{
        const double actual = outputs[word];
        if (double_to_bits(actual) !=
            double_to_bits(expected_outputs[word])) {{
            finish(UINT64_C(40) + word);
        }}
    }}
    if (completion[0] != UINT64_C(0x0004000143414d4c) ||
        completion[1] != 0 || completion[2] != FACES ||
        completion[3] != UINT64_C(4) * ACTIVE_FACES) {{
        finish(UINT64_C(120));
    }}
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def build_program(root, cells, faces, bad_value=False):
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("EAP face CPU descriptor smoke requires cc")
    suffix = "_bad_value" if bad_value else ""
    source = root / f"eap_face_cpu_descriptor{suffix}.c"
    binary = root / f"eap_face_cpu_descriptor{suffix}.elf"
    generate_source(source, cells, faces, bad_value)
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-nostdlib",
            "-static",
            "-fno-pie",
            "-no-pie",
            "-fno-stack-protector",
            "-fno-builtin",
            "-Wl,--build-id=none",
            "-Wl,-e,_start",
            source,
            "-o",
            binary,
        ],
        check=True,
    )
    return source, binary


def read_scalar(path, name):
    prefix = name + " "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return int(float(line.split()[1]))
    return None


def validate(stats, metadata, stats_path):
    errors = []
    accelerator = stats["lanl_maa"]
    if metadata["bad_value"]:
        negative_expected = {
            "activeContextHighWaterMark": metadata["active_faces"],
            "physicalAtomicUpdates": 0,
            "atomicAcknowledgements": 0,
            "updateOperationsAcknowledged": 0,
            "descriptorResultWrites": 0,
            "descriptorCompletionWrites": 0,
            "descriptorErrors": 1,
            "descriptorFaceUpdatesAcknowledged": 0,
        }
        for name, value in negative_expected.items():
            check_equal(errors, accelerator, name, value)
        if accelerator.get("physicalLineReads", 0) <= 0:
            errors.append("bad-value case issued no input reads")
        if errors:
            raise RuntimeError(
                "LANLMAA EAP bad-value descriptor smoke failed:\n  "
                + "\n  ".join(errors)
            )
        return

    active = metadata["active_faces"]
    faces = metadata["faces"]
    logical_gathers = active * 4
    logical_updates = active * 4
    expected = {
        "logicalItems": faces,
        "activeContextHighWaterMark": active,
        "logicalMemoryAccesses": logical_gathers + logical_updates,
        "responsesFannedOut": logical_gathers,
        "completionsRetired": faces,
        "verificationFailures": 0,
        "updateOperationsAcknowledged": logical_updates,
        "descriptorDoorbells": 1,
        "descriptorBusyRejections": 0,
        "descriptorFetches": 1,
        "descriptorAddressLineReads": (faces + 7) // 8,
        "descriptorAddressesLoaded": faces,
        "descriptorResultWrites": 0,
        "descriptorCompletionWrites": 1,
        "descriptorErrors": 0,
        "descriptorPredicatesSkipped": faces - active,
        "descriptorFaceValuesComputed": active,
        "descriptorFaceUpdatesAcknowledged": logical_updates,
        "atomicAddUpdates": 0,
        "atomicMinUpdates": 0,
        "atomicMaxUpdates": 0,
        "atomicFp64AddUpdates": 0,
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)

    reads = accelerator.get("physicalLineReads")
    merges = accelerator.get("lineMergeHits")
    if reads is None or merges is None or reads + merges != logical_gathers:
        errors.append(
            f"gather accounting mismatch: reads={reads}, merges={merges}, "
            f"logical={logical_gathers}"
        )
    atomics = accelerator.get("physicalAtomicUpdates")
    min_atomics = accelerator.get("atomicFp64MinUpdates")
    max_atomics = accelerator.get("atomicFp64MaxUpdates")
    if (
        atomics is None
        or min_atomics is None
        or max_atomics is None
        or atomics != min_atomics + max_atomics
    ):
        errors.append(
            f"atomic operation mismatch: total={atomics}, min={min_atomics}, "
            f"max={max_atomics}"
        )
    check_equal(errors, accelerator, "updateDrains", atomics)
    check_equal(errors, accelerator, "atomicAcknowledgements", atomics)
    check_equal(errors, accelerator, "atomicOldValuesReturned", atomics)
    if accelerator.get("responses") != reads + atomics:
        errors.append("read/atomic response accounting did not close")

    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    acceptances = accelerator.get("retryPacketAcceptances")
    if failures != notifications or notifications != resubmissions:
        errors.append(
            f"retry mismatch: failures={failures}, notifications="
            f"{notifications}, resubmissions={resubmissions}"
        )
    if (
        acceptances is None
        or resubmissions is None
        or not 0 <= acceptances <= resubmissions
    ):
        errors.append(
            f"invalid retry acceptances={acceptances}, "
            f"resubmissions={resubmissions}"
        )

    cpu_insts = read_scalar(stats_path, "system.cpu.commitStats0.numInsts")
    if cpu_insts is None or cpu_insts <= 0:
        errors.append("CPU retired no instructions")
    cache_accesses = read_scalar(
        stats_path, "system.maa_cache.overallAccesses_T::total"
    )
    cache_misses = read_scalar(
        stats_path, "system.maa_cache.overallMisses_T::total"
    )
    membus_snoops = read_scalar(stats_path, "system.membus.snoops")
    for name, value in (
        ("maa_cache_accesses", cache_accesses),
        ("maa_cache_misses", cache_misses),
        ("membus_snoops", membus_snoops),
    ):
        if value is None or value <= 0:
            errors.append(f"{name} did not exercise coherence")
    if errors:
        raise RuntimeError(
            "LANLMAA EAP face CPU descriptor smoke failed:\n  "
            + "\n  ".join(errors)
        )


def run_smoke(args, root):
    cells, faces = dataset()
    source, binary = build_program(root, cells, faces, args.bad_value)
    active_faces = sum(active for _, _, active in faces)
    metadata = {
        "schema_version": 1,
        "mapping": "EAP Patterns inside_com3b paired-face gather, "
        "predicate, and FP64 MIN/MAX updates",
        "source_revision": "85211296c2358c4efef876ddcf67827ef613231d",
        "source_file": "src/derivatives_common_template.f90",
        "source_sha256": "ea3a163c6954627de0dd9732f947a94b"
        "f3959b4394e6b5579424929a086a717c",
        "faces": FACES,
        "active_faces": active_faces,
        "inactive_faces": FACES - active_faces,
        "cells": CELLS,
        "descriptor_opcode": 4,
        "descriptor_offset": DESCRIPTOR_OFFSET,
        "face_vector_offset": FACE_OFFSET,
        "cell_record_offset": CELL_OFFSET,
        "output_offset": OUTPUT_OFFSET,
        "completion_offset": COMPLETION_OFFSET,
        "data_vaddr": DATA_VADDR,
        "data_paddr": DATA_PADDR,
        "data_bytes": DATA_BYTES,
        "control_vaddr": CONTROL_VADDR,
        "control_paddr": CONTROL_PADDR,
        "control_bytes": CONTROL_BYTES,
        "descriptor_paddr": DATA_PADDR + DESCRIPTOR_OFFSET,
        "program_source_sha256": file_sha256(source),
        "program_elf_sha256": file_sha256(binary),
        "exact_output_words_checked": CELLS * 4,
        "exact_completion_words_checked": 4,
        "inactive_poison_indices": True,
        "bad_value": args.bad_value,
        "l1_caches": args.l1_caches,
        "maa_coherence_cache": (
            {
                "size": args.maa_cache_size,
                "associativity": args.maa_cache_assoc,
                "mshrs": args.maa_cache_mshrs,
                "targets_per_mshr": args.maa_cache_targets_per_mshr,
                "write_buffers": args.maa_cache_write_buffers,
            }
            if args.l1_caches
            else None
        ),
        "claim_boundary": "Generated real-X86 microbenchmark over an "
        "EAP-derived compact face/cell ABI; not native EAP or FLAG "
        "application submission, pressure/special branch coverage, "
        "application correctness, or performance.",
    }
    metadata_path = root / "eap_face_cpu_descriptor_metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )

    outdir = root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--binary={binary}",
        f"--metadata={metadata_path}",
    ]
    if args.l1_caches:
        command.append("--l1-caches")
        command.extend(
            [
                f"--maa-cache-size={args.maa_cache_size}",
                f"--maa-cache-assoc={args.maa_cache_assoc}",
                f"--maa-cache-mshrs={args.maa_cache_mshrs}",
                "--maa-cache-targets-per-mshr="
                f"{args.maa_cache_targets_per_mshr}",
                f"--maa-cache-write-buffers={args.maa_cache_write_buffers}",
            ]
        )
    result = subprocess.run(command, text=True, capture_output=True)
    (root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
    (root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "gem5 EAP face CPU descriptor failed:\n"
            + result.stdout
            + result.stderr
        )
    stats_path = outdir / "stats.txt"
    validate(read_stats(stats_path), metadata, stats_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--l1-caches", action="store_true")
    parser.add_argument("--bad-value", action="store_true")
    parser.add_argument("--maa-cache-size", default="4KiB")
    parser.add_argument("--maa-cache-assoc", type=int, default=2)
    parser.add_argument("--maa-cache-mshrs", type=int, default=8)
    parser.add_argument("--maa-cache-targets-per-mshr", type=int, default=2)
    parser.add_argument("--maa-cache-write-buffers", type=int, default=2)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "eap_face_cpu_descriptor_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument("--outdir", type=pathlib.Path)
    args = parser.parse_args()

    if not args.l1_caches:
        parser.error("this coherence slice requires --l1-caches")
    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run_smoke(args, root)
    else:
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-eap-face-cpu-descriptor-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    mode = " bad-value fail-closed" if args.bad_value else ""
    print(f"LANLMAA EAP face CPU descriptor{mode} with L1 caches: PASS")


if __name__ == "__main__":
    main()
