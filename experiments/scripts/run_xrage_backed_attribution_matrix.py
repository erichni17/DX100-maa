#!/usr/bin/env python3
"""Run the exact repeated four-arm XRAGE backed-capacity attribution matrix."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERAL_PATH = (
    ROOT / "experiments/scripts/run_general_hybrid_benchmark_matrix.py"
)
ANALYZER = (
    ROOT / "experiments/analysis/analyze_xrage_backed_attribution_matrix.py"
)
DEFAULT_CONFIG = ROOT / "configs/deprecated/example/se.py"
DEFAULT_RAMULATOR = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"

spec = importlib.util.spec_from_file_location(
    "general_hybrid_runner", GENERAL_PATH
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load general hybrid runner helpers")
general = importlib.util.module_from_spec(spec)
spec.loader.exec_module(general)

ARMS = (
    {
        "name": "native16",
        "role": "ordinary_native_control",
        "guest": "native16",
        "guest_arm": "native16x3",
        "checkpoint_group": "native16",
        "logical": 16384,
        "physical": 16384,
    },
    {
        "name": "native4",
        "role": "ordinary_native_control",
        "guest": "native4",
        "guest_arm": "native4x3",
        "checkpoint_group": "native4",
        "logical": 4096,
        "physical": 4096,
    },
    {
        "name": "backed16",
        "role": "nonfused_backed_direct_index",
        "guest": "native16",
        "guest_arm": "backedx3",
        "checkpoint_group": "backed",
        "logical": 16384,
        "physical": 16384,
    },
    {
        "name": "backed4",
        "role": "nonfused_backed_direct_index",
        "guest": "native16",
        "guest_arm": "backedx3",
        "checkpoint_group": "backed",
        "logical": 16384,
        "physical": 4096,
    },
)


def options(input_path: Path, guest_arm: str) -> str:
    return f"-f {input_path} --maa-arm {guest_arm}"


def checkpoint_command(
    gem5: Path, config: Path, outdir: Path, binary: Path, guest_options: str
) -> list[str]:
    return [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={outdir}",
        str(config),
        "--cpu-type",
        "AtomicSimpleCPU",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        "--max-checkpoints=1",
        "--cmd",
        str(binary),
        "--options",
        guest_options,
    ]


def restore_command(
    gem5: Path,
    config: Path,
    ramulator: Path,
    outdir: Path,
    checkpoint: Path,
    binary: Path,
    guest_options: str,
    logical: int,
    physical: int,
) -> list[str]:
    # This is the accepted line_i128_p1024 full-XRAGE configuration, with the
    # fused/direct-sink mode disabled.  The backed pair changes only the final
    # physical-capacity option (plus its unavoidable output directory).
    return [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={outdir}",
        "--debug-flags=MAAVirtualTrace,MAAIssueDigest,MAAIssueTrace",
        "--debug-file=mechanism.log",
        str(config),
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        f"--checkpoint-dir={checkpoint}",
        "--sys-clock",
        "3.2GHz",
        "--cpu-clock",
        "3.2GHz",
        "--caches",
        "--l1d_size=32kB",
        "--l1d_assoc=8",
        "--l1d-hwp-type=StridePrefetcher",
        "--l1d_mshrs=16",
        "--l1d_write_buffers=8",
        "--l1i_size=32kB",
        "--l1i_assoc=8",
        "--l1i-hwp-type=StridePrefetcher",
        "--l1i_mshrs=16",
        "--l1i_write_buffers=8",
        "--l2cache",
        "--l2_size=256kB",
        "--l2_assoc=4",
        "--l2-hwp-type=StridePrefetcher",
        "--l2_mshrs=32",
        "--l2_write_buffers=16",
        "--l3cache",
        "--l3_size=8MB",
        "--l3_assoc=16",
        "--l3_mshrs=256",
        "--l3_write_buffers=128",
        "--l3_ports=4",
        "--cacheline_size=64",
        "--mem-type",
        "Ramulator2",
        "--ramulator-config",
        str(ramulator),
        "--mem-channels=2",
        "--maa_ncbus_width=32",
        "--maa",
        "--maa_num_maas=1",
        "--maa_num_indirect_units_per_maa=1",
        f"--maa_num_tile_elements={logical}",
        f"--maa_physical_tile_elements={physical}",
        "--maa_transparent_spd_mode=0",
        "--maa_l2_uncacheable",
        "--maa_l3_uncacheable",
        "--maa_num_initial_row_table_slices=32",
        "--maa_num_row_table_rows_per_slice=64",
        "--maa_num_offset_table_entries=0",
        "--maa_num_offset_table_epoch_entries=0",
        "--maa_virtual_combine_slots=384",
        "--maa_virtual_combine_words=4096",
        "--maa_virtual_combine_ways=4",
        "--maa_virtual_combine_banks=0",
        "--maa_virtual_response_slots=128",
        "--maa_virtual_response_word_pool=1024",
        "--maa_virtual_words_per_cycle=4",
        "--maa_virtual_max_outstanding_writes=64",
        "--maa_virtual_index_buffer_lines=128",
        "--maa_virtual_index_partitions=1",
        "--maa_virtual_index_filter_words_per_cycle=0",
        "--maa_retirement_cache_size=1kB",
        "--maa_virtual_masked_writes",
        # Required by the token-bound materializer and held identical across
        # the pair.  No VIRTUAL_TILE_ALU/direct-sink descriptor is issued.
        "--maa_direct_retirement_line_handoff",
        "--cmd",
        str(binary),
        "--options",
        guest_options,
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--gem5", required=True, type=Path)
    parser.add_argument("--ramulator-library", required=True, type=Path)
    parser.add_argument(
        "--ramulator-config", type=Path, default=DEFAULT_RAMULATOR
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--native16", required=True, type=Path)
    parser.add_argument("--native4", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--simulator-provenance", required=True, type=Path)
    parser.add_argument("--guest-build-manifest", required=True, type=Path)
    parser.add_argument("--guest-build-artifacts", required=True, type=Path)
    parser.add_argument("--replicas", type=int, default=2)
    parser.add_argument("--max-parallel-restores", type=int, default=4)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.replicas < 2:
        parser.error("the attribution matrix requires at least two replicas")
    if args.max_parallel_restores < 1:
        parser.error("--max-parallel-restores must be positive")
    return args


def require_files(args: argparse.Namespace) -> None:
    missing = [
        str(path)
        for path in (
            args.gem5,
            args.ramulator_library,
            args.ramulator_config,
            args.config,
            args.native16,
            args.native4,
            args.input,
            args.simulator_provenance,
            args.guest_build_manifest,
            args.guest_build_artifacts,
        )
        if not path.is_file()
    ]
    if missing:
        raise RuntimeError("missing inputs: " + ", ".join(missing))


def run_restore(
    job: dict[str, object],
    checkpoint_identities: dict[str, dict[str, object]],
    checkpoint_root: Path,
    environment: dict[str, str],
) -> str | None:
    group = str(job["checkpoint_group"])
    before = general.tree_identity(checkpoint_root / group / "gem5")
    if before != checkpoint_identities[group]:
        return (
            f"{job['arm']}/{job['replica']}: checkpoint changed before restore"
        )
    run_dir = Path(job["run_dir"])
    rc = general.run_logged(
        list(job["command"]), run_dir / "restore.log", environment
    )
    after = general.tree_identity(checkpoint_root / group / "gem5")
    if after != checkpoint_identities[group]:
        return (
            f"{job['arm']}/{job['replica']}: checkpoint changed during restore"
        )
    if rc != 0:
        return f"{job['arm']}/{job['replica']}: restore failed rc={rc}"
    return None


def main() -> int:
    args = parse_args()
    try:
        require_files(args)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not args.execute:
        print(
            json.dumps(
                {
                    "schema": "dx100.xrage_backed_attribution_plan.v1",
                    "arms": ARMS,
                    "replicas": args.replicas,
                    "max_parallel_restores": args.max_parallel_restores,
                    "timeout": None,
                    "backed_treatment_delta": "physical_tile_elements only",
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
    if args.out.exists():
        print(f"error: refusing to overwrite {args.out}", file=sys.stderr)
        return 2

    out = args.out.resolve()
    out.mkdir(parents=True)
    general.atomic_text(out / "campaign.exit", "running\n")
    frozen = out / "inputs"
    frozen.mkdir()
    sources = {
        "gem5": args.gem5.resolve(),
        "ramulator_library": args.ramulator_library.resolve(),
        "ramulator_config": args.ramulator_config.resolve(),
        "native16": args.native16.resolve(),
        "native4": args.native4.resolve(),
        "workload_input": args.input.resolve(),
        "simulator_provenance": args.simulator_provenance.resolve(),
        "guest_build_manifest": args.guest_build_manifest.resolve(),
        "guest_build_artifacts": args.guest_build_artifacts.resolve(),
    }
    names = {
        "gem5": "gem5.opt",
        "ramulator_library": "libramulator.so",
        "ramulator_config": "ramulator.yaml",
        "native16": "spatter_maa_xrage_runtime_verify_16K",
        "native4": "spatter_maa_xrage_runtime_verify_4K",
        "workload_input": "xrage_gather0_64k.json",
        "simulator_provenance": "simulator-provenance.json",
        "guest_build_manifest": "guest-build-manifest.txt",
        "guest_build_artifacts": "guest-build-artifacts.sha256",
    }
    artifacts: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for key, source in sources.items():
        destination = frozen / names[key]
        hashes[key] = general.copy_stable_artifact(source, destination)
        artifacts[key] = destination.resolve()
    config, config_identity = general.freeze_config_tree(
        args.config, ROOT / "configs", frozen / "configs"
    )
    artifacts["config"] = config
    for key in ("gem5", "native16", "native4"):
        artifacts[key].chmod(0o555)

    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = str(frozen) + (
        ":" + environment["LD_LIBRARY_PATH"]
        if environment.get("LD_LIBRARY_PATH")
        else ""
    )
    environment["OMP_NUM_THREADS"] = "4"
    environment["OMP_PROC_BIND"] = "false"
    ldd = subprocess.run(
        ["ldd", str(artifacts["gem5"])],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    general.atomic_text(frozen / "gem5.ldd.txt", ldd.stdout + ldd.stderr)
    if ldd.returncode or str(artifacts["ramulator_library"]) not in ldd.stdout:
        general.atomic_text(out / "campaign.exit", "1\n")
        print(
            "error: gem5 did not resolve the frozen Ramulator library",
            file=sys.stderr,
        )
        return 1

    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    simulator_provenance = json.loads(
        artifacts["simulator_provenance"].read_text(encoding="utf-8")
    )
    simulator_source_commit = str(simulator_provenance["source_commit"])
    simulator_tree_match = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            simulator_source_commit,
            "HEAD",
            "--",
            "src",
            "configs",
            "SConstruct",
            "ext/ramulator2",
        ],
        cwd=ROOT,
        check=False,
    )
    if simulator_tree_match.returncode != 0:
        general.atomic_text(out / "campaign.exit", "1\n")
        print(
            "error: reused simulator source tree differs from the lead branch",
            file=sys.stderr,
        )
        return 1
    guest_build_values = {}
    for line in (
        artifacts["guest_build_manifest"]
        .read_text(encoding="utf-8")
        .splitlines()
    ):
        key, value = line.split("=", 1)
        guest_build_values[key] = value
    guest_source_commit = guest_build_values["source_commit"]
    guest_is_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", guest_source_commit, "HEAD"],
        cwd=ROOT,
        check=False,
    )
    if guest_is_ancestor.returncode != 0:
        general.atomic_text(out / "campaign.exit", "1\n")
        print(
            "error: guest build commit is not in the lead history",
            file=sys.stderr,
        )
        return 1
    manifest: dict[str, object] = {
        "schema": "dx100.xrage_backed_attribution_matrix.v1",
        "source_commit": source_commit,
        "source_status": "clean",
        "arms": ARMS,
        "replicas": args.replicas,
        "max_parallel_restores": args.max_parallel_restores,
        "timeout": None,
        "configuration_contract": "accepted_xrage_line_i128_p1024_nonfused",
        "backed_treatment_delta": "physical_tile_elements only",
        "fused_direct_sink": False,
        "artifacts": {
            key: {
                "path": str(path),
                "sha256": hashes.get(key, general.sha256_file(path)),
            }
            for key, path in artifacts.items()
        },
        "config_tree": config_identity,
        "provenance": {
            "simulator_source_commit": simulator_source_commit,
            "simulator_source_tree_matches_lead": True,
            "guest_source_commit": guest_source_commit,
            "guest_source_commit_in_lead_history": True,
        },
    }
    general.atomic_json(out / "manifest.json", manifest)

    checkpoint_root = out / "checkpoints"
    checkpoint_identities: dict[str, dict[str, object]] = {}
    group_arms = {str(arm["checkpoint_group"]): arm for arm in ARMS}
    try:
        for group, arm in group_arms.items():
            group_dir = checkpoint_root / group
            group_dir.mkdir(parents=True)
            guest = str(arm["guest"])
            guest_options = options(
                artifacts["workload_input"], str(arm["guest_arm"])
            )
            command = checkpoint_command(
                artifacts["gem5"],
                config,
                group_dir / "gem5",
                artifacts[guest],
                guest_options,
            )
            rc = general.run_logged(
                command, group_dir / "checkpoint.log", environment
            )
            if rc:
                raise RuntimeError(f"checkpoint {group} failed rc={rc}")
            checkpoint_identities[group] = general.tree_identity(
                group_dir / "gem5"
            )
            general.atomic_json(
                group_dir / "identity.json", checkpoint_identities[group]
            )

        jobs: list[dict[str, object]] = []
        for arm in ARMS:
            group = str(arm["checkpoint_group"])
            for replica in range(1, args.replicas + 1):
                run_dir = (
                    out / "arms" / str(arm["name"]) / f"replica-{replica}"
                )
                run_dir.mkdir(parents=True)
                command = restore_command(
                    artifacts["gem5"],
                    config,
                    artifacts["ramulator_config"],
                    run_dir / "gem5",
                    checkpoint_root / group / "gem5",
                    artifacts[str(arm["guest"])],
                    options(
                        artifacts["workload_input"], str(arm["guest_arm"])
                    ),
                    int(arm["logical"]),
                    int(arm["physical"]),
                )
                jobs.append(
                    {
                        "arm": arm["name"],
                        "replica": replica,
                        "checkpoint_group": group,
                        "run_dir": str(run_dir),
                        "command": command,
                    }
                )
        manifest["checkpoint_identity"] = checkpoint_identities
        manifest["restore_runs"] = [
            {
                "arm": job["arm"],
                "replica": job["replica"],
                "checkpoint_group": job["checkpoint_group"],
                "command_sha256": general.sha256_file(
                    Path(job["run_dir"]) / "restore.command.json"
                )
                if (Path(job["run_dir"]) / "restore.command.json").is_file()
                else None,
            }
            for job in jobs
        ]
        general.atomic_json(out / "manifest.json", manifest)
        with ThreadPoolExecutor(
            max_workers=args.max_parallel_restores
        ) as pool:
            failures = list(
                pool.map(
                    lambda job: run_restore(
                        job,
                        checkpoint_identities,
                        checkpoint_root,
                        environment,
                    ),
                    jobs,
                )
            )
        failures = [failure for failure in failures if failure]
        if failures:
            raise RuntimeError("; ".join(failures))
        # Refresh metadata now that immutable command records exist.
        for record, job in zip(manifest["restore_runs"], jobs):
            command_path = Path(job["run_dir"]) / "restore.command.json"
            record["command_sha256"] = general.sha256_file(command_path)
        general.atomic_json(out / "manifest.json", manifest)
        analyzed = subprocess.run(
            [sys.executable, str(ANALYZER), str(out)], check=False
        )
        if analyzed.returncode:
            raise RuntimeError("post-run evidence validation failed")
    except RuntimeError as error:
        general.atomic_text(out / "campaign.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1

    general.atomic_text(out / "campaign.exit", "0\n")
    general.atomic_text(out / "campaign.complete", general.utc_now() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
