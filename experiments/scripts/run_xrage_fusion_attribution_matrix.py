#!/usr/bin/env python3
"""Run the repeated XRAGE direct4x3 arm against frozen accepted controls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKED_RUNNER_PATH = (
    ROOT / "experiments/scripts/run_xrage_backed_attribution_matrix.py"
)
GENERAL_PATH = (
    ROOT / "experiments/scripts/run_general_hybrid_benchmark_matrix.py"
)
ANALYZER = (
    ROOT / "experiments/analysis/analyze_xrage_fusion_attribution_matrix.py"
)


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backed = load("xrage_backed_runner_for_fusion", BACKED_RUNNER_PATH)
general = load("general_hybrid_runner_for_xrage_fusion", GENERAL_PATH)

ACCEPTED_MANIFEST_SHA256 = (
    "a5c9efdbf955fcd24e58b72bdaefb9a93210f9cb27eba1a6365281011be3754d"
)
ACCEPTED_REPORT_SHA256 = (
    "346ec9d1d92973eac170296c134d629a5326ef191624c7adb35dfcae8e3e8d50"
)
ACCEPTED_GUEST_SOURCE_COMMIT = "95a6836e8070cf0daeae579375f2c9e2df4ed73b"
ACCEPTED_SIMULATOR_SOURCE_COMMIT = "be77a62ca992507d9145fe0d44c9ed491c8310a2"
ACCEPTED_ARTIFACT_HASHES = {
    "gem5": "44b6e86ebc86fd692ce02dcb2e1f627082ded10c741134ae49de5721e2edcb45",
    "ramulator_library": "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
    "ramulator_config": "aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b",
    "native16": "365aa7f2e9d83f0f5d789d3cc1357a98c31680244a50b75d92a9c193ff69726e",
    "workload_input": "70e3d82973d7a93300db950d2c81e9db5b6a37273b0f21da8344302ce53022d9",
}
ACCEPTED_ROW_IDENTITIES = {
    (
        "native16",
        1,
    ): "7b7190f4d669e3c5b3c055b52cfeb0d55af0006ef92ec781d2ab85d36a4a766c",
    (
        "native16",
        2,
    ): "e20070aeaa31e32b24e100584f00e211c4b9cd3c49121fa92430a7877060e91f",
    (
        "native4",
        1,
    ): "0ba534330fa8621699017cb07fe70476092b6895b5ef14dcf5f9d272beb156f1",
    (
        "native4",
        2,
    ): "dd146ba667fd28a117c39f4405501046e79ea8b0ec56f18d9a05a62c1cdac2bc",
    (
        "backed4",
        1,
    ): "58619062b3b5c50efba38070aa4515f15ce852c00ef6ce2f36c584055ae6263b",
    (
        "backed4",
        2,
    ): "092b8a31740d0beb272f9af5395d36dfc643e2eeebf95f173baf462d2c7ce0a0",
}
ROW_IDENTITY_FILES = (
    "restore.exit",
    "restore.log",
    "restore.command.json",
    "gem5/config.ini",
    "gem5/stats.txt",
    "gem5/mechanism.log",
)
GUEST_SOURCE_PATHS = (
    "benchmarks/spatter/src",
    "benchmarks/API/MAA_gem5.hpp",
)
SIMULATOR_SOURCE_PATHS = (
    "src",
    "configs",
    "SConstruct",
    "ext/ramulator2",
)
DIRECT_ARM = {
    "name": "direct4x3",
    "role": "fused_direct_sink",
    "guest": "native16",
    "guest_arm": "direct4x3",
    "checkpoint_group": "direct4x3",
    "logical": 16384,
    "physical": 4096,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--accepted-root", required=True, type=Path)
    parser.add_argument("--replicas", type=int, default=2)
    parser.add_argument("--max-parallel-restores", type=int, default=2)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.replicas < 2:
        parser.error("the direct arm requires at least two replicas")
    if args.max_parallel_restores < 2:
        parser.error("repeated direct restores must execute concurrently")
    return args


def read_accepted(root: Path) -> tuple[dict[str, object], dict[str, object]]:
    manifest_path = root / "manifest.json"
    report_path = root / "analysis/report.json"
    pass_path = root / "analysis/report.pass"
    if (
        not manifest_path.is_file()
        or not report_path.is_file()
        or not pass_path.is_file()
    ):
        raise RuntimeError("accepted root lacks immutable PASS evidence")
    if general.sha256_file(manifest_path) != ACCEPTED_MANIFEST_SHA256:
        raise RuntimeError("accepted manifest hash mismatch")
    if general.sha256_file(report_path) != ACCEPTED_REPORT_SHA256:
        raise RuntimeError("accepted report hash mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "dx100.xrage_backed_attribution_matrix.v1":
        raise RuntimeError("accepted root has wrong matrix schema")
    if report.get("status") != "PASS":
        raise RuntimeError("accepted control report is not PASS")
    provenance = manifest.get("provenance", {})
    if provenance.get("guest_source_commit") != ACCEPTED_GUEST_SOURCE_COMMIT:
        raise RuntimeError("accepted guest source commit mismatch")
    if (
        provenance.get("simulator_source_commit")
        != ACCEPTED_SIMULATOR_SOURCE_COMMIT
    ):
        raise RuntimeError("accepted simulator source commit mismatch")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RuntimeError("accepted manifest lacks artifacts")
    for key, record in artifacts.items():
        if not isinstance(record, dict):
            raise RuntimeError(f"accepted {key} artifact record is invalid")
        expected = str(record.get("sha256", ""))
        path = Path(str(record.get("path", "")))
        if not path.is_file() or general.sha256_file(path) != expected:
            raise RuntimeError(f"accepted {key} artifact changed")
    for key, expected in ACCEPTED_ARTIFACT_HASHES.items():
        record = artifacts.get(key)
        if not isinstance(record, dict) or record.get("sha256") != expected:
            raise RuntimeError(f"accepted {key} identity mismatch")
        path = Path(str(record.get("path", "")))
        if not path.is_file() or general.sha256_file(path) != expected:
            raise RuntimeError(f"accepted {key} artifact changed")
    for key, expected in ACCEPTED_ROW_IDENTITIES.items():
        if (
            row_identity(root / "arms" / key[0] / f"replica-{key[1]}")
            != expected
        ):
            raise RuntimeError(f"accepted {key[0]}/{key[1]} row changed")
    return manifest, report


def row_identity(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in ROW_IDENTITY_FILES:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"accepted row lacks {relative}")
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(general.sha256_file(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def tree_matches(commit: str, paths: tuple[str, ...]) -> bool:
    return (
        subprocess.run(
            ["git", "diff", "--quiet", commit, "HEAD", "--", *paths],
            cwd=ROOT,
            check=False,
        ).returncode
        == 0
    )


def direct_restore_command(
    gem5: Path,
    config: Path,
    ramulator: Path,
    outdir: Path,
    checkpoint: Path,
    binary: Path,
    guest_options: str,
) -> list[str]:
    command = backed.restore_command(
        gem5,
        config,
        ramulator,
        outdir,
        checkpoint,
        binary,
        guest_options,
        16384,
        4096,
    )
    disabled = command.index("--maa_transparent_spd_mode=0")
    command[disabled] = "--maa_transparent_spd_mode=3"
    return command


def run_restore(
    job: dict[str, object],
    checkpoint_identity: dict[str, object],
    checkpoint: Path,
    environment: dict[str, str],
) -> str | None:
    before = general.tree_identity(checkpoint)
    if before != checkpoint_identity:
        return f"direct4x3/{job['replica']}: checkpoint changed before restore"
    run_dir = Path(str(job["run_dir"]))
    rc = general.run_logged(
        list(job["command"]), run_dir / "restore.log", environment
    )
    after = general.tree_identity(checkpoint)
    if after != checkpoint_identity:
        return f"direct4x3/{job['replica']}: checkpoint changed during restore"
    if rc:
        return f"direct4x3/{job['replica']}: restore failed rc={rc}"
    return None


def main() -> int:
    args = parse_args()
    accepted_root = args.accepted_root.resolve()
    try:
        accepted_manifest, _accepted_report = read_accepted(accepted_root)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not args.execute:
        print(
            json.dumps(
                {
                    "schema": "dx100.xrage_fusion_attribution_plan.v1",
                    "accepted_root": str(accepted_root),
                    "reused_arms": ["native16", "native4", "backed4"],
                    "new_arm": DIRECT_ARM,
                    "replicas": args.replicas,
                    "max_parallel_restores": args.max_parallel_restores,
                    "timeout": None,
                    "direct_checkpoint_argv": "-f INPUT --maa-arm direct4x3",
                    "decisive_comparison": "direct4x3 vs backed4 at fixed physical4",
                },
                indent=2,
            )
        )
        return 0

    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        print(
            "error: execution requires a clean source worktree",
            file=sys.stderr,
        )
        print(status, file=sys.stderr, end="")
        return 1
    if not tree_matches(ACCEPTED_GUEST_SOURCE_COMMIT, GUEST_SOURCE_PATHS):
        print(
            "error: current XRAGE guest/API source differs from accepted build",
            file=sys.stderr,
        )
        return 1
    if not tree_matches(
        ACCEPTED_SIMULATOR_SOURCE_COMMIT, SIMULATOR_SOURCE_PATHS
    ):
        print(
            "error: current simulator tree differs from accepted gem5 source",
            file=sys.stderr,
        )
        return 1
    if args.out.exists():
        print(f"error: refusing to overwrite {args.out}", file=sys.stderr)
        return 2

    out = args.out.resolve()
    out.mkdir(parents=True)
    general.atomic_text(out / "campaign.exit", "running\n")
    artifacts = accepted_manifest["artifacts"]
    assert isinstance(artifacts, dict)
    paths = {
        key: Path(str(artifacts[key]["path"])).resolve()
        for key in ACCEPTED_ARTIFACT_HASHES
    }
    config = Path(str(artifacts["config"]["path"])).resolve()
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest: dict[str, object] = {
        "schema": "dx100.xrage_fusion_attribution_matrix.v1",
        "source_commit": source_commit,
        "source_status": "clean",
        "accepted_root": str(accepted_root),
        "accepted_manifest_sha256": ACCEPTED_MANIFEST_SHA256,
        "accepted_report_sha256": ACCEPTED_REPORT_SHA256,
        "accepted_reused_arms": ["native16", "native4", "backed4"],
        "new_arm": DIRECT_ARM,
        "replicas": args.replicas,
        "max_parallel_restores": args.max_parallel_restores,
        "timeout": None,
        "simulated_metric": "first ROI simTicks only",
        "decisive_comparison": "direct4x3 vs backed4 at fixed physical4",
        "virtualization_claim_permitted": False,
        "artifacts": {
            key: {"path": str(path), "sha256": ACCEPTED_ARTIFACT_HASHES[key]}
            for key, path in paths.items()
        },
        "config": {
            "path": str(config),
            "sha256": str(artifacts["config"]["sha256"]),
        },
        "provenance": {
            "guest_source_commit": ACCEPTED_GUEST_SOURCE_COMMIT,
            "guest_source_tree_matches_lead": True,
            "simulator_source_commit": ACCEPTED_SIMULATOR_SOURCE_COMMIT,
            "simulator_source_tree_matches_lead": True,
        },
    }
    general.atomic_json(out / "manifest.json", manifest)

    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = str(paths["ramulator_library"].parent) + (
        ":" + environment["LD_LIBRARY_PATH"]
        if environment.get("LD_LIBRARY_PATH")
        else ""
    )
    environment["OMP_NUM_THREADS"] = "4"
    environment["OMP_PROC_BIND"] = "false"
    ldd = subprocess.run(
        ["ldd", str(paths["gem5"])],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    general.atomic_text(out / "gem5.ldd.txt", ldd.stdout + ldd.stderr)
    if ldd.returncode or str(paths["ramulator_library"]) not in ldd.stdout:
        general.atomic_text(out / "campaign.exit", "1\n")
        print(
            "error: gem5 did not resolve the accepted Ramulator library",
            file=sys.stderr,
        )
        return 1
    guest_options = backed.options(paths["workload_input"], "direct4x3")
    checkpoint_dir = out / "checkpoint"
    checkpoint_dir.mkdir()
    checkpoint_command = backed.checkpoint_command(
        paths["gem5"],
        config,
        checkpoint_dir / "gem5",
        paths["native16"],
        guest_options,
    )
    try:
        checkpoint_rc = general.run_logged(
            checkpoint_command, checkpoint_dir / "checkpoint.log", environment
        )
        if checkpoint_rc:
            raise RuntimeError(
                f"direct4x3 checkpoint failed rc={checkpoint_rc}"
            )
        checkpoint_identity = general.tree_identity(checkpoint_dir / "gem5")
        general.atomic_json(
            checkpoint_dir / "identity.json", checkpoint_identity
        )
        jobs: list[dict[str, object]] = []
        for replica in range(1, args.replicas + 1):
            run_dir = out / "direct4x3" / f"replica-{replica}"
            run_dir.mkdir(parents=True)
            command = direct_restore_command(
                paths["gem5"],
                config,
                paths["ramulator_config"],
                run_dir / "gem5",
                checkpoint_dir / "gem5",
                paths["native16"],
                guest_options,
            )
            jobs.append(
                {
                    "replica": replica,
                    "run_dir": str(run_dir),
                    "command": command,
                }
            )
        manifest["checkpoint_identity"] = checkpoint_identity
        manifest["checkpoint_command_sha256"] = general.sha256_file(
            checkpoint_dir / "checkpoint.command.json"
        )
        manifest["restore_runs"] = [
            {"replica": job["replica"], "command_sha256": None} for job in jobs
        ]
        general.atomic_json(out / "manifest.json", manifest)
        with ThreadPoolExecutor(
            max_workers=args.max_parallel_restores
        ) as pool:
            failures = list(
                pool.map(
                    lambda job: run_restore(
                        job,
                        checkpoint_identity,
                        checkpoint_dir / "gem5",
                        environment,
                    ),
                    jobs,
                )
            )
        failures = [failure for failure in failures if failure]
        if failures:
            raise RuntimeError("; ".join(failures))
        for record, job in zip(manifest["restore_runs"], jobs):
            assert isinstance(record, dict)
            record["command_sha256"] = general.sha256_file(
                Path(str(job["run_dir"])) / "restore.command.json"
            )
        general.atomic_json(out / "manifest.json", manifest)
        analyzed = subprocess.run(
            [sys.executable, str(ANALYZER), str(out)], check=False
        )
        if analyzed.returncode:
            raise RuntimeError(f"analysis failed rc={analyzed.returncode}")
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        general.atomic_text(out / "campaign.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1
    general.atomic_text(out / "campaign.exit", "0\n")
    print(f"PASS XRAGE fusion attribution: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
