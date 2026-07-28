#!/usr/bin/env python3

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import tempfile

from run_xrage_descriptor_rearm_smoke import (
    DESCRIPTOR_ITEMS,
    TRACE_SHA256,
    check_equal,
    file_sha256,
    read_stats,
    read_trace,
)

DATA_VADDR = 0x1000000000
DATA_PADDR = 0x02000000
DATA_BYTES = 0x200000
CONTROL_VADDR = 0x1000200000
CONTROL_PADDR = 0x04000000
CONTROL_BYTES = 0x1000
DESCRIPTOR_OFFSET = 0x0000
ADDRESS_VECTOR_OFFSET = 0x1000
RESULT_VECTOR_OFFSET = 0x2000
COMPLETION_OFFSET = 0x3000
TARGET_OFFSET = 0x4000
WINDOW_SHA256 = (
    "6929711f4f49fbbde674fa80d5b8f5cd"
    "05f2140b75f1e747ea7467995cb1aa7b"
)


def read_scalar(path, name):
    prefix = name + " "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return int(line.split()[1])
    return None


def validate(stats, committed_instructions):
    errors = []
    accelerator = stats.get("lanl_maa", {})
    expected = {
        "logicalItems": 64,
        "logicalMemoryAccesses": 64,
        "physicalLineReads": 10,
        "lineMergeHits": 54,
        "lineWouldBlockCycles": 0,
        "responses": 10,
        "responsesFannedOut": 64,
        "completionsRetired": 64,
        "verificationFailures": 0,
        "descriptorDoorbells": 1,
        "descriptorBusyRejections": 0,
        "descriptorRearms": 0,
        "descriptorFetches": 1,
        "descriptorAddressLineReads": 8,
        "descriptorAddressesLoaded": 64,
        "descriptorResultWrites": 64,
        "descriptorCompletionWrites": 1,
        "descriptorErrors": 0,
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)
    if accelerator.get("physicalLineReads", 0) + accelerator.get(
        "lineMergeHits", 0
    ) != accelerator.get("logicalMemoryAccesses"):
        errors.append("CPU descriptor line accounting did not close")
    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    if failures != notifications or notifications != resubmissions:
        errors.append(
            "accelerator retry obligations differ: "
            f"failures={failures}, notifications={notifications}, "
            f"resubmissions={resubmissions}"
        )
    if committed_instructions is None or committed_instructions <= 0:
        errors.append("CPU retired no instructions")
    if errors:
        raise RuntimeError(
            "XRAGE CPU descriptor smoke failed:\n  " + "\n  ".join(errors)
        )


def generate_source(path, indices):
    minimum = min(indices)
    if minimum % 8 != 0:
        raise RuntimeError("XRAGE CPU window minimum is not line aligned")
    maximum_target_offset = TARGET_OFFSET + (max(indices) - minimum) * 8
    if maximum_target_offset + 8 > DATA_BYTES:
        raise RuntimeError("XRAGE CPU target window exceeds mapped data")
    index_lines = ",\n".join(f"    UINT64_C({index})" for index in indices)
    source = f"""
#include <stdint.h>

#define DATA_VADDR UINT64_C(0x{DATA_VADDR:x})
#define DATA_PADDR UINT64_C(0x{DATA_PADDR:x})
#define CONTROL_VADDR UINT64_C(0x{CONTROL_VADDR:x})
#define DESCRIPTOR_OFFSET UINT64_C(0x{DESCRIPTOR_OFFSET:x})
#define ADDRESS_VECTOR_OFFSET UINT64_C(0x{ADDRESS_VECTOR_OFFSET:x})
#define RESULT_VECTOR_OFFSET UINT64_C(0x{RESULT_VECTOR_OFFSET:x})
#define COMPLETION_OFFSET UINT64_C(0x{COMPLETION_OFFSET:x})
#define TARGET_OFFSET UINT64_C(0x{TARGET_OFFSET:x})
#define MINIMUM_INDEX UINT64_C({minimum})
#define ITEMS UINT64_C({DESCRIPTOR_ITEMS})

static const uint64_t indices[ITEMS] = {{
{index_lines}
}};

static uint64_t
splitmix64(uint64_t value)
{{
    value += UINT64_C(0x9e3779b97f4a7c15);
    value = (value ^ (value >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    value = (value ^ (value >> 27)) * UINT64_C(0x94d049bb133111eb);
    return value ^ (value >> 31);
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
    volatile uint64_t *addresses = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + ADDRESS_VECTOR_OFFSET);
    volatile uint64_t *results = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + RESULT_VECTOR_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *targets = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + TARGET_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);

    for (uint64_t item = 0; item < ITEMS; ++item) {{
        const uint64_t delta = indices[item] - MINIMUM_INDEX;
        targets[delta] = splitmix64(indices[item]);
        addresses[item] = DATA_PADDR + TARGET_OFFSET + delta * 8;
        results[item] = 0;
    }}
    for (uint64_t word = 0; word < 4; ++word) {{
        completion[word] = 0;
    }}
    descriptor[0] = UINT64_C(0x0001000131414d4c);
    descriptor[1] = ITEMS;
    descriptor[2] = DATA_PADDR + ADDRESS_VECTOR_OFFSET;
    descriptor[3] = DATA_PADDR + RESULT_VECTOR_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = 0;
    descriptor[6] = 0;
    descriptor[7] = 0;
    fence();

    control[0] = 0;
    fence();
    uint64_t status = 0;
    for (uint64_t spin = 0; spin < UINT64_C(1000000); ++spin) {{
        status = control[UINT64_C(0x110) / 8];
        if (status == UINT64_C(4)) {{
            break;
        }}
        if (status == UINT64_C(8)) {{
            finish(UINT64_C(20) + control[UINT64_C(0x120) / 8]);
        }}
        if (status != UINT64_C(1) && status != UINT64_C(2)) {{
            finish(UINT64_C(12));
        }}
    }}
    if (status != UINT64_C(4)) {{
        finish(UINT64_C(13));
    }}
    if (control[UINT64_C(0x118) / 8] != 0) {{
        finish(UINT64_C(14));
    }}
    fence();

    for (uint64_t item = 0; item < ITEMS; ++item) {{
        if (results[item] != splitmix64(indices[item])) {{
            finish(UINT64_C(40));
        }}
    }}
    if (completion[0] != UINT64_C(0x0001000143414d4c) ||
        completion[1] != 0 || completion[2] != ITEMS ||
        completion[3] != ITEMS) {{
        finish(UINT64_C(41));
    }}
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def build_program(root, indices):
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("XRAGE CPU descriptor smoke requires cc")
    source = root / "xrage_cpu_descriptor.c"
    binary = root / "xrage_cpu_descriptor.elf"
    generate_source(source, indices)
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


def run_smoke(args, root):
    trace = args.trace.resolve()
    if file_sha256(trace) != TRACE_SHA256:
        raise RuntimeError("XRAGE CPU trace SHA-256 changed")
    pattern = read_trace(trace)
    indices = pattern[:DESCRIPTOR_ITEMS]
    packed = struct.pack("<64Q", *indices)
    if hashlib.sha256(packed).hexdigest() != WINDOW_SHA256:
        raise RuntimeError("XRAGE CPU descriptor window changed")
    source, binary = build_program(root, indices)
    metadata = {
        "schema_version": 1,
        "mapping": "CPU-submitted direct gather over pinned XRAGE head window",
        "trace_path": str(trace),
        "trace_sha256": TRACE_SHA256,
        "window_u64le_sha256": WINDOW_SHA256,
        "items": DESCRIPTOR_ITEMS,
        "unique_target_lines": len({index // 8 for index in indices}),
        "minimum_index": min(indices),
        "maximum_index": max(indices),
        "data_vaddr": DATA_VADDR,
        "data_paddr": DATA_PADDR,
        "data_bytes": DATA_BYTES,
        "control_vaddr": CONTROL_VADDR,
        "control_paddr": CONTROL_PADDR,
        "control_bytes": CONTROL_BYTES,
        "descriptor_paddr": DATA_PADDR + DESCRIPTOR_OFFSET,
        "source_sha256": file_sha256(source),
        "binary_sha256": file_sha256(binary),
        "value_oracle": "SplitMix64(index), modulo 2^64",
    }
    metadata_path = root / "metadata.json"
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
    result = subprocess.run(command, text=True, capture_output=True)
    (root / "gem5.stdout").write_text(result.stdout, encoding="utf-8")
    (root / "gem5.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            "gem5 XRAGE CPU descriptor failed:\n"
            + result.stdout
            + result.stderr
        )
    stats_path = outdir / "stats.txt"
    validate(
        read_stats(stats_path),
        read_scalar(stats_path, "system.cpu.commitStats0.numInsts"),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--trace", required=True, type=pathlib.Path)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "xrage_cpu_descriptor_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument("--outdir", type=pathlib.Path)
    args = parser.parse_args()

    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run_smoke(args, root)
    else:
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-xrage-cpu-descriptor-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    print("LANLMAA XRAGE CPU descriptor smoke: PASS")


if __name__ == "__main__":
    main()
