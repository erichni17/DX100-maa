#!/usr/bin/env python3
"""Create the durable physical tile-size sweep workflow and manifest."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

TILES = (16384, 4096, 1024, 2048, 8192, 32768, 65536)
BASELINE_SHA256 = (
    "bcc30842a2f26aad2a0cddc769381180f885c683c0be711e2feffb0ac56c18ab"
)
IS_KEY_HEADER_SHA256 = (
    "b70a33ed1a5017425c85ba664618f0dabac520df96b95894ec5657270cf75479"
)
CG_DATA_HEADER_SHA256 = (
    "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131"
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    temporary.replace(path)


def task(task_id, command, environment):
    return {
        "id": task_id,
        "command": [str(item) for item in command],
        "cwd": environment["DX100_SOURCE_ROOT"],
        "env": environment,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--is-key-header", type=Path, required=True)
    parser.add_argument("--cg-data-header", type=Path, required=True)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    runtime_root = args.runtime_root.resolve()
    run_root = args.run_root.resolve()
    is_key_header = args.is_key_header.resolve()
    cg_data_header = args.cg_data_header.resolve()
    gem5 = runtime_root / "build/X86/gem5.opt.ovl_base"
    actual_sha256 = sha256(gem5)
    if actual_sha256 != BASELINE_SHA256:
        raise SystemExit(
            f"baseline gem5 hash mismatch: {actual_sha256} != {BASELINE_SHA256}"
        )

    is_key_header_sha256 = sha256(is_key_header)
    if is_key_header_sha256 != IS_KEY_HEADER_SHA256:
        raise SystemExit(
            "IS key header hash mismatch: "
            f"{is_key_header_sha256} != {IS_KEY_HEADER_SHA256}"
        )
    staged_is_header = source_root / "benchmarks/NAS/is/key_array_4C.h"
    if (
        not staged_is_header.exists()
        or sha256(staged_is_header) != IS_KEY_HEADER_SHA256
    ):
        temporary = staged_is_header.with_name(f".{staged_is_header.name}.tmp")
        shutil.copy2(is_key_header, temporary)
        temporary.replace(staged_is_header)

    cg_data_header_sha256 = sha256(cg_data_header)
    if cg_data_header_sha256 != CG_DATA_HEADER_SHA256:
        raise SystemExit(
            "CG data header hash mismatch: "
            f"{cg_data_header_sha256} != {CG_DATA_HEADER_SHA256}"
        )
    staged_cg_header = source_root / "benchmarks/NAS/cg/cg_data_4C.h"
    if (
        not staged_cg_header.exists()
        or sha256(staged_cg_header) != CG_DATA_HEADER_SHA256
    ):
        temporary = staged_cg_header.with_name(f".{staged_cg_header.name}.tmp")
        shutil.copy2(cg_data_header, temporary)
        temporary.replace(staged_cg_header)

    common = {
        "DX100_SOURCE_ROOT": str(source_root),
        "DX100_RUNTIME_ROOT": str(runtime_root),
        "DX100_GEM5_BIN": str(gem5),
        "DX100_SE_CONFIG": str(
            runtime_root / "configs/deprecated/example/se.py"
        ),
        "DX100_RAMULATOR_CONFIG": str(
            runtime_root / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
        ),
        "DX100_RAMULATOR_LIBDIR": str(
            runtime_root / "ext/ramulator2/ramulator2"
        ),
        "CHECKPOINT_ROOT": str(run_root / "checkpoints"),
        "OMP_PROC_BIND": "false",
        "OMP_NUM_THREADS": "4",
    }
    workflow_tasks = []
    for tile in TILES:
        for kernel in ("bfs", "sssp", "bc"):
            env = dict(common, CAMPAIGN_ROOT=str(run_root / "gapbs"))
            command = [
                source_root / "benchmarks/gapbs/run_gapbs_tile_smoke.sh",
                "gem5.opt.ovl_base",
                kernel,
                tile,
                22,
                1,
                "2GB",
                0,
                0,
                10000000,
            ]
            workflow_tasks.append(
                task(f"gapbs-{kernel}-t{tile}", command, env)
            )

        env = dict(common, CAMPAIGN_ROOT=str(run_root / "is"))
        command = [
            source_root / "benchmarks/NAS/is/run_is_smoke.sh",
            "gem5.opt.ovl_base",
            tile,
            0,
            0,
            0,
            10000000,
        ]
        workflow_tasks.append(task(f"nas-is-t{tile}", command, env))

        env = dict(common, CAMPAIGN_ROOT=str(run_root / "cg"))
        command = [
            source_root / "benchmarks/NAS/cg/run_cg_tile_smoke.sh",
            "gem5.opt.ovl_base",
            tile,
            "2GB",
            0,
            0,
            10000000,
        ]
        workflow_tasks.append(task(f"nas-cg-t{tile}", command, env))

        for kernel in ("gradzatp", "gradzatz"):
            env = dict(common, CAMPAIGN_ROOT=str(run_root / "ume"))
            command = [
                source_root / "benchmarks/UME/run_ume_tile_smoke.sh",
                "gem5.opt.ovl_base",
                kernel,
                tile,
                1000000,
                "2GB",
                0,
                0,
                10000000,
            ]
            workflow_tasks.append(task(f"ume-{kernel}-t{tile}", command, env))

        env = dict(
            common,
            CAMPAIGN_ROOT=str(run_root / "xrage"),
            XRAGE_DATA=str(
                runtime_root
                / "benchmarks/spatter/tests/test-data/xrage/all.json"
            ),
        )
        command = [
            source_root / "benchmarks/spatter/run_xrage_tile_smoke.sh",
            "gem5.opt.ovl_base",
            tile,
            "2GB",
            0,
            0,
            10000000,
        ]
        workflow_tasks.append(task(f"xrage-t{tile}", command, env))

    workflow = {
        "version": 1,
        "name": "dx100-full-tile-sweep-20260720",
        "tasks": workflow_tasks,
    }
    write_json(args.output, workflow)

    source_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
    ).strip()
    manifest = {
        "schema_version": 1,
        "objective": "Complete missing physical tile-size curves across DX100 workloads",
        "source_root": str(source_root),
        "source_commit": source_commit,
        "runtime_root": str(runtime_root),
        "gem5_binary": str(gem5),
        "gem5_sha256": actual_sha256,
        "is_key_header_source": str(is_key_header),
        "is_key_header_staged": str(staged_is_header),
        "is_key_header_sha256": is_key_header_sha256,
        "cg_data_header_source": str(cg_data_header),
        "cg_data_header_staged": str(staged_cg_header),
        "cg_data_header_sha256": cg_data_header_sha256,
        "tiles": list(TILES),
        "max_parallel_initial": 20,
        "wall_clock_timeout_seconds": None,
        "timing_metric": "first ROI simTicks",
        "fresh_workloads": [
            "GAPBS BFS S22",
            "GAPBS SSSP S22",
            "GAPBS BC S22",
            "NAS IS full class",
            "NAS CG",
            "UME gradzatp",
            "UME gradzatz",
            "XRAGE",
        ],
        "reused_complete_workloads": [
            "GAPBS PageRank S22",
            "HashJoin PRH 2M/2M",
            "HashJoin PRO 2M/2M",
        ],
        "unsupported_points": [],
        "correctness_policy": "Each runner fails closed on its post-ROI semantic marker; cross-tile fingerprints are compared before graphing.",
        "workflow": str(args.output.resolve()),
        "task_count": len(workflow_tasks),
    }
    write_json(run_root / "manifest.json", manifest)
    print(
        json.dumps(
            {"workflow": str(args.output), "tasks": len(workflow_tasks)}
        )
    )


if __name__ == "__main__":
    main()
