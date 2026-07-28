#!/usr/bin/env python3

import argparse
import json
import pathlib
import shutil
import subprocess
import tempfile

from run_branson_cpu_descriptor_smoke import (
    CONTROL_BYTES,
    CONTROL_PADDR,
    CONTROL_VADDR,
    DATA_BYTES,
    DATA_PADDR,
    DATA_VADDR,
    file_sha256,
    read_scalar,
    validate,
)
from run_sparta_compact_descriptor_smoke import build_staging
from run_sparta_descriptor_staging_smoke import read_stats


def generate_source(path, metadata):
    items = metadata["descriptor_items"]
    records = metadata["record_count"]
    submissions = metadata["submissions"]
    if len(metadata["start_states"]) != items:
        raise RuntimeError("SPARTA start-state count does not close")
    if len(metadata["expected_results"]) != items:
        raise RuntimeError("SPARTA expected-result count does not close")
    if len(metadata["record_words"]) != records:
        raise RuntimeError("SPARTA compact-record count does not close")

    starts = ",\n".join(
        f"    UINT64_C({value})" for value in metadata["start_states"]
    )
    expected = ",\n".join(
        f"    UINT64_C({value})" for value in metadata["expected_results"]
    )
    record_words = ",\n".join(
        f"    UINT64_C({value})" for value in metadata["record_words"]
    )
    source = f"""
#include <stdint.h>

#define DATA_VADDR UINT64_C(0x{DATA_VADDR:x})
#define DATA_PADDR UINT64_C(0x{DATA_PADDR:x})
#define CONTROL_VADDR UINT64_C(0x{CONTROL_VADDR:x})
#define DESCRIPTOR_OFFSET UINT64_C(0x{metadata['descriptor_address']:x})
#define START_OFFSET UINT64_C(0x{metadata['start_vector']:x})
#define RESULT_OFFSET UINT64_C(0x{metadata['result_vector']:x})
#define COMPLETION_OFFSET UINT64_C(0x{metadata['completion_record']:x})
#define RECORD_OFFSET UINT64_C(0x{metadata['record_base']:x})
#define ITEMS UINT64_C({items})
#define RECORDS UINT64_C({records})
#define MAXIMUM_STEPS UINT64_C({metadata['maximum_steps']})
#define SUBMISSIONS UINT64_C({submissions})

static const uint64_t start_states[ITEMS] = {{
{starts}
}};

static const uint64_t expected_results[ITEMS] = {{
{expected}
}};

static const uint64_t record_values[RECORDS] = {{
{record_words}
}};

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
    volatile uint64_t *starts = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + START_OFFSET);
    volatile uint64_t *results = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + RESULT_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *records = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + RECORD_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);

    for (uint64_t index = 0; index < RECORDS; ++index) {{
        records[index] = record_values[index];
    }}
    for (uint64_t item = 0; item < ITEMS; ++item) {{
        starts[item] = start_states[item];
    }}
    descriptor[0] = UINT64_C(0x0003000131414d4c);
    descriptor[1] = ITEMS;
    descriptor[2] = DATA_PADDR + START_OFFSET;
    descriptor[3] = DATA_PADDR + RESULT_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = DATA_PADDR + RECORD_OFFSET;
    descriptor[6] = RECORDS | (MAXIMUM_STEPS << 32);
    descriptor[7] = 0;
    fence();

    for (uint64_t submission = 0; submission < SUBMISSIONS; ++submission) {{
        for (uint64_t item = 0; item < ITEMS; ++item) {{
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
            if (results[item] != expected_results[item]) {{
                finish(UINT64_C(40));
            }}
        }}
        if (completion[0] != UINT64_C(0x0003000143414d4c) ||
            completion[1] != 0 || completion[2] != ITEMS ||
            completion[3] != ITEMS) {{
            finish(UINT64_C(41));
        }}
    }}
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def build_program(root, metadata):
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("SPARTA CPU descriptor smoke requires cc")
    source = root / "sparta_cpu_descriptor.c"
    binary = root / "sparta_cpu_descriptor.elf"
    generate_source(source, metadata)
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
    _, staging_metadata_path, staging_metadata = build_staging(
        root,
        particles=args.particles,
        cells=args.cells,
        maximum_visits=args.maximum_visits,
        descriptor_items=args.descriptor_items,
        order=args.order,
    )
    staging_metadata["submissions"] = args.submissions
    source, binary = build_program(root, staging_metadata)
    metadata = dict(staging_metadata)
    metadata.update(
        {
            "cpu_mapping": "real-X86 cache-coherent SPARTA-derived "
            "packed-directional cell-walk descriptor",
            "data_vaddr": DATA_VADDR,
            "data_paddr": DATA_PADDR,
            "data_bytes": DATA_BYTES,
            "control_vaddr": CONTROL_VADDR,
            "control_paddr": CONTROL_PADDR,
            "control_bytes": CONTROL_BYTES,
            "descriptor_paddr": DATA_PADDR
            + staging_metadata["descriptor_address"],
            "staging_metadata_sha256": file_sha256(staging_metadata_path),
            "program_source_sha256": file_sha256(source),
            "program_elf_sha256": file_sha256(binary),
            "l1_caches": args.l1_caches,
            "particle_order": args.order,
            "submissions": args.submissions,
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
        }
    )
    metadata_path = root / "sparta_cpu_descriptor_metadata.json"
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
            "gem5 SPARTA CPU descriptor failed:\n"
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
            "membus_snoops": read_scalar(
                stats_path, "system.membus.snoops"
            ),
            "snoop_filter_single_holder_hits": read_scalar(
                stats_path,
                "system.membus.snoop_filter.hitSingleRequests",
            ),
        }
    validate(
        read_stats(stats_path),
        metadata,
        read_scalar(stats_path, "system.cpu.commitStats0.numInsts"),
        coherence_stats,
        workload="SPARTA",
        submissions=args.submissions,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--l1-caches", action="store_true")
    parser.add_argument("--particles", type=int, default=256)
    parser.add_argument("--cells", type=int, default=64)
    parser.add_argument("--maximum-visits", type=int, default=8)
    parser.add_argument("--descriptor-items", type=int, default=8)
    parser.add_argument("--submissions", type=int, default=1)
    parser.add_argument(
        "--order", choices=("sorted", "shuffled"), default="sorted"
    )
    parser.add_argument("--maa-cache-size", default="4KiB")
    parser.add_argument("--maa-cache-assoc", type=int, default=4)
    parser.add_argument("--maa-cache-mshrs", type=int, default=4)
    parser.add_argument("--maa-cache-targets-per-mshr", type=int, default=4)
    parser.add_argument("--maa-cache-write-buffers", type=int, default=4)
    parser.add_argument(
        "--config",
        default=pathlib.Path(__file__).with_name(
            "branson_cpu_descriptor_smoke.py"
        ),
        type=pathlib.Path,
    )
    parser.add_argument("--outdir", type=pathlib.Path)
    args = parser.parse_args()
    for name in (
        "particles",
        "cells",
        "maximum_visits",
        "descriptor_items",
        "submissions",
    ):
        if getattr(args, name) <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    if args.descriptor_items > 32:
        parser.error(
            "--descriptor-items must be at most 32 to keep the result "
            "vector disjoint from the fixed completion record"
        )
    if args.descriptor_items > args.particles:
        parser.error("--descriptor-items must not exceed --particles")
    if 0xC00 + args.cells * 8 > DATA_BYTES:
        parser.error("packed record arena exceeds the mapped data range")

    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run_smoke(args, root)
    else:
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-sparta-cpu-descriptor-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    mode = " with L1 caches" if args.l1_caches else ""
    print(f"LANLMAA SPARTA CPU descriptor smoke{mode}: PASS")


if __name__ == "__main__":
    main()
