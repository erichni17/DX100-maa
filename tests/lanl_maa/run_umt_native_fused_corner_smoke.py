#!/usr/bin/env python3
"""Run native-linked UMT three-face corner batches through fused LANL-MAA."""

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import tempfile

import run_umt_native_flux_gather_smoke as gather

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNNER = pathlib.Path(__file__).resolve()
SUPPORT_RUNNER = pathlib.Path(gather.__file__).resolve()
UPSTREAM_REVISION = gather.UPSTREAM_REVISION
DATA_VADDR = 0x1000000000
DATA_PADDR = 0x02000000
DATA_BYTES = 0x00100000
CONTROL_VADDR = DATA_VADDR + DATA_BYTES
CONTROL_PADDR = 0x04000000
CONTROL_BYTES = 0x1000
RECORD_OFFSET = 0x1000
RESULT_OFFSET = 0x2000
COMPLETION_OFFSET = 0x3000
SUBMISSION_STRIDE = 0x4000
RECORD_WORDS = 12
RECORD_BYTES = 96
ABI_FINGERPRINT = 0x3B7345C85F10A927


def bits(token):
    value = int(token, 16)
    if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"invalid FP64 word: {token}")
    return value


def parse_record(path):
    identity = gather.parse_record(path)
    scalars = identity["scalars"]
    corners = {}
    faces = {}
    total_source = {}
    old_psi = {}
    cross_section = {}
    expected = {}
    tau = None
    native_group_count = None
    for line in path.read_text(encoding="utf-8").splitlines()[1:-1]:
        fields = line.split()
        if fields[0] == "tau_bits":
            tau = bits(fields[1])
        elif fields[0] == "native_group_count":
            native_group_count = int(fields[1])
        elif fields[0] == "corner":
            corners[int(fields[1])] = {
                "face_offset": int(fields[3]),
                "face_count": int(fields[4]),
                "volume": bits(fields[5]),
                "norm_sum": bits(fields[6]),
            }
        elif fields[0] == "face":
            faces[int(fields[1])] = {
                "flux_point": int(fields[2]),
                "ez_corner": int(fields[3]),
                "fp_norm": bits(fields[4]),
                "ez_norm": bits(fields[5]),
            }
        elif fields[0] == "total_source":
            total_source[(int(fields[1]), int(fields[2]))] = bits(fields[3])
        elif fields[0] == "old_psi":
            old_psi[(int(fields[1]), int(fields[2]))] = bits(fields[3])
        elif fields[0] == "cross_section":
            cross_section[(int(fields[1]), int(fields[2]))] = bits(fields[3])
        elif fields[0] == "native_expected":
            expected[int(fields[1])] = bits(fields[2])

    groups = identity["identity"]["groups"]
    if tau is None or native_group_count is None or groups not in (16, 32):
        raise ValueError(
            "UMT fused replay requires a frozen 16/32-group record"
        )
    current = corners.get(0)
    if current is None or current["face_count"] != 3:
        raise ValueError(
            "UMT fused replay requires native corner zero/three faces"
        )
    current_faces = [faces[current["face_offset"] + face] for face in range(3)]
    if any(
        face["fp_norm"] >> 63 == 0 or face["ez_norm"] >> 63 != 0
        for face in current_faces
    ):
        raise ValueError(
            "UMT fused replay requires negative fpNorm/outgoing ezNorm"
        )
    if set(expected) != set(range(groups)):
        raise ValueError("native UMT expected results are incomplete")

    records = []
    for group in range(groups):
        words = [
            total_source[(0, group)],
            old_psi[(0, group)],
            cross_section[(0, group)],
        ]
        for face in current_faces:
            neighbor = face["ez_corner"]
            words.extend(
                [total_source[(neighbor, group)], old_psi[(neighbor, group)]]
            )
        for face in current_faces:
            index = face["flux_point"] * scalars["total_groups"] + group
            words.append(identity["values"][index])
        if len(words) != RECORD_WORDS:
            raise AssertionError("UMT fused record geometry diverged")
        records.extend(words)

    return {
        **identity,
        "groups": groups,
        "native_group_count": native_group_count,
        "tau": tau,
        "volume": current["volume"],
        "norm_sum": current["norm_sum"],
        "fp_norm": [face["fp_norm"] for face in current_faces],
        "ez_norm": [face["ez_norm"] for face in current_faces],
        "records": records,
        "expected": [expected[group] for group in range(groups)],
    }


def parse_issue_wave(path, record):
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "LANL_MAA_UMT_SWEEP_ISSUE_TRACE_V2":
        raise ValueError("native UMT issue trace has the wrong magic")
    events = []
    sweep_ends = []
    names = (
        "sweep",
        "phase",
        "set",
        "groups",
        "angle",
        "zone_sign",
        "zone",
        "corners",
        "ordinal",
        "corner",
        "clock",
    )
    for line in lines[1:]:
        fields = line.split()
        if not fields or fields[0] in {
            "clock_rate",
            "clock_max",
            "buffer_limit",
        }:
            continue
        if fields[0] == "issue" and len(fields) == 12:
            events.append(dict(zip(names, map(int, fields[1:]))))
        elif fields[0] == "sweep_end" and len(fields) == 4:
            sweep_ends.append(tuple(map(int, fields[1:])))
        else:
            raise ValueError(f"invalid native UMT issue trace line: {line}")
    dropped = any(count for _, _, count in sweep_ends)
    if not events or not sweep_ends or dropped:
        raise ValueError("native UMT issue trace is empty or dropped events")
    first = events[0]
    wave_key = tuple(
        first[name]
        for name in ("sweep", "phase", "set", "angle", "zone_sign", "zone")
    )
    wave = []
    for event in events:
        key = tuple(
            event[name]
            for name in (
                "sweep",
                "phase",
                "set",
                "angle",
                "zone_sign",
                "zone",
            )
        )
        if key != wave_key:
            break
        wave.append(event)
    corners = record["scalars"]["corner_count"]
    if (
        len(wave) != corners
        or [event["ordinal"] for event in wave] != list(range(1, corners + 1))
        or sorted(event["corner"] for event in wave)
        != list(range(1, corners + 1))
        or any(event["corners"] != corners for event in wave)
        or any(
            event["groups"] != record["native_group_count"] for event in wave
        )
    ):
        raise ValueError("native UMT first issue wave changed shape")
    return {
        "path": str(path),
        "sha256": gather.file_sha256(path),
        "submissions": len(wave),
        "key": dict(
            zip(
                ("sweep", "phase", "set", "angle", "zone_sign", "zone"),
                wave_key,
            )
        ),
        "native_groups": first["groups"],
        "corner_order": [event["corner"] for event in wave],
    }


def c_array(name, values):
    rows = []
    for begin in range(0, len(values), 4):
        rows.append(
            "    "
            + ", ".join(
                f"UINT64_C(0x{value:016x})"
                for value in values[begin : begin + 4]
            )
            + ","
        )
    return f"static const uint64_t {name}[] = {{\n" + "\n".join(rows) + "\n};"


def generate_source(path, record, cpu_baseline, submissions):
    geometry = [
        record["tau"],
        record["volume"],
        record["norm_sum"],
        *record["fp_norm"],
        *record["ez_norm"],
    ]
    source = f"""
#include <stdint.h>

#define DATA_VADDR UINT64_C(0x{DATA_VADDR:x})
#define DATA_PADDR UINT64_C(0x{DATA_PADDR:x})
#define CONTROL_VADDR UINT64_C(0x{CONTROL_VADDR:x})
#define RECORD_OFFSET UINT64_C(0x{RECORD_OFFSET:x})
#define RESULT_OFFSET UINT64_C(0x{RESULT_OFFSET:x})
#define COMPLETION_OFFSET UINT64_C(0x{COMPLETION_OFFSET:x})
#define SUBMISSION_STRIDE UINT64_C(0x{SUBMISSION_STRIDE:x})
#define GROUPS UINT64_C({record['groups']})
#define SUBMISSIONS UINT64_C({submissions})
#define CPU_BASELINE {1 if cpu_baseline else 0}

{c_array("native_records", record["records"])}
{c_array("native_expected", record["expected"])}
{c_array("native_geometry", geometry)}

#if CPU_BASELINE
static double
from_bits(uint64_t bits)
{{
    union {{ uint64_t bits; double value; }} converted = {{ .bits = bits }};
    return converted.value;
}}

static uint64_t
to_bits(double value)
{{
    union {{ uint64_t bits; double value; }} converted = {{ .value = value }};
    return converted.bits;
}}
#endif

#if !CPU_BASELINE
static void
fence(void)
{{
    __asm__ volatile("mfence" ::: "memory");
}}
#endif

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
    for (uint64_t submission = 0; submission < SUBMISSIONS; ++submission) {{
        volatile uint64_t *records = (volatile uint64_t *)(uintptr_t)(
            DATA_VADDR + RECORD_OFFSET + submission * SUBMISSION_STRIDE);
        volatile uint64_t *results = (volatile uint64_t *)(uintptr_t)(
            DATA_VADDR + RESULT_OFFSET + submission * SUBMISSION_STRIDE);
        for (uint64_t word = 0; word < GROUPS * UINT64_C(12); ++word) {{
            records[word] = native_records[word];
        }}
        for (uint64_t group = 0; group < GROUPS; ++group) {{
            results[group] = 0;
        }}
    }}

#if CPU_BASELINE
    const double tau = from_bits(native_geometry[0]);
    const double volume = from_bits(native_geometry[1]);
    const double norm_sum = from_bits(native_geometry[2]);
    for (uint64_t submission = 0; submission < SUBMISSIONS; ++submission) {{
      volatile uint64_t *records = (volatile uint64_t *)(uintptr_t)(
          DATA_VADDR + RECORD_OFFSET + submission * SUBMISSION_STRIDE);
      volatile uint64_t *results = (volatile uint64_t *)(uintptr_t)(
          DATA_VADDR + RESULT_OFFSET + submission * SUBMISSION_STRIDE);
      for (uint64_t group = 0; group < GROUPS; ++group) {{
        volatile uint64_t *record = records + group * UINT64_C(12);
        const double source =
            from_bits(record[0]) + tau * from_bits(record[1]);
        const double sigma = from_bits(record[2]);
        double ss = volume * source;
        for (uint64_t face = 0; face < UINT64_C(3); ++face) {{
            ss -= from_bits(native_geometry[3 + face]) *
                from_bits(record[9 + face]);
        }}
        for (uint64_t face = 0; face < UINT64_C(3); ++face) {{
            const double aez = from_bits(native_geometry[6 + face]);
            const double neighbor_source = from_bits(record[3 + 2 * face]) +
                tau * from_bits(record[4 + 2 * face]);
            const uint64_t opposite = (face + 1) % UINT64_C(3);
            const double psi_opposite = from_bits(record[9 + opposite]);
            const double sigv = sigma * volume;
            const double sigv2 = sigv * sigv;
            const double aez2 = aez * aez;
            const double gnum = aez2 *
                (1.82 * sigv2 + aez * (4.0 * sigv + 3.0 * aez));
            const double gden = volume *
                (4.0 * sigv * sigv2 +
                 aez * (6.0 * sigv2 +
                        2.0 * aez * (2.0 * sigv + aez)));
            const double sez =
                (volume * gnum * (sigma * psi_opposite - source) +
                 0.5 * aez * gden * (source - neighbor_source)) /
                (gnum + gden * sigma);
            ss += 1.0 * sez;
        }}
        const double value = ss / (norm_sum + sigma * volume);
        results[group] = to_bits(value);
        if (results[group] != native_expected[group]) {{
            finish(UINT64_C(40));
        }}
      }}
    }}
#else
    volatile uint64_t *descriptor =
        (volatile uint64_t *)(uintptr_t)DATA_VADDR;
    volatile uint64_t *control =
        (volatile uint64_t *)(uintptr_t)CONTROL_VADDR;
    descriptor[0] = UINT64_C(0x0109000231414d4c);
    descriptor[1] = (UINT64_C(96) << 32) | GROUPS;
    for (uint64_t word = 0; word < UINT64_C(9); ++word) {{
        descriptor[5 + word] = native_geometry[word];
    }}
    descriptor[14] = UINT64_C(0x{ABI_FINGERPRINT:016x});
    descriptor[15] = 0;
    for (uint64_t submission = 0; submission < SUBMISSIONS; ++submission) {{
        const uint64_t offset = submission * SUBMISSION_STRIDE;
        volatile uint64_t *results = (volatile uint64_t *)(uintptr_t)(
            DATA_VADDR + RESULT_OFFSET + offset);
        volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
            DATA_VADDR + COMPLETION_OFFSET + offset);
        descriptor[2] = DATA_PADDR + RECORD_OFFSET + offset;
        descriptor[3] = DATA_PADDR + RESULT_OFFSET + offset;
        descriptor[4] = DATA_PADDR + COMPLETION_OFFSET + offset;
        for (uint64_t word = 0; word < UINT64_C(4); ++word) {{
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
        fence();
        for (uint64_t group = 0; group < GROUPS; ++group) {{
            if (results[group] != native_expected[group]) {{
                finish(UINT64_C(40));
            }}
        }}
        if (completion[0] != UINT64_C(0x0009000243414d4c) ||
            completion[1] != 0 || completion[2] != GROUPS ||
            completion[3] != GROUPS) {{
            finish(UINT64_C(41));
        }}
    }}
#endif
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def build_program(root, record, cpu_baseline, submissions):
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("UMT fused smoke requires cc")
    source = root / "umt_native_fused_corner.c"
    binary = root / "umt_native_fused_corner.elf"
    generate_source(source, record, cpu_baseline, submissions)
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
            "-ffp-contract=off",
            "-Wall",
            "-Wextra",
            "-Werror",
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


def validate(stats, groups, submissions, payload_model):
    items = groups * submissions
    expected = {
        "logicalItems": items,
        "logicalMemoryAccesses": RECORD_WORDS * items,
        "completionsRetired": items,
        "verificationFailures": 0,
        "descriptorDoorbells": submissions,
        "descriptorRearms": submissions - 1,
        "descriptorFetches": 2 * submissions,
        "descriptorAddressesLoaded": 0,
        "descriptorResultWrites": items,
        "descriptorCompletionWrites": submissions,
        "descriptorErrors": 0,
        "descriptorUmtGroupsLoaded": items,
        "descriptorUmtInputReads": RECORD_WORDS * items,
        "descriptorUmtFp64AddSubOperations": 38 * items,
        "descriptorUmtFp64MultiplyOperations": 59 * items,
        "descriptorUmtFp64DivideOperations": 4 * items,
        "descriptorUmtBatches": submissions,
        "descriptorUmtBatchCycles": submissions
        * (1819 if groups == 16 else 3595),
        "descriptorUmtResultsComputed": items,
    }
    if payload_model:
        expected.update(
            {
                "payloadOverlayCompletionWrites": items,
                "payloadOverlayRetirementReads": items,
            }
        )
    for name, value in expected.items():
        if stats.get(name) != value:
            raise RuntimeError(
                f"UMT fused {name}: expected {value}, got {stats.get(name)}"
            )
    unique_lines = items * RECORD_BYTES // 64
    physical = stats.get("physicalLineReads")
    merges = stats.get("lineMergeHits")
    logical = RECORD_WORDS * items
    if physical is None or physical < unique_lines or physical > logical:
        raise RuntimeError(
            "UMT fused physical reads violate exact-stream bounds"
        )
    if merges is None or physical + merges != logical:
        raise RuntimeError("UMT fused line-read accounting did not close")


def validate_cpu(stats):
    for name in (
        "logicalItems",
        "physicalLineReads",
        "completionsRetired",
        "descriptorDoorbells",
        "descriptorErrors",
        "descriptorUmtGroupsLoaded",
    ):
        if stats.get(name, 0) != 0:
            raise RuntimeError(f"UMT scalar arm unexpectedly exercised {name}")


def run_smoke(args, root):
    commit, clean = gather.repository_identity(ROOT)
    if args.require_clean_simulator and not clean:
        raise RuntimeError("UMT fused evidence requires a clean simulator")
    record = parse_record(args.record.resolve())
    wave = (
        parse_issue_wave(args.issue_trace.resolve(), record)
        if args.issue_trace
        else None
    )
    submissions = wave["submissions"] if wave else 1
    footprint = COMPLETION_OFFSET + (submissions - 1) * SUBMISSION_STRIDE + 32
    if footprint > DATA_BYTES:
        raise RuntimeError("UMT fused submission footprint exceeds data map")
    if args.cpu_baseline and args.model_payload_overlay_ports:
        raise RuntimeError("scalar arm cannot model accelerator payload ports")
    source, binary = build_program(
        root, record, args.cpu_baseline, submissions
    )
    record_bytes = b"".join(
        struct.pack("<Q", word) for word in record["records"]
    )
    expected_bytes = b"".join(
        struct.pack("<Q", word) for word in record["expected"]
    )
    metadata = {
        "schema": "lanl-maa-umt-native-fused-corner-v1",
        "mapping": (
            "Exact native ATS UMT corner-zero, direct-three-face, "
            "outgoing-only group batch"
        ),
        "problem": record["identity"]["problem"],
        "record_path": str(args.record.resolve()),
        "record_sha256": record["record_sha256"],
        "upstream_revision": UPSTREAM_REVISION,
        "groups": record["groups"],
        "submissions": submissions,
        "total_items": record["groups"] * submissions,
        "source_issue_wave": wave,
        "record_words_per_group": RECORD_WORDS,
        "record_stream_u64le_sha256": hashlib.sha256(record_bytes).hexdigest(),
        "expected_stream_u64le_sha256": hashlib.sha256(
            expected_bytes
        ).hexdigest(),
        "items": record["groups"],
        "data_vaddr": DATA_VADDR,
        "data_paddr": DATA_PADDR,
        "data_bytes": DATA_BYTES,
        "control_vaddr": CONTROL_VADDR,
        "control_paddr": CONTROL_PADDR,
        "control_bytes": CONTROL_BYTES,
        "descriptor_paddr": DATA_PADDR,
        "cpu_baseline": args.cpu_baseline,
        "l1_caches": args.l1_caches,
        "model_payload_overlay_ports": args.model_payload_overlay_ports,
        "source_sha256": gather.file_sha256(source),
        "binary_sha256": gather.file_sha256(binary),
        "simulator_commit": commit,
        "simulator_worktree_clean": clean,
        "gem5_sha256": gather.file_sha256(args.gem5.resolve()),
        "runner_sha256": gather.file_sha256(RUNNER),
        "support_runner_sha256": gather.file_sha256(SUPPORT_RUNNER),
        "config_sha256": gather.file_sha256(args.config.resolve()),
    }
    metadata_path = root / "metadata.json"
    gather.write_json_atomic(metadata_path, metadata)
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
    if args.model_payload_overlay_ports:
        command.append("--model-payload-overlay-ports")
    report = {
        "schema": "lanl-maa-umt-native-fused-corner-report-v1",
        "status": "running",
        "terminal": False,
        "problem": record["identity"]["problem"],
        "record_sha256": record["record_sha256"],
        "simulator_commit": commit,
        "simulator_worktree_clean": clean,
        "gem5_sha256": metadata["gem5_sha256"],
        "runner_sha256": metadata["runner_sha256"],
        "config_sha256": metadata["config_sha256"],
        "record_stream_u64le_sha256": metadata["record_stream_u64le_sha256"],
        "expected_stream_u64le_sha256": metadata[
            "expected_stream_u64le_sha256"
        ],
        "binary_sha256": metadata["binary_sha256"],
        "metadata_sha256": gather.file_sha256(metadata_path),
        "groups": record["groups"],
        "submissions": submissions,
        "source_issue_wave": wave,
        "cpu_baseline": args.cpu_baseline,
        "l1_caches": args.l1_caches,
        "model_payload_overlay_ports": args.model_payload_overlay_ports,
        "command": command,
        "correctness_method": (
            "Every result in every submission is compared bit-exactly with "
            "the native UMT captured result; each completion record is exact."
        ),
        "claim_boundary": (
            "The native trace supplies only the eight-submission corner-wave "
            "shape. One captured corner is repeated at disjoint addresses; "
            "this is not eight distinct native corners, the native UMT "
            "process, application speedup, or promotion evidence."
        ),
    }
    report_path = root / "report.json"
    gather.write_json_atomic(report_path, report)
    stdout_path = root / "gem5.stdout"
    stderr_path = root / "gem5.stderr"
    try:
        with stdout_path.open(
            "w", encoding="utf-8"
        ) as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            result = subprocess.run(
                command, text=True, stdout=stdout, stderr=stderr
            )
        report["driver_return_code"] = result.returncode
        if result.returncode != 0:
            raise RuntimeError("gem5 native UMT fused corner failed")
        stats_path = outdir / "stats.txt"
        stats = gather.read_stats(stats_path)
        if args.cpu_baseline:
            validate_cpu(stats)
        else:
            validate(
                stats,
                record["groups"],
                submissions,
                args.model_payload_overlay_ports,
            )
        names = (
            "logicalItems",
            "logicalMemoryAccesses",
            "physicalLineReads",
            "lineMergeHits",
            "lineBankConflictCycles",
            "operationWouldBlockCycles",
            "lineWouldBlockCycles",
            "contextWouldBlockCycles",
            "completionsRetired",
            "descriptorDoorbells",
            "descriptorRearms",
            "descriptorFetches",
            "descriptorCompletionWrites",
            "descriptorErrors",
            "payloadOverlayCompletionWrites",
            "payloadOverlayRetirementReads",
            "descriptorUmtGroupsLoaded",
            "descriptorUmtInputReads",
            "descriptorUmtFp64AddSubOperations",
            "descriptorUmtFp64MultiplyOperations",
            "descriptorUmtFp64DivideOperations",
            "descriptorUmtBatches",
            "descriptorUmtBatchCycles",
            "descriptorUmtResultsComputed",
            "descriptorCycles",
            "engineCycles",
            "verificationFailures",
        )
        report["metrics"] = {name: stats.get(name) for name in names}
        report["metrics"]["simTicks"] = gather.read_scalar(
            stats_path, "simTicks"
        )
        report["metrics"]["cpuCommittedInstructions"] = gather.read_scalar(
            stats_path, "system.cpu.commitStats0.numInsts"
        )
        if args.l1_caches and not args.cpu_baseline:
            report["cache_metrics"] = {
                "accesses": gather.read_scalar(
                    stats_path, "system.maa_cache.overallAccesses_T::total"
                ),
                "misses": gather.read_scalar(
                    stats_path, "system.maa_cache.overallMisses_T::total"
                ),
                "hits": gather.read_scalar(
                    stats_path, "system.maa_cache.overallHits_T::total"
                ),
            }
            cache = report["cache_metrics"]
            if cache["accesses"] != cache["misses"] + cache["hits"]:
                raise RuntimeError(
                    f"UMT fused cache accounting failed: {cache}"
                )
        report["stats_sha256"] = gather.file_sha256(stats_path)
        report["stdout_sha256"] = gather.file_sha256(stdout_path)
        report["stderr_sha256"] = gather.file_sha256(stderr_path)
        report["status"] = "validated"
        report["terminal"] = True
    except Exception as error:
        report["status"] = "failed"
        report["terminal"] = True
        report["error"] = str(error)
        raise
    finally:
        gather.write_json_atomic(report_path, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--record", required=True, type=pathlib.Path)
    parser.add_argument("--issue-trace", type=pathlib.Path)
    parser.add_argument("--l1-caches", action="store_true")
    parser.add_argument("--cpu-baseline", action="store_true")
    parser.add_argument("--model-payload-overlay-ports", action="store_true")
    parser.add_argument("--require-clean-simulator", action="store_true")
    parser.add_argument(
        "--config",
        default=HERE / "umt_fused_corner_descriptor_smoke.py",
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
            prefix="lanl-maa-umt-native-fused-corner-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    print("LANLMAA native UMT fused corner: PASS")


if __name__ == "__main__":
    main()
