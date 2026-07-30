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
    "6929711f4f49fbbde674fa80d5b8f5cd" "05f2140b75f1e747ea7467995cb1aa7b"
)
MAX_STREAM_DESCRIPTORS = 32


def read_scalar(path, name):
    prefix = name + " "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return int(line.split()[1])
    return None


def validate(
    stats,
    committed_instructions,
    chunks=1,
    minimum_physical_line_reads=10,
    coherence_stats=None,
    expect_reference_coherence=False,
    model_payload_overlay_ports=False,
):
    errors = []
    accelerator = stats.get("lanl_maa", {})
    logical_items = chunks * DESCRIPTOR_ITEMS
    expected = {
        "logicalItems": logical_items,
        "logicalMemoryAccesses": logical_items,
        "lineWouldBlockCycles": 0,
        "responsesFannedOut": logical_items,
        "completionsRetired": logical_items,
        "verificationFailures": 0,
        "descriptorDoorbells": chunks,
        "descriptorBusyRejections": 0,
        "descriptorRearms": chunks - 1,
        "descriptorFetches": chunks,
        "descriptorAddressLineReads": chunks * 8,
        "descriptorAddressesLoaded": logical_items,
        "descriptorResultWrites": logical_items,
        "descriptorCompletionWrites": chunks,
        "descriptorErrors": 0,
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)
    physical_line_reads = accelerator.get("physicalLineReads")
    line_merge_hits = accelerator.get("lineMergeHits")
    if chunks == 1 and physical_line_reads != minimum_physical_line_reads:
        errors.append(
            "single-window physical reads differ: "
            f"expected {minimum_physical_line_reads}, "
            f"got {physical_line_reads}"
        )
    if (
        physical_line_reads is None
        or physical_line_reads < minimum_physical_line_reads
        or physical_line_reads > logical_items
    ):
        errors.append(
            "physical reads violate unique-line lower bound: "
            f"minimum={minimum_physical_line_reads}, "
            f"logical={logical_items}, actual={physical_line_reads}"
        )
    if (
        physical_line_reads is None
        or line_merge_hits is None
        or physical_line_reads + line_merge_hits
        != accelerator.get("logicalMemoryAccesses")
    ):
        errors.append("CPU descriptor line accounting did not close")
    if accelerator.get("responses") != physical_line_reads:
        errors.append(
            "CPU descriptor request/response accounting did not close"
        )
    if model_payload_overlay_ports:
        for name in (
            "payloadOverlayCompletionWrites",
            "payloadOverlayRetirementReads",
        ):
            if accelerator.get(name) != logical_items:
                errors.append(
                    f"{name}: expected {logical_items}, "
                    f"got {accelerator.get(name)}"
                )
        if (
            accelerator.get(
                "payloadOverlayCompletionQueueHighWaterMark", 0
            )
            <= 0
        ):
            errors.append("payload overlay completion queue was not exercised")
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
    if expect_reference_coherence:
        expected_coherence = {
            "maa_cache_accesses": 84,
            "maa_cache_misses": 28,
            "maa_cache_read_accesses": 19,
            "maa_cache_write_accesses": 65,
            "membus_snoops": 37,
            "snoop_filter_single_holder_hits": 37,
        }
        for name, expected_value in expected_coherence.items():
            actual = coherence_stats.get(name)
            if actual != expected_value:
                errors.append(
                    f"{name}: expected {expected_value}, got {actual}"
                )
    elif coherence_stats is not None:
        if coherence_stats["maa_cache_accesses"] != (
            coherence_stats["maa_cache_read_accesses"]
            + coherence_stats["maa_cache_write_accesses"]
        ):
            errors.append("MAA cache read/write accesses do not close")
        for name in (
            "maa_cache_accesses",
            "maa_cache_misses",
            "membus_snoops",
            "snoop_filter_single_holder_hits",
        ):
            if coherence_stats[name] is None or coherence_stats[name] <= 0:
                errors.append(f"{name} did not exercise coherence")
    if errors:
        raise RuntimeError(
            "XRAGE CPU descriptor smoke failed:\n  " + "\n  ".join(errors)
        )


def generate_source(path, indices, chunks):
    if len(indices) != chunks * DESCRIPTOR_ITEMS:
        raise RuntimeError("XRAGE CPU stream item count does not close")
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
#define CHUNKS UINT64_C({chunks})
#define TOTAL_ITEMS (ITEMS * CHUNKS)

static const uint64_t indices[TOTAL_ITEMS] = {{
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

    for (uint64_t item = 0; item < TOTAL_ITEMS; ++item) {{
        const uint64_t delta = indices[item] - MINIMUM_INDEX;
        targets[delta] = splitmix64(indices[item]);
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

    for (uint64_t chunk = 0; chunk < CHUNKS; ++chunk) {{
        for (uint64_t item = 0; item < ITEMS; ++item) {{
            const uint64_t index = indices[chunk * ITEMS + item];
            const uint64_t delta = index - MINIMUM_INDEX;
            addresses[item] = DATA_PADDR + TARGET_OFFSET + delta * 8;
            results[item] = 0;
        }}
        for (uint64_t word = 0; word < 4; ++word) {{
            completion[word] = 0;
        }}
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
            const uint64_t index = indices[chunk * ITEMS + item];
            if (results[item] != splitmix64(index)) {{
                finish(UINT64_C(40));
            }}
        }}
        if (completion[0] != UINT64_C(0x0001000143414d4c) ||
            completion[1] != 0 || completion[2] != ITEMS ||
            completion[3] != ITEMS) {{
            finish(UINT64_C(41));
        }}
    }}
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def build_program(root, indices, chunks):
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("XRAGE CPU descriptor smoke requires cc")
    source = root / "xrage_cpu_descriptor.c"
    binary = root / "xrage_cpu_descriptor.elf"
    generate_source(source, indices, chunks)
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
    if args.chunks < 1 or args.chunks > MAX_STREAM_DESCRIPTORS:
        raise RuntimeError(f"chunks must be in [1, {MAX_STREAM_DESCRIPTORS}]")
    total_items = args.chunks * DESCRIPTOR_ITEMS
    indices = pattern[:total_items]
    if len(indices) != total_items:
        raise RuntimeError("XRAGE trace ended before the requested CPU stream")
    packed = struct.pack(f"<{total_items}Q", *indices)
    stream_sha256 = hashlib.sha256(packed).hexdigest()
    if args.chunks == 1 and stream_sha256 != WINDOW_SHA256:
        raise RuntimeError("XRAGE CPU descriptor window changed")
    windows = []
    for chunk in range(args.chunks):
        begin = chunk * DESCRIPTOR_ITEMS
        window = indices[begin : begin + DESCRIPTOR_ITEMS]
        window_packed = struct.pack("<64Q", *window)
        windows.append(
            {
                "chunk": chunk,
                "trace_offset": begin,
                "window_u64le_sha256": hashlib.sha256(
                    window_packed
                ).hexdigest(),
                "unique_target_lines": len({index // 8 for index in window}),
            }
        )
    source, binary = build_program(root, indices, args.chunks)
    minimum_physical_line_reads = sum(
        window["unique_target_lines"] for window in windows
    )
    stream_unique_target_lines = len({index // 8 for index in indices})
    metadata = {
        "schema_version": 1,
        "mapping": (
            "CPU-submitted direct gather over pinned XRAGE head window"
            if args.chunks == 1
            else "CPU-submitted status-driven direct-gather stream over "
            "pinned XRAGE head windows"
        ),
        "trace_path": str(trace),
        "trace_sha256": TRACE_SHA256,
        "stream_u64le_sha256": stream_sha256,
        "windows": windows,
        "chunks": args.chunks,
        "items": DESCRIPTOR_ITEMS,
        "total_items": total_items,
        "sum_window_unique_target_lines": minimum_physical_line_reads,
        "stream_unique_target_lines": stream_unique_target_lines,
        "cross_descriptor_reused_target_lines": (
            minimum_physical_line_reads - stream_unique_target_lines
        ),
        "minimum_index": min(indices),
        "maximum_index": max(indices),
        "data_vaddr": DATA_VADDR,
        "data_paddr": DATA_PADDR,
        "data_bytes": DATA_BYTES,
        "control_vaddr": CONTROL_VADDR,
        "control_paddr": CONTROL_PADDR,
        "control_bytes": CONTROL_BYTES,
        "descriptor_paddr": DATA_PADDR + DESCRIPTOR_OFFSET,
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
        "source_sha256": file_sha256(source),
        "binary_sha256": file_sha256(binary),
        "value_oracle": "SplitMix64(index), modulo 2^64",
    }
    if args.chunks == 1:
        metadata["window_u64le_sha256"] = stream_sha256
        metadata["unique_target_lines"] = windows[0]["unique_target_lines"]
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
    if args.model_payload_overlay_ports:
        command.append("--model-payload-overlay-ports")
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
    coherence_stats = None
    if args.l1_caches:
        coherence_stats = {
            "maa_cache_accesses": read_scalar(
                stats_path, "system.maa_cache.overallAccesses_T::total"
            ),
            "maa_cache_misses": read_scalar(
                stats_path, "system.maa_cache.overallMisses_T::total"
            ),
            "maa_cache_read_accesses": read_scalar(
                stats_path, "system.maa_cache.ReadReq_T.accesses::total"
            ),
            "maa_cache_write_accesses": read_scalar(
                stats_path, "system.maa_cache.WriteReq_T.accesses::total"
            ),
            "membus_snoops": read_scalar(stats_path, "system.membus.snoops"),
            "snoop_filter_single_holder_hits": read_scalar(
                stats_path,
                "system.membus.snoop_filter.hitSingleRequests",
            ),
        }
    validate(
        read_stats(stats_path),
        read_scalar(stats_path, "system.cpu.commitStats0.numInsts"),
        args.chunks,
        minimum_physical_line_reads,
        coherence_stats,
        args.chunks == 1
        and args.l1_caches
        and args.maa_cache_size == "2KiB"
        and args.maa_cache_assoc == 4
        and args.maa_cache_mshrs == 32
        and args.maa_cache_targets_per_mshr == 20
        and args.maa_cache_write_buffers == 8,
        args.model_payload_overlay_ports,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--trace", required=True, type=pathlib.Path)
    parser.add_argument("--chunks", default=1, type=int)
    parser.add_argument("--l1-caches", action="store_true")
    parser.add_argument("--model-payload-overlay-ports", action="store_true")
    parser.add_argument("--maa-cache-size", default="4KiB")
    parser.add_argument("--maa-cache-assoc", type=int, default=2)
    parser.add_argument("--maa-cache-mshrs", type=int, default=8)
    parser.add_argument("--maa-cache-targets-per-mshr", type=int, default=2)
    parser.add_argument("--maa-cache-write-buffers", type=int, default=2)
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
    mode = " with L1 caches" if args.l1_caches else ""
    print(
        f"LANLMAA XRAGE CPU descriptor smoke{mode}, "
        f"chunks={args.chunks}: PASS"
    )


if __name__ == "__main__":
    main()
