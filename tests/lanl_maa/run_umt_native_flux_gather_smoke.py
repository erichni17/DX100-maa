#!/usr/bin/env python3
"""Replay native UMT flux reads through live LANL-MAA direct gather."""

import argparse
import hashlib
import json
import pathlib
import shutil
import struct
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RUNNER = pathlib.Path(__file__).resolve()
UPSTREAM_REVISION = "5fd8c132560b5debbe06bdaf0bbd70ce5fcb4979"
KNOWN_RECORDS = {
    "768bd36850af5e47f67272bc86e48c5ffa481bf318a6def673276e095abdfaac": {
        "problem": "SPP1",
        "groups": 32,
        "logical_reads": 192,
        "unique_lines": 12,
    },
    "fedbc90e3f07b211b88e62f8967d1b6748799da5ab3111b64061fced3a2bb32f": {
        "problem": "SPP2",
        "groups": 16,
        "logical_reads": 96,
        "unique_lines": 6,
    },
}
DESCRIPTOR_ITEMS = 64
DATA_VADDR = 0x1000000000
DATA_PADDR = 0x02000000
DATA_BYTES = 0x00100000
CONTROL_VADDR = DATA_VADDR + DATA_BYTES
CONTROL_PADDR = 0x04000000
CONTROL_BYTES = 0x1000
DESCRIPTOR_OFFSET = 0x0000
ADDRESS_VECTOR_OFFSET = 0x1000
RESULT_VECTOR_OFFSET = 0x2000
COMPLETION_OFFSET = 0x3000
TARGET_OFFSET = 0x4000


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path, document):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def repository_identity(root):
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    status = subprocess.check_output(
        ["git", "status", "--short"], cwd=root, text=True
    )
    return commit, not bool(status.strip())


def parse_hex64(token, name):
    try:
        value = int(token, 16)
    except ValueError as error:
        raise ValueError(f"invalid {name} FP64 word: {token}") from error
    if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"out-of-range {name} FP64 word: {token}")
    return value


def parse_record(path):
    record_sha256 = file_sha256(path)
    identity = KNOWN_RECORDS.get(record_sha256)
    if identity is None:
        raise ValueError(f"unrecognized native UMT record: {record_sha256}")

    scalars = {}
    corner_order = []
    corners = {}
    faces = {}
    psi1 = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "LANL_MAA_UMT_SWEEP_V1":
        raise ValueError("native UMT record has the wrong magic")
    if lines[-1] != "END_LANL_MAA_UMT_SWEEP_V1":
        raise ValueError("native UMT record has no terminal marker")

    scalar_names = {
        "corner_count",
        "zone_count",
        "flux_point_count",
        "total_groups",
        "selected_corner_count",
        "first_group",
        "group_count",
        "face_record_count",
    }
    for line in lines[1:-1]:
        fields = line.split()
        if fields[0] == "upstream_revision":
            if len(fields) != 2 or fields[1] != UPSTREAM_REVISION:
                raise ValueError("native UMT revision changed")
        elif fields[0] in scalar_names:
            if len(fields) != 2 or fields[0] in scalars:
                raise ValueError(f"invalid scalar record: {line}")
            scalars[fields[0]] = int(fields[1])
        elif fields[0] == "corner_order":
            corner_order = [int(value) for value in fields[1:]]
        elif fields[0] == "corner":
            if len(fields) != 7:
                raise ValueError(f"invalid corner record: {line}")
            index = int(fields[1])
            if index in corners:
                raise ValueError(f"duplicate corner record: {index}")
            corners[index] = {
                "zone": int(fields[2]),
                "face_offset": int(fields[3]),
                "face_count": int(fields[4]),
                "volume": parse_hex64(fields[5], "corner volume"),
                "norm_sum": parse_hex64(fields[6], "corner norm sum"),
            }
        elif fields[0] == "face":
            if len(fields) != 6:
                raise ValueError(f"invalid face record: {line}")
            index = int(fields[1])
            if index in faces:
                raise ValueError(f"duplicate face record: {index}")
            faces[index] = {
                "flux_point": int(fields[2]),
                "ez_corner": int(fields[3]),
                "fp_norm": parse_hex64(fields[4], "face fp norm"),
                "ez_norm": parse_hex64(fields[5], "face ez norm"),
            }
        elif fields[0] == "psi1_before":
            if len(fields) != 4:
                raise ValueError(f"invalid psi1 record: {line}")
            key = (int(fields[1]), int(fields[2]))
            if key in psi1:
                raise ValueError(f"duplicate psi1 record: {key}")
            psi1[key] = parse_hex64(fields[3], "psi1")

    required_scalars = scalar_names
    if set(scalars) != required_scalars:
        raise ValueError("native UMT scalar metadata is incomplete")
    if scalars["first_group"] != 0:
        raise ValueError("UMT gather replay requires group zero origin")
    if scalars["group_count"] != identity["groups"]:
        raise ValueError("native UMT group count changed")
    if scalars["selected_corner_count"] != 1 or corner_order != [0]:
        raise ValueError("UMT gather replay requires native corner zero")
    if len(corners) != scalars["corner_count"] or set(corners) != set(
        range(scalars["corner_count"])
    ):
        raise ValueError("native UMT corner records are incomplete")
    if len(faces) != scalars["face_record_count"] or set(faces) != set(
        range(scalars["face_record_count"])
    ):
        raise ValueError("native UMT face records are incomplete")

    expected_psi1 = scalars["flux_point_count"] * scalars["total_groups"]
    if len(psi1) != expected_psi1:
        raise ValueError("native UMT psi1 records are incomplete")
    values = []
    for point in range(scalars["flux_point_count"]):
        for group in range(scalars["total_groups"]):
            key = (point, group)
            if key not in psi1:
                raise ValueError(f"native UMT psi1 element is absent: {key}")
            values.append(psi1[key])

    current = corners[0]
    if current["face_count"] != 3:
        raise ValueError("native UMT gather replay requires three faces")
    current_faces = [
        faces[current["face_offset"] + local]
        for local in range(current["face_count"])
    ]
    accesses = []
    for group in range(scalars["group_count"]):
        for face in current_faces:
            if face["fp_norm"] >> 63:
                accesses.append(
                    face["flux_point"] * scalars["total_groups"] + group
                )
        for local, face in enumerate(current_faces):
            if face["ez_norm"] >> 63:
                raise ValueError(
                    "native UMT gather replay does not model reverse search"
                )
            opposite = current_faces[(local + 1) % current["face_count"]]
            if opposite["fp_norm"] >> 63:
                accesses.append(
                    opposite["flux_point"] * scalars["total_groups"] + group
                )

    if len(accesses) != identity["logical_reads"]:
        raise ValueError("native UMT flux-read incidence changed")
    unique_lines = len({index // 8 for index in accesses})
    if unique_lines != identity["unique_lines"]:
        raise ValueError("native UMT flux-line locality changed")
    return {
        "record_sha256": record_sha256,
        "identity": identity,
        "scalars": scalars,
        "values": values,
        "accesses": accesses,
    }


def c_array(name, values):
    rows = []
    for begin in range(0, len(values), 4):
        words = ", ".join(
            f"UINT64_C(0x{value:016x})" for value in values[begin : begin + 4]
        )
        rows.append("    " + words + ",")
    return f"static const uint64_t {name}[] = {{\n" + "\n".join(rows) + "\n};"


def generate_source(path, values, accesses):
    source = f"""
#include <stdint.h>

#define DATA_VADDR UINT64_C(0x{DATA_VADDR:x})
#define DATA_PADDR UINT64_C(0x{DATA_PADDR:x})
#define CONTROL_VADDR UINT64_C(0x{CONTROL_VADDR:x})
#define ADDRESS_VECTOR_OFFSET UINT64_C(0x{ADDRESS_VECTOR_OFFSET:x})
#define RESULT_VECTOR_OFFSET UINT64_C(0x{RESULT_VECTOR_OFFSET:x})
#define COMPLETION_OFFSET UINT64_C(0x{COMPLETION_OFFSET:x})
#define TARGET_OFFSET UINT64_C(0x{TARGET_OFFSET:x})
#define MAX_ITEMS UINT64_C({DESCRIPTOR_ITEMS})
#define TOTAL_ITEMS UINT64_C({len(accesses)})
#define VALUE_COUNT UINT64_C({len(values)})

{c_array("native_flux_values", values)}

{c_array("native_flux_indices", accesses)}

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
    volatile uint64_t *descriptor = (volatile uint64_t *)(uintptr_t)DATA_VADDR;
    volatile uint64_t *addresses = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + ADDRESS_VECTOR_OFFSET);
    volatile uint64_t *results = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + RESULT_VECTOR_OFFSET);
    volatile uint64_t *completion = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + COMPLETION_OFFSET);
    volatile uint64_t *targets = (volatile uint64_t *)(uintptr_t)(
        DATA_VADDR + TARGET_OFFSET);
    volatile uint64_t *control = (volatile uint64_t *)(uintptr_t)CONTROL_VADDR;

    for (uint64_t value = 0; value < VALUE_COUNT; ++value) {{
        targets[value] = native_flux_values[value];
    }}
    descriptor[0] = UINT64_C(0x0001000131414d4c);
    descriptor[2] = DATA_PADDR + ADDRESS_VECTOR_OFFSET;
    descriptor[3] = DATA_PADDR + RESULT_VECTOR_OFFSET;
    descriptor[4] = DATA_PADDR + COMPLETION_OFFSET;
    descriptor[5] = 0;
    descriptor[6] = 0;
    descriptor[7] = 0;
    fence();

    for (uint64_t begin = 0; begin < TOTAL_ITEMS; begin += MAX_ITEMS) {{
        uint64_t items = TOTAL_ITEMS - begin;
        if (items > MAX_ITEMS) {{
            items = MAX_ITEMS;
        }}
        descriptor[1] = items;
        for (uint64_t item = 0; item < items; ++item) {{
            const uint64_t index = native_flux_indices[begin + item];
            addresses[item] = DATA_PADDR + TARGET_OFFSET + index * 8;
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

        for (uint64_t item = 0; item < items; ++item) {{
            const uint64_t index = native_flux_indices[begin + item];
            if (results[item] != native_flux_values[index]) {{
                finish(UINT64_C(40));
            }}
        }}
        if (completion[0] != UINT64_C(0x0001000143414d4c) ||
            completion[1] != 0 || completion[2] != items ||
            completion[3] != items) {{
            finish(UINT64_C(41));
        }}
    }}
    finish(0);
}}
"""
    path.write_text(source.lstrip(), encoding="utf-8")


def build_program(root, values, accesses):
    compiler = shutil.which("cc")
    if not compiler:
        raise RuntimeError("UMT flux gather smoke requires cc")
    source = root / "umt_native_flux_gather.c"
    binary = root / "umt_native_flux_gather.elf"
    generate_source(source, values, accesses)
    subprocess.run(
        [
            compiler,
            "-std=c11",
            "-O2",
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


def read_stats(path):
    stats = {}
    prefix = "system.lanl_maa."
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            fields = line.split()
            if len(fields) >= 2 and fields[1].isdigit():
                stats[fields[0][len(prefix) :]] = int(fields[1])
    return stats


def read_scalar(path, name):
    prefix = name + " "
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return int(line.split()[1])
    return None


def validate(stats, logical_reads, chunks, minimum_reads, payload_model):
    expected = {
        "logicalItems": logical_reads,
        "logicalMemoryAccesses": logical_reads,
        "physicalLineReads": minimum_reads,
        "lineMergeHits": logical_reads - minimum_reads,
        "completionsRetired": logical_reads,
        "verificationFailures": 0,
        "descriptorDoorbells": chunks,
        "descriptorRearms": chunks - 1,
        "descriptorFetches": chunks,
        "descriptorAddressesLoaded": logical_reads,
        "descriptorResultWrites": logical_reads,
        "descriptorCompletionWrites": chunks,
        "descriptorErrors": 0,
    }
    if payload_model:
        expected.update(
            {
                "payloadOverlayCompletionWrites": logical_reads,
                "payloadOverlayRetirementReads": logical_reads,
            }
        )
    for name, value in expected.items():
        if stats.get(name) != value:
            raise RuntimeError(
                f"UMT gather {name}: expected {value}, got {stats.get(name)}"
            )
    retries = [
        stats.get(name)
        for name in (
            "portSendFailures",
            "portRetryNotifications",
            "retryPacketResubmissions",
            "retryPacketAcceptances",
        )
    ]
    if any(value is None for value in retries) or len(set(retries)) != 1:
        raise RuntimeError(f"UMT gather retry accounting diverged: {retries}")


def run_smoke(args, root):
    simulator_commit, simulator_clean = repository_identity(ROOT)
    if args.require_clean_simulator and not simulator_clean:
        raise RuntimeError("UMT gather evidence requires a clean simulator")
    record = parse_record(args.record.resolve())
    accesses = record["accesses"]
    values = record["values"]
    chunks = (len(accesses) + DESCRIPTOR_ITEMS - 1) // DESCRIPTOR_ITEMS
    chunk_unique_lines = []
    for begin in range(0, len(accesses), DESCRIPTOR_ITEMS):
        chunk = accesses[begin : begin + DESCRIPTOR_ITEMS]
        chunk_unique_lines.append(len({index // 8 for index in chunk}))
    minimum_reads = sum(chunk_unique_lines)
    access_stream = b"".join(struct.pack("<Q", index) for index in accesses)
    value_stream = b"".join(struct.pack("<Q", value) for value in values)
    source, binary = build_program(root, values, accesses)
    metadata = {
        "schema": "lanl-maa-umt-native-flux-gather-v1",
        "mapping": (
            "Exact ordered external and three-face-opposite flux reads from "
            "one native ATS UMT ordinal-1 record"
        ),
        "problem": record["identity"]["problem"],
        "record_path": str(args.record.resolve()),
        "record_sha256": record["record_sha256"],
        "upstream_revision": UPSTREAM_REVISION,
        "groups": record["identity"]["groups"],
        "logical_reads": len(accesses),
        "stream_unique_lines": record["identity"]["unique_lines"],
        "chunk_unique_lines": chunk_unique_lines,
        "sum_chunk_unique_lines": minimum_reads,
        "access_stream_u64le_sha256": hashlib.sha256(
            access_stream
        ).hexdigest(),
        "flux_values_u64le_sha256": hashlib.sha256(value_stream).hexdigest(),
        "chunks": chunks,
        "items": DESCRIPTOR_ITEMS,
        "data_vaddr": DATA_VADDR,
        "data_paddr": DATA_PADDR,
        "data_bytes": DATA_BYTES,
        "control_vaddr": CONTROL_VADDR,
        "control_paddr": CONTROL_PADDR,
        "control_bytes": CONTROL_BYTES,
        "descriptor_paddr": DATA_PADDR + DESCRIPTOR_OFFSET,
        "model_payload_overlay_ports": args.model_payload_overlay_ports,
        "l1_caches": args.l1_caches,
        "source_sha256": file_sha256(source),
        "binary_sha256": file_sha256(binary),
        "simulator_commit": simulator_commit,
        "simulator_worktree_clean": simulator_clean,
        "gem5_sha256": file_sha256(args.gem5.resolve()),
        "runner_sha256": file_sha256(RUNNER),
        "config_sha256": file_sha256(args.config.resolve()),
    }
    metadata_path = root / "metadata.json"
    write_json_atomic(metadata_path, metadata)
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
        "schema": "lanl-maa-umt-native-flux-gather-report-v1",
        "status": "running",
        "terminal": False,
        "problem": record["identity"]["problem"],
        "record_sha256": record["record_sha256"],
        "simulator_commit": simulator_commit,
        "simulator_worktree_clean": simulator_clean,
        "gem5_sha256": metadata["gem5_sha256"],
        "runner_sha256": metadata["runner_sha256"],
        "config_sha256": metadata["config_sha256"],
        "access_stream_u64le_sha256": metadata["access_stream_u64le_sha256"],
        "binary_sha256": metadata["binary_sha256"],
        "metadata_sha256": file_sha256(metadata_path),
        "logical_reads": len(accesses),
        "chunks": chunks,
        "model_payload_overlay_ports": args.model_payload_overlay_ports,
        "l1_caches": args.l1_caches,
        "command": command,
        "correctness_method": (
            "Every gathered FP64 word is compared bit-exactly with the "
            "corresponding native UMT psi1 record word; every descriptor "
            "completion record is checked exactly."
        ),
        "claim_boundary": (
            "Native UMT-derived request-level direct-gather replay; not the "
            "UMT application, full sweep arithmetic, application speedup, "
            "RTL, or promotion evidence."
        ),
    }
    report_path = root / "report.json"
    write_json_atomic(report_path, report)
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
            raise RuntimeError("gem5 native UMT flux gather failed")
        stats_path = outdir / "stats.txt"
        stats = read_stats(stats_path)
        validate(
            stats,
            len(accesses),
            chunks,
            minimum_reads,
            args.model_payload_overlay_ports,
        )
        metric_names = (
            "logicalItems",
            "physicalLineReads",
            "lineMergeHits",
            "lineBankConflictCycles",
            "operationWouldBlockCycles",
            "lineWouldBlockCycles",
            "completionsRetired",
            "payloadOverlayCompletionWrites",
            "payloadOverlayRetirementReads",
            "payloadOverlayCompletionBankConflictCycles",
            "payloadOverlayCompletionReadConflictCycles",
            "payloadOverlayCompletionWouldBlockCycles",
            "payloadOverlayCompletionQueueHighWaterMark",
            "descriptorCycles",
            "engineCycles",
            "verificationFailures",
        )
        report["metrics"] = {name: stats.get(name) for name in metric_names}
        report["metrics"]["simTicks"] = read_scalar(stats_path, "simTicks")
        report["metrics"]["cpuCommittedInstructions"] = read_scalar(
            stats_path, "system.cpu.commitStats0.numInsts"
        )
        if args.l1_caches:
            report["cache_metrics"] = {
                "accesses": read_scalar(
                    stats_path, "system.maa_cache.overallAccesses_T::total"
                ),
                "misses": read_scalar(
                    stats_path, "system.maa_cache.overallMisses_T::total"
                ),
                "hits": read_scalar(
                    stats_path, "system.maa_cache.overallHits_T::total"
                ),
            }
            cache = report["cache_metrics"]
            if (
                cache["accesses"] is None
                or cache["misses"] is None
                or cache["hits"] is None
                or cache["accesses"] != cache["misses"] + cache["hits"]
                or cache["hits"] <= 0
            ):
                raise RuntimeError(
                    f"UMT gather cache accounting failed: {cache}"
                )
        report["logical_to_physical_read_reduction_percent"] = (
            100.0 * (len(accesses) - minimum_reads) / len(accesses)
        )
        report["stats_sha256"] = file_sha256(stats_path)
        report["stdout_sha256"] = file_sha256(stdout_path)
        report["stderr_sha256"] = file_sha256(stderr_path)
        report["status"] = "validated"
        report["terminal"] = True
    except Exception as error:
        report["status"] = "failed"
        report["terminal"] = True
        report["error"] = str(error)
        raise
    finally:
        write_json_atomic(report_path, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", required=True, type=pathlib.Path)
    parser.add_argument("--record", required=True, type=pathlib.Path)
    parser.add_argument("--l1-caches", action="store_true")
    parser.add_argument("--model-payload-overlay-ports", action="store_true")
    parser.add_argument("--require-clean-simulator", action="store_true")
    parser.add_argument(
        "--config",
        default=HERE / "xrage_cpu_descriptor_smoke.py",
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
            prefix="lanl-maa-umt-native-flux-gather-"
        ) as root:
            run_smoke(args, pathlib.Path(root))
    print("LANLMAA native UMT flux gather: PASS")


if __name__ == "__main__":
    main()
