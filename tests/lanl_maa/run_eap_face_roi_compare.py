#!/usr/bin/env python3

import argparse
import hashlib
import json
import math
import pathlib
import shutil
import struct
import subprocess
import tempfile

from run_branson_descriptor_staging_smoke import (
    check_equal,
    read_stats,
)
from run_eap_face_cpu_descriptor_smoke import read_scalar

DATA_VADDR = 0x1000000000
DATA_PADDR = 0x02000000
DATA_BYTES = 0x200000
CONTROL_VADDR = 0x1000400000
CONTROL_PADDR = 0x04000000
CONTROL_BYTES = 0x1000
DESCRIPTOR_OFFSET = 0x0000
FACE_OFFSET = 0x1000
CELL_OFFSET = 0x4018
OUTPUT_OFFSET = 0x8000
COMPLETION_OFFSET = 0xA000
FACE_VALUE_OFFSET = 0xB000

INACTIVE = 0
INTERNAL = 1
LOW_BOUNDARY = 2
HIGH_BOUNDARY = 3

CASES = {
    "representative_anchor": {"faces": 256, "cells": 128},
    "lightweight_equivalence": {"faces": 64, "cells": 32},
}


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def double_bits(value):
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def c_array(values):
    return ",\n".join(f"    UINT64_C(0x{value:016x})" for value in values)


def make_dataset(cell_count, face_count):
    if cell_count < 8 or face_count < 8:
        raise ValueError("EAP ROI dataset requires at least eight cells/faces")
    cells = [
        (
            1.0 + (cell % 4) * 0.25,
            0.5 + (cell % 5) * 0.25,
            -6.0 + cell * 0.75,
            5.0 - cell * 0.625,
            0.0 if cell % 4 == 0 else 0.5 + (cell % 6) * 0.25,
        )
        for cell in range(cell_count)
    ]
    faces = []
    for ordinal in range(face_count):
        branch = ordinal % 8
        if branch == 0:
            faces.append((INACTIVE, 0x7FFFFFFF, 0x7FFFFFFF, ordinal))
        elif branch == 1:
            faces.append(
                (LOW_BOUNDARY, (ordinal * 3) % cell_count, 0, ordinal)
            )
        elif branch == 2:
            faces.append(
                (HIGH_BOUNDARY, 0, (ordinal * 5) % cell_count, ordinal)
            )
        elif branch == 3:
            faces.append((INTERNAL, 0, 4, ordinal))
        else:
            low = (ordinal * 5 + 3) % cell_count
            high = (ordinal * 7 + 1) % cell_count
            if low == high:
                high = (high + 1) % cell_count
            faces.append((INTERNAL, low, high, ordinal))
    face_values = [-4.0 + ordinal * 0.375 for ordinal in range(face_count)]
    return cells, faces, face_values


def pack_faces(faces):
    words = []
    for kind, low, high, ordinal in faces:
        if kind == INTERNAL or kind == INACTIVE:
            payload0, payload1 = low, high
        elif kind == LOW_BOUNDARY:
            payload0, payload1 = low, ordinal
        else:
            payload0, payload1 = high, ordinal
        words.append(payload0 | (payload1 << 31) | (kind << 62))
    return words


def compute_face(cells, face_values, face):
    kind, low, high, ordinal = face
    if kind == LOW_BOUNDARY or kind == HIGH_BOUNDARY:
        return face_values[ordinal]
    low_cell = cells[low]
    high_cell = cells[high]
    low_rho = low_cell[4]
    high_rho = high_cell[4]
    if low_rho <= 0.0 and high_rho <= 0.0:
        return 0.0
    high_coefficient = high_cell[0]
    low_coefficient = low_cell[1]
    if low_cell[2] * high_cell[3] <= 0.0:
        high_coefficient *= high_rho
        low_coefficient *= low_rho
    high_term = high_coefficient * low_cell[3]
    return (high_term + low_coefficient * high_cell[2]) / (
        high_coefficient + low_coefficient
    )


def make_expected(cells, faces, face_values):
    cell_count = len(cells)
    output = [math.inf] * cell_count + [-math.inf] * cell_count
    output += [math.inf] * cell_count + [-math.inf] * cell_count
    for face in faces:
        kind, low, high, _ = face
        if kind == INACTIVE:
            continue
        value = compute_face(cells, face_values, face)
        if kind == INTERNAL or kind == HIGH_BOUNDARY:
            output[high] = min(output[high], value)
            output[cell_count + high] = max(output[cell_count + high], value)
        if kind == INTERNAL or kind == LOW_BOUNDARY:
            output[2 * cell_count + low] = min(
                output[2 * cell_count + low], value
            )
            output[3 * cell_count + low] = max(
                output[3 * cell_count + low], value
            )
    return [double_bits(value) for value in output]


def workload_counts(cells, faces):
    counts = {
        "active": 0,
        "inactive": 0,
        "boundary": 0,
        "vacuum": 0,
        "pressure_weighted": 0,
        "gathers": 0,
        "updates": 0,
    }
    for kind, low, high, _ in faces:
        if kind == INACTIVE:
            counts["inactive"] += 1
            continue
        counts["active"] += 1
        if kind != INTERNAL:
            counts["boundary"] += 1
            counts["gathers"] += 1
            counts["updates"] += 2
            continue
        counts["updates"] += 4
        low_cell = cells[low]
        high_cell = cells[high]
        if low_cell[4] <= 0.0 and high_cell[4] <= 0.0:
            counts["vacuum"] += 1
            counts["gathers"] += 2
        else:
            counts["gathers"] += 8
            if low_cell[2] * high_cell[3] <= 0.0:
                counts["pressure_weighted"] += 1
    return counts


def generate_source(path, cells, faces, face_values, expected, counts):
    cell_count = len(cells)
    face_count = len(faces)
    cell_words = [double_bits(value) for record in cells for value in record]
    source = f"""
#include <stdint.h>

#include <gem5/m5ops.h>

#ifndef USE_MAA
#error USE_MAA must be defined to zero or one
#endif

#define DATA_VADDR UINT64_C(0x{DATA_VADDR:x})
#define DATA_PADDR UINT64_C(0x{DATA_PADDR:x})
#define CONTROL_VADDR UINT64_C(0x{CONTROL_VADDR:x})
#define DESCRIPTOR_OFFSET UINT64_C(0x{DESCRIPTOR_OFFSET:x})
#define FACE_OFFSET UINT64_C(0x{FACE_OFFSET:x})
#define CELL_OFFSET UINT64_C(0x{CELL_OFFSET:x})
#define OUTPUT_OFFSET UINT64_C(0x{OUTPUT_OFFSET:x})
#define COMPLETION_OFFSET UINT64_C(0x{COMPLETION_OFFSET:x})
#define FACE_VALUE_OFFSET UINT64_C(0x{FACE_VALUE_OFFSET:x})
#define CELLS UINT64_C({cell_count})
#define FACES UINT64_C({face_count})
#define OUTPUT_WORDS (UINT64_C(4) * CELLS)
#define ACTIVE_FACES UINT64_C({counts['active']})
#define LOGICAL_UPDATES UINT64_C({counts['updates']})

static const uint64_t cell_words[UINT64_C(5) * CELLS] = {{
{c_array(cell_words)}
}};
static const uint64_t face_words[FACES] = {{
{c_array(pack_faces(faces))}
}};
static const uint64_t face_value_words[FACES] = {{
{c_array([double_bits(value) for value in face_values])}
}};
static const uint64_t expected_words[OUTPUT_WORDS] = {{
{c_array(expected)}
}};

#if !USE_MAA
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
#endif

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

static void
reset_output(volatile uint64_t *output, volatile uint64_t *completion)
{{
    for (uint64_t cell = 0; cell < CELLS; ++cell) {{
        output[cell] = UINT64_C(0x7ff0000000000000);
        output[CELLS + cell] = UINT64_C(0xfff0000000000000);
        output[UINT64_C(2) * CELLS + cell] =
            UINT64_C(0x7ff0000000000000);
        output[UINT64_C(3) * CELLS + cell] =
            UINT64_C(0xfff0000000000000);
    }}
    for (uint64_t word = 0; word < UINT64_C(4); ++word) {{
        completion[word] = 0;
    }}
}}

#if !USE_MAA
static void
update_min(volatile uint64_t *destination, double value)
{{
    if (value < bits_to_double(*destination)) {{
        *destination = double_to_bits(value);
    }}
}}

static void
update_max(volatile uint64_t *destination, double value)
{{
    if (value > bits_to_double(*destination)) {{
        *destination = double_to_bits(value);
    }}
}}

static void
scalar_kernel(
    const volatile uint64_t *face, const volatile uint64_t *cell,
    const volatile uint64_t *face_value, volatile uint64_t *output)
{{
    const uint64_t payload_mask = (UINT64_C(1) << 31) - 1;
    for (uint64_t ordinal = 0; ordinal < FACES; ++ordinal) {{
        const uint64_t packed = face[ordinal];
        const uint64_t kind = packed >> 62;
        if (kind == UINT64_C(0)) {{
            continue;
        }}
        const uint64_t payload0 = packed & payload_mask;
        const uint64_t payload1 = (packed >> 31) & payload_mask;
        uint64_t low = 0;
        uint64_t high = 0;
        double value = 0.0;
        if (kind == UINT64_C(1)) {{
            low = payload0;
            high = payload1;
            const volatile uint64_t *low_cell = cell + UINT64_C(5) * low;
            const volatile uint64_t *high_cell =
                cell + UINT64_C(5) * high;
            const double low_rho = bits_to_double(low_cell[4]);
            const double high_rho = bits_to_double(high_cell[4]);
            if (low_rho <= 0.0 && high_rho <= 0.0) {{
                value = 0.0;
            }} else {{
                const double low_sign = bits_to_double(low_cell[2]);
                const double high_sign = bits_to_double(high_cell[3]);
                const int weighted = low_sign * high_sign <= 0.0;
                double high_coefficient = bits_to_double(high_cell[0]);
                double low_coefficient = bits_to_double(low_cell[1]);
                if (weighted) {{
                    high_coefficient *= high_rho;
                    low_coefficient *= low_rho;
                }}
                const double high_term =
                    high_coefficient * bits_to_double(low_cell[3]);
                value =
                    (high_term +
                     low_coefficient * bits_to_double(high_cell[2])) /
                    (high_coefficient + low_coefficient);
            }}
        }} else {{
            value = bits_to_double(face_value[payload1]);
            if (kind == UINT64_C(2)) {{
                low = payload0;
            }} else {{
                high = payload0;
            }}
        }}
        if (kind == UINT64_C(1) || kind == UINT64_C(3)) {{
            update_min(output + high, value);
            update_max(output + CELLS + high, value);
        }}
        if (kind == UINT64_C(1) || kind == UINT64_C(2)) {{
            update_min(output + UINT64_C(2) * CELLS + low, value);
            update_max(output + UINT64_C(3) * CELLS + low, value);
        }}
    }}
}}
#endif

void __attribute__((noreturn))
_start(void)
{{
#if USE_MAA
    volatile uint64_t *descriptor = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + DESCRIPTOR_OFFSET);
#endif
    volatile uint64_t *face = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + FACE_OFFSET);
    volatile uint64_t *cell = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + CELL_OFFSET);
    volatile uint64_t *output = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + OUTPUT_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *face_value = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + FACE_VALUE_OFFSET);
#if USE_MAA
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);
#endif

    for (uint64_t word = 0; word < UINT64_C(5) * CELLS; ++word) {{
        cell[word] = cell_words[word];
    }}
    for (uint64_t ordinal = 0; ordinal < FACES; ++ordinal) {{
        face[ordinal] = face_words[ordinal];
        face_value[ordinal] = face_value_words[ordinal];
    }}
    reset_output(output, completion);
    fence();

#if USE_MAA
    if ((control[UINT64_C(0x128) / 8] & (UINT64_C(1) << 4)) == 0) {{
        finish(UINT64_C(15));
    }}
#endif

    m5_work_begin(0, 0);
#if USE_MAA
    descriptor[0] = UINT64_C(0x0604000131414d4c);
    descriptor[1] = FACES;
    descriptor[2] = DATA_PADDR + FACE_OFFSET;
    descriptor[3] = DATA_PADDR + CELL_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = DATA_PADDR + OUTPUT_OFFSET;
    descriptor[6] = CELLS | (FACES << 32);
    descriptor[7] = DATA_PADDR + FACE_VALUE_OFFSET;
    fence();
    control[0] = 0;
    fence();
    uint64_t status = 0;
    for (uint64_t spin = 0; spin < UINT64_C(10000000); ++spin) {{
        status = control[UINT64_C(0x110) / 8];
        if (status == UINT64_C(4) || status == UINT64_C(8)) {{
            break;
        }}
        if (status != UINT64_C(1) && status != UINT64_C(2)) {{
            finish(UINT64_C(20));
        }}
    }}
    if (status != UINT64_C(4)) {{
        finish(UINT64_C(21) + control[UINT64_C(0x120) / 8]);
    }}
    fence();
#else
    scalar_kernel(face, cell, face_value, output);
    fence();
#endif
    m5_work_end(0, 0);

    for (uint64_t word = 0; word < OUTPUT_WORDS; ++word) {{
        if (output[word] != expected_words[word]) {{
            finish(UINT64_C(100) + word % UINT64_C(100));
        }}
    }}
#if USE_MAA
    if (completion[0] != UINT64_C(0x0004000143414d4c) ||
        completion[1] != 0 || completion[2] != FACES ||
        completion[3] != LOGICAL_UPDATES) {{
        finish(UINT64_C(1000));
    }}
#else
    if (completion[0] != 0 || completion[1] != 0 ||
        completion[2] != 0 || completion[3] != 0) {{
        finish(UINT64_C(1001));
    }}
#endif
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def compile_programs(repo, root, source):
    compiler = shutil.which("cc")
    if compiler is None:
        raise RuntimeError("EAP ROI comparison requires cc")
    include = repo / "include"
    m5op_source = repo / "util/m5/src/abi/x86/m5op.S"
    m5op_object = root / "m5op.o"
    subprocess.run(
        [
            compiler,
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{include}",
            "-c",
            m5op_source,
            "-o",
            m5op_object,
        ],
        check=True,
    )
    binaries = {}
    for variant, use_maa in (("scalar_cpu", 0), ("maa_descriptor", 1)):
        object_path = root / f"{variant}.o"
        binary = root / f"{variant}.elf"
        subprocess.run(
            [
                compiler,
                "-std=c11",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-ffreestanding",
                "-fno-pie",
                "-fno-stack-protector",
                "-fno-builtin",
                "-fno-tree-vectorize",
                "-ffp-contract=off",
                f"-DUSE_MAA={use_maa}",
                f"-I{include}",
                "-c",
                source,
                "-o",
                object_path,
            ],
            check=True,
        )
        subprocess.run(
            [
                compiler,
                "-nostdlib",
                "-static",
                "-fno-pie",
                "-no-pie",
                "-Wl,--build-id=none",
                "-Wl,-e,_start",
                object_path,
                m5op_object,
                "-o",
                binary,
            ],
            check=True,
        )
        binaries[variant] = binary
    return binaries


def validate_maa(stats, metadata):
    errors = []
    accelerator = stats["lanl_maa"]
    counts = metadata["counts"]
    expected = {
        "logicalItems": metadata["faces"],
        "logicalMemoryAccesses": counts["gathers"] + counts["updates"],
        "responsesFannedOut": counts["gathers"],
        "completionsRetired": metadata["faces"],
        "verificationFailures": 0,
        "updateOperationsAcknowledged": counts["updates"],
        "descriptorDoorbells": 1,
        "descriptorBusyRejections": 0,
        "descriptorRearms": 0,
        "descriptorFetches": 1,
        "descriptorAddressLineReads": (metadata["faces"] + 7) // 8,
        "descriptorAddressesLoaded": metadata["faces"],
        "descriptorResultWrites": 0,
        "descriptorCompletionWrites": 1,
        "descriptorErrors": 0,
        "descriptorPredicatesSkipped": counts["inactive"],
        "descriptorFaceValuesComputed": counts["active"],
        "descriptorFaceVacuumValues": counts["vacuum"],
        "descriptorFacePressureWeightedValues": counts["pressure_weighted"],
        "descriptorFaceBoundaryValues": counts["boundary"],
        "descriptorFaceUpdatesAcknowledged": counts["updates"],
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)

    reads = accelerator.get("physicalLineReads")
    merges = accelerator.get("lineMergeHits")
    atomics = accelerator.get("physicalAtomicUpdates")
    min_atomics = accelerator.get("atomicFp64MinUpdates")
    max_atomics = accelerator.get("atomicFp64MaxUpdates")
    if reads is None or merges is None or reads + merges != counts["gathers"]:
        errors.append("gather accounting did not close")
    if (
        atomics is None
        or min_atomics is None
        or max_atomics is None
        or atomics != min_atomics + max_atomics
    ):
        errors.append("atomic MIN/MAX accounting did not close")
    check_equal(errors, accelerator, "updateDrains", atomics)
    check_equal(errors, accelerator, "atomicAcknowledgements", atomics)
    check_equal(errors, accelerator, "atomicOldValuesReturned", atomics)
    if reads is not None and atomics is not None:
        check_equal(errors, accelerator, "responses", reads + atomics)
    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    acceptances = accelerator.get("retryPacketAcceptances")
    if failures != notifications or notifications != resubmissions:
        errors.append("retry accounting did not close")
    if (
        acceptances is None
        or resubmissions is None
        or not 0 <= acceptances <= resubmissions
    ):
        errors.append("retry acceptance accounting is invalid")
    if errors:
        raise RuntimeError(
            "EAP face MAA ROI validation failed:\n  " + "\n  ".join(errors)
        )


def validate_scalar(stats):
    errors = []
    accelerator = stats["lanl_maa"]
    for name in (
        "logicalItems",
        "logicalMemoryAccesses",
        "physicalLineReads",
        "physicalAtomicUpdates",
        "descriptorDoorbells",
        "descriptorFetches",
        "descriptorCompletionWrites",
        "descriptorErrors",
    ):
        check_equal(errors, accelerator, name, 0)
    if errors:
        raise RuntimeError(
            "scalar ROI unexpectedly exercised LANLMAA:\n  "
            + "\n  ".join(errors)
        )


def metric(stats_path, name, required=True):
    value = read_scalar(stats_path, name)
    if required and value is None:
        raise RuntimeError(f"missing ROI stat {name} in {stats_path}")
    return value


def extract_roi_stats(stats_path, roi_stats_path):
    marker = "---------- Begin Simulation Statistics ----------"
    text = stats_path.read_text(encoding="utf-8")
    sections = text.split(marker)
    if len(sections) != 3:
        raise RuntimeError(
            "expected explicit ROI and automatic final stats in "
            f"{stats_path}; "
            f"found {len(sections) - 1} sections"
        )
    roi_stats_path.write_text(
        marker + sections[1],
        encoding="utf-8",
    )
    return len(sections) - 1


def extract_metrics(stats, stats_path, variant):
    accelerator = stats["lanl_maa"]
    metrics = {
        "roi_ticks": metric(stats_path, "simTicks"),
        "cpu_cycles": metric(stats_path, "system.cpu.numCycles"),
        "cpu_instructions": metric(
            stats_path, "system.cpu.commitStats0.numInsts"
        ),
        "cpu_dcache_accesses": metric(
            stats_path, "system.dcache.overallAccesses_T::total"
        ),
        "cpu_dcache_misses": metric(
            stats_path,
            "system.dcache.overallMisses_T::total",
            required=False,
        )
        or 0,
        "maa_cache_accesses": metric(
            stats_path,
            "system.maa_cache.overallAccesses_T::total",
            required=False,
        )
        or 0,
        "maa_cache_misses": metric(
            stats_path,
            "system.maa_cache.overallMisses_T::total",
            required=False,
        )
        or 0,
        "membus_snoops": metric(
            stats_path, "system.membus.snoops", required=False
        )
        or 0,
        "logical_gathers": accelerator.get("responsesFannedOut"),
        "logical_updates": accelerator.get("updateOperationsAcknowledged"),
        "physical_line_reads": accelerator.get("physicalLineReads"),
        "line_merge_hits": accelerator.get("lineMergeHits"),
        "physical_atomic_updates": accelerator.get("physicalAtomicUpdates"),
        "update_combiner_hits": accelerator.get("updateCombinerHits"),
        "descriptor_cycles": accelerator.get("descriptorCycles"),
        "engine_cycles": accelerator.get("engineCycles"),
    }
    if metrics["roi_ticks"] <= 0 or metrics["cpu_cycles"] <= 0:
        raise RuntimeError(f"{variant} produced an empty ROI")
    return metrics


def run_variant(args, case_root, variant, binary, base_metadata):
    variant_root = case_root / variant
    variant_root.mkdir()
    metadata = dict(base_metadata)
    metadata["variant"] = variant
    metadata["binary_sha256"] = file_sha256(binary)
    metadata_path = variant_root / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    outdir = variant_root / "m5out"
    command = [
        str(args.gem5.resolve()),
        f"--outdir={outdir}",
        str(args.config.resolve()),
        f"--binary={binary}",
        f"--metadata={metadata_path}",
    ]
    result = subprocess.run(command, text=True, capture_output=True)
    (variant_root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
    (variant_root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"gem5 {variant} ROI run failed:\n{result.stdout}{result.stderr}"
        )
    stats_path = outdir / "stats.txt"
    roi_stats_path = variant_root / "roi_stats.txt"
    stats_sections = extract_roi_stats(stats_path, roi_stats_path)
    stats = read_stats(roi_stats_path)
    if variant == "maa_descriptor":
        validate_maa(stats, metadata)
    else:
        validate_scalar(stats)
    return {
        "command": command,
        "binary_sha256": file_sha256(binary),
        "metadata_sha256": file_sha256(metadata_path),
        "raw_stats_sha256": file_sha256(stats_path),
        "roi_stats_sha256": file_sha256(roi_stats_path),
        "raw_stats_sections": stats_sections,
        "config_ini_sha256": file_sha256(outdir / "config.ini"),
        "stdout_sha256": file_sha256(variant_root / "gem5.stdout"),
        "stderr_sha256": file_sha256(variant_root / "gem5.stderr"),
        "metrics": extract_metrics(stats, roi_stats_path, variant),
    }


def run_case(args, repo, root, name, dimensions):
    case_root = root / name
    case_root.mkdir()
    cells, faces, face_values = make_dataset(
        dimensions["cells"], dimensions["faces"]
    )
    expected = make_expected(cells, faces, face_values)
    counts = workload_counts(cells, faces)
    if counts["vacuum"] == 0 or counts["pressure_weighted"] == 0:
        raise RuntimeError(f"{name} missed a required EAP branch")
    source = case_root / "eap_face_roi.c"
    generate_source(source, cells, faces, face_values, expected, counts)
    binaries = compile_programs(repo, case_root, source)
    base_metadata = {
        "schema_version": 1,
        "case": name,
        "mapping": "EAP Patterns inside_com3b pressure-weighted internal "
        "faces and faceval low/high boundaries",
        "source_revision": "85211296c2358c4efef876ddcf67827ef613231d",
        "source_file": "src/derivatives_common_template.f90",
        "source_sha256": "ea3a163c6954627de0dd9732f947a94b"
        "f3959b4394e6b5579424929a086a717c",
        "faces": dimensions["faces"],
        "cells": dimensions["cells"],
        "counts": counts,
        "descriptor_opcode": 4,
        "descriptor_flags": 6,
        "exact_output_words_checked": 4 * dimensions["cells"],
        "exact_completion_words_checked": 4,
        "data_vaddr": DATA_VADDR,
        "data_paddr": DATA_PADDR,
        "data_bytes": DATA_BYTES,
        "control_vaddr": CONTROL_VADDR,
        "control_paddr": CONTROL_PADDR,
        "control_bytes": CONTROL_BYTES,
        "descriptor_paddr": DATA_PADDR + DESCRIPTOR_OFFSET,
        "program_source_sha256": file_sha256(source),
        "gem5_binary_sha256": file_sha256(args.gem5.resolve()),
        "config_source_sha256": file_sha256(args.config.resolve()),
        "roi": {
            "begin": "after initialization and output reset",
            "end": "after scalar kernel or descriptor completion poll",
            "post_roi_exact_oracle": True,
            "warm_cache": True,
        },
        "claim_boundary": "generated scalar microbenchmark comparison; "
        "not native EAP/FLAG correctness, performance, or promotion evidence",
    }
    variants = {}
    for variant in ("scalar_cpu", "maa_descriptor"):
        variants[variant] = run_variant(
            args,
            case_root,
            variant,
            binaries[variant],
            base_metadata,
        )
    scalar = variants["scalar_cpu"]["metrics"]
    maa = variants["maa_descriptor"]["metrics"]
    return {
        "faces": dimensions["faces"],
        "cells": dimensions["cells"],
        "counts": counts,
        "program_source_sha256": file_sha256(source),
        "variants": variants,
        "ratios": {
            "scalar_over_maa_roi_ticks": scalar["roi_ticks"]
            / maa["roi_ticks"],
            "scalar_over_maa_cpu_cycles": scalar["cpu_cycles"]
            / maa["cpu_cycles"],
            "scalar_over_maa_cpu_instructions": scalar["cpu_instructions"]
            / maa["cpu_instructions"],
        },
        "maa_faster_by_roi_ticks": maa["roi_ticks"] < scalar["roi_ticks"],
    }


def classify(cases):
    anchor = cases["representative_anchor"]["maa_faster_by_roi_ticks"]
    lightweight = cases["lightweight_equivalence"]["maa_faster_by_roi_ticks"]
    if anchor != lightweight:
        return "inconclusive_size_sensitivity"
    if anchor:
        return "positive_bounded_microbenchmark_screen"
    return "negative_bounded_microbenchmark_screen"


def run_comparison(args, root):
    repo = pathlib.Path(__file__).resolve().parents[2]
    report = {
        "schema_version": 1,
        "status": "complete",
        "claim": "bounded generated EAP-derived scalar-vs-MAA ROI screen",
        "promotion_ready": False,
        "application_speedup_claim_allowed": False,
        "gem5_binary": str(args.gem5.resolve()),
        "gem5_binary_sha256": file_sha256(args.gem5.resolve()),
        "config": str(args.config.resolve()),
        "config_source_sha256": file_sha256(args.config.resolve()),
        "cases": {},
    }
    for name, dimensions in CASES.items():
        report["cases"][name] = run_case(args, repo, root, name, dimensions)
    report["classification"] = classify(report["cases"])
    report["limitations"] = [
        "single deterministic run per variant and size",
        "generated scalar comparator rather than optimized native EAP/FLAG",
        "warm-cache compact SE records rather than native application ABI",
        "no application, scalability, variance, area, power, or energy claim",
    ]
    report_path = root / "comparison.json"
    report_path.write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name("eap_face_roi_compare.py"),
        type=pathlib.Path,
    )
    parser.add_argument("--outdir", type=pathlib.Path)
    args = parser.parse_args()
    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        report = run_comparison(args, root)
    else:
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-eap-face-roi-"
        ) as temporary:
            report = run_comparison(args, pathlib.Path(temporary))
    anchor_ratio = report["cases"]["representative_anchor"]["ratios"][
        "scalar_over_maa_roi_ticks"
    ]
    print(
        "LANLMAA EAP face ROI comparison: PASS; "
        f"classification={report['classification']}; "
        f"anchor_scalar_over_maa={anchor_ratio:.6f}"
    )


if __name__ == "__main__":
    main()
