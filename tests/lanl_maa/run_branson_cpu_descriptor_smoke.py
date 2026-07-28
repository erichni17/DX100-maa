#!/usr/bin/env python3

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import tempfile

from run_branson_descriptor_staging_smoke import (
    build_staging,
    check_equal,
    read_stats,
)

DATA_VADDR = 0x1000000000
DATA_PADDR = 0x02000000
DATA_BYTES = 0x100000
CONTROL_VADDR = 0x1000200000
CONTROL_PADDR = 0x04000000
CONTROL_BYTES = 0x1000


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_scalar(path, name):
    prefix = name + " "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return int(line.split()[1])
    return None


def validate(stats, metadata, committed_instructions, coherence_stats=None):
    errors = []
    accelerator = stats["lanl_maa"]
    items = metadata["descriptor_items"]
    visits = metadata["executed_record_visits"]
    expected = {
        "logicalItems": items,
        "logicalMemoryAccesses": visits,
        "responsesFannedOut": visits,
        "completionsRetired": items,
        "verificationFailures": 0,
        "continuationSteps": visits,
        "continuationExhaustions": 0,
        "descriptorDoorbells": 1,
        "descriptorBusyRejections": 0,
        "descriptorRearms": 0,
        "descriptorFetches": 1,
        "descriptorAddressLineReads": 1,
        "descriptorAddressesLoaded": items,
        "descriptorResultWrites": items,
        "descriptorCompletionWrites": 1,
        "descriptorErrors": 0,
    }
    for name, value in expected.items():
        check_equal(errors, accelerator, name, value)

    physical = accelerator.get("physicalLineReads")
    merges = accelerator.get("lineMergeHits")
    if physical is None or merges is None or physical + merges != visits:
        errors.append(
            "record accounting mismatch: "
            f"physical={physical}, merges={merges}, visits={visits}"
        )
    if accelerator.get("responses") != physical:
        errors.append("record request/response accounting did not close")
    active_contexts = accelerator.get("activeContextHighWaterMark")
    if active_contexts is None or not 0 < active_contexts <= 4:
        errors.append(
            f"invalid active context high-water mark {active_contexts}"
        )

    failures = accelerator.get("portSendFailures")
    notifications = accelerator.get("portRetryNotifications")
    resubmissions = accelerator.get("retryPacketResubmissions")
    acceptances = accelerator.get("retryPacketAcceptances")
    if failures != notifications or notifications != resubmissions:
        errors.append(
            "retry obligation mismatch: "
            f"failures={failures}, notifications={notifications}, "
            f"resubmissions={resubmissions}"
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
    if committed_instructions is None or committed_instructions <= 0:
        errors.append("CPU retired no instructions")

    if coherence_stats is not None:
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
            "LANLMAA Branson CPU descriptor smoke failed:\n  "
            + "\n  ".join(errors)
        )


def generate_source(path, metadata):
    items = metadata["descriptor_items"]
    records = metadata["record_count"]
    if len(metadata["start_indices"]) != items:
        raise RuntimeError("Branson start-index count does not close")
    if len(metadata["expected_results"]) != items:
        raise RuntimeError("Branson expected-result count does not close")
    if len(metadata["record_next"]) != records:
        raise RuntimeError("Branson record-next count does not close")
    if len(metadata["record_payload"]) != records:
        raise RuntimeError("Branson record-payload count does not close")

    starts = ",\n".join(
        f"    UINT64_C({value})" for value in metadata["start_indices"]
    )
    expected = ",\n".join(
        f"    UINT64_C({value})" for value in metadata["expected_results"]
    )
    record_next = ",\n".join(
        f"    UINT64_C({value})" for value in metadata["record_next"]
    )
    record_payload = ",\n".join(
        f"    UINT64_C({value})" for value in metadata["record_payload"]
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

static const uint64_t start_indices[ITEMS] = {{
{starts}
}};

static const uint64_t expected_results[ITEMS] = {{
{expected}
}};

static const uint64_t record_next[RECORDS] = {{
{record_next}
}};

static const uint64_t record_payload[RECORDS] = {{
{record_payload}
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
    volatile uint64_t *record_words = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + RECORD_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)(
        CONTROL_VADDR);

    for (uint64_t index = 0; index < RECORDS; ++index) {{
        record_words[index * 2] = record_next[index];
        record_words[index * 2 + 1] = record_payload[index];
    }}
    for (uint64_t item = 0; item < ITEMS; ++item) {{
        starts[item] = start_indices[item];
        results[item] = 0;
    }}
    for (uint64_t word = 0; word < 4; ++word) {{
        completion[word] = 0;
    }}
    descriptor[0] = UINT64_C(0x0002000131414d4c);
    descriptor[1] = ITEMS;
    descriptor[2] = DATA_PADDR + START_OFFSET;
    descriptor[3] = DATA_PADDR + RESULT_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = DATA_PADDR + RECORD_OFFSET;
    descriptor[6] = RECORDS | (MAXIMUM_STEPS << 32);
    descriptor[7] = UINT64_MAX;
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
    if (completion[0] != UINT64_C(0x0002000143414d4c) ||
        completion[1] != 0 || completion[2] != ITEMS ||
        completion[3] != ITEMS) {{
        finish(UINT64_C(41));
    }}
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def build_program(root, metadata):
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("Branson CPU descriptor smoke requires cc")
    source = root / "branson_cpu_descriptor.c"
    binary = root / "branson_cpu_descriptor.elf"
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
        root, args.descriptor_items
    )
    source, binary = build_program(root, staging_metadata)
    metadata = dict(staging_metadata)
    metadata.update(
        {
            "cpu_mapping": "real-X86 cache-coherent Branson-derived "
            "indexed-cell-walk descriptor",
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
    metadata_path = root / "branson_cpu_descriptor_metadata.json"
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
            "gem5 Branson CPU descriptor failed:\n"
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
        metadata,
        read_scalar(stats_path, "system.cpu.commitStats0.numInsts"),
        coherence_stats,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--descriptor-items", default=8, type=int)
    parser.add_argument("--l1-caches", action="store_true")
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

    if args.outdir:
        root = args.outdir.resolve()
        if root.exists():
            raise RuntimeError(f"refusing to reuse evidence directory: {root}")
        root.mkdir(parents=True)
        run_smoke(args, root)
    else:
        with tempfile.TemporaryDirectory(
            prefix="lanl-maa-branson-cpu-descriptor-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    mode = " with L1 caches" if args.l1_caches else ""
    print(f"LANLMAA Branson CPU descriptor smoke{mode}: PASS")


if __name__ == "__main__":
    main()
