#!/usr/bin/env python3
"""Run the five-arm GZP SoA/JIT matrix with frozen provenance.

Execution is intentionally gated on an explicit lead-provided optimized gem5
SHA-256.  The volume-only arm is performance-capable; the two-RMW SoA/JIT arm
uses bounded response-bearing SPD publication and remains correctness-only
until a matched same-binary performance run is accepted.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_general_hybrid_benchmark_matrix as common  # noqa: E402

DEFAULT_CONFIG = ROOT / "configs/deprecated/example/se.py"
DEFAULT_RAMULATOR = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"
EXPECTED_HASH = "11225737641199706160"

ARMS = (
    ("native16", "native16", "native16", None),
    ("native4", "native4", "native4", None),
    (
        "current_hybrid",
        "hybrid",
        "hybrid",
        "token_stream_ld legacy_4k",
    ),
    (
        "volume_only_soa_jit",
        "hybrid",
        "hybrid",
        "token_stream_ld volume_soa_jit",
    ),
    (
        "soa_jit_correctness",
        "hybrid",
        "hybrid",
        "token_stream_ld soa_jit",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--gem5", required=True, type=Path)
    parser.add_argument("--ramulator-library", required=True, type=Path)
    parser.add_argument("--native16", required=True, type=Path)
    parser.add_argument("--native4", required=True, type=Path)
    parser.add_argument("--hybrid", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--ramulator-config", type=Path, default=DEFAULT_RAMULATOR
    )
    parser.add_argument("--n", type=int, default=1_000_000)
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--mem-channels", type=int, default=2)
    parser.add_argument("--l3-ports", type=int, default=4)
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="maximum concurrent checkpoint or restore gem5 processes (1-16)",
    )
    parser.add_argument(
        "--lead-optimized-gem5-sha256",
        help="required with --execute; must equal the supplied gem5 binary",
    )
    parser.add_argument(
        "--extra-gem5-arg",
        action="append",
        default=[],
        help="restore only: add one global gem5 --option token",
    )
    parser.add_argument(
        "--restore-arm-gem5-arg",
        action="append",
        default=[],
        type=common.parse_restore_arm_gem5_arg,
        metavar="ARM=ARG",
        help="restore only: add one gem5 option to exactly one named arm",
    )
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.n != 1_000_000:
        parser.error("the frozen exact-hash contract requires --n=1000000")
    if args.replicas < 1 or args.mem_channels < 1:
        parser.error("replicas and memory channels must be positive")
    if not 1 <= args.l3_ports <= 16:
        parser.error("--l3-ports must be in [1,16]")
    if not 1 <= args.max_workers <= 16:
        parser.error("--max-workers must be in [1,16]")
    if args.execute and not args.lead_optimized_gem5_sha256:
        parser.error(
            "--execute requires --lead-optimized-gem5-sha256 from the lead"
        )
    if args.lead_optimized_gem5_sha256 and not all(
        character in "0123456789abcdef"
        for character in args.lead_optimized_gem5_sha256
    ):
        parser.error("the lead gem5 SHA-256 must be lowercase hexadecimal")
    if (
        args.lead_optimized_gem5_sha256
        and len(args.lead_optimized_gem5_sha256) != 64
    ):
        parser.error("the lead gem5 SHA-256 must contain 64 digits")
    for extra in args.extra_gem5_arg:
        if not extra.startswith("--") or any(
            character.isspace() for character in extra
        ):
            parser.error("each --extra-gem5-arg must be one --option token")
    try:
        args.restore_arm_gem5_args = common.restore_arm_gem5_args(
            args.restore_arm_gem5_arg,
            [{"name": name} for name, _profile, _binary, _selector in ARMS],
        )
    except ValueError as error:
        parser.error(str(error))
    return args


def require_files(args: argparse.Namespace) -> None:
    paths = (
        args.gem5,
        args.ramulator_library,
        args.ramulator_config,
        args.config,
        args.native16,
        args.native4,
        args.hybrid,
    )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError("missing input artifacts: " + ", ".join(missing))


def plan(args: argparse.Namespace) -> dict[str, object]:
    return {
        "schema": "dx100.gzp_soa_jit_plan.v1",
        "workload": "ume-gzp",
        "n": args.n,
        "replicas": args.replicas,
        "arms": [
            {
                "name": name,
                "profile": profile,
                "binary": binary,
                "checkpoint_group": name,
                "selector": selector,
                "performance_comparison_authorized": (
                    name != "soa_jit_correctness"
                ),
            }
            for name, profile, binary, selector in ARMS
        ],
        "exact_output_hash": EXPECTED_HASH,
        "performance_comparisons": {
            "current_hybrid_vs_volume_only_soa_jit": True,
            "current_hybrid_vs_soa_jit_correctness": False,
        },
        "extra_gem5_args": args.extra_gem5_arg,
        "restore_arm_gem5_args": args.restore_arm_gem5_args,
        "max_workers": args.max_workers,
        "execution_gate": "lead-provided optimized gem5 SHA-256",
    }


def freeze_inputs(
    args: argparse.Namespace, frozen: Path
) -> tuple[
    dict[str, Path], dict[str, dict[str, str]], Path, dict[str, object]
]:
    sources = {
        "gem5": args.gem5,
        "ramulator_library": args.ramulator_library,
        "ramulator_config": args.ramulator_config,
        "native16": args.native16,
        "native4": args.native4,
        "hybrid": args.hybrid,
    }
    names = {
        "gem5": "gem5.opt",
        "ramulator_library": "libramulator.so",
        "ramulator_config": "ramulator.yaml",
    }
    artifacts: dict[str, Path] = {}
    identities: dict[str, dict[str, str]] = {}
    for key, source in sources.items():
        destination = frozen / names.get(key, key)
        digest = common.copy_stable_artifact(source.resolve(), destination)
        artifacts[key] = destination.resolve()
        identities[key] = {
            "path": str(destination.resolve()),
            "sha256": digest,
        }
    frozen_config, config_identity = common.freeze_config_tree(
        args.config, ROOT / "configs", frozen / "configs"
    )
    return artifacts, identities, frozen_config, config_identity


def source_identity() -> dict[str, object]:
    paths = (
        ROOT / "benchmarks/UME/gradzatp.cpp",
        ROOT / "benchmarks/API/MAA_gem5.hpp",
        ROOT / "experiments/scripts/run_gzp_soa_jit_correctness.py",
        ROOT / "experiments/analysis/analyze_gzp_soa_jit_correctness.py",
    )
    return {
        "commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "files": {
            str(path.relative_to(ROOT)): common.sha256_file(path)
            for path in paths
        },
    }


def main() -> int:
    args = parse_args()
    try:
        require_files(args)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not args.execute:
        print(json.dumps(plan(args), indent=2, sort_keys=True))
        return 0

    try:
        args.out.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        print(
            "error: raw evidence output must be outside the Git tree",
            file=sys.stderr,
        )
        return 2

    gem5_hash = common.sha256_file(args.gem5)
    if gem5_hash != args.lead_optimized_gem5_sha256:
        print(
            "error: gem5 hash does not match the lead-provided hash",
            file=sys.stderr,
        )
        return 2
    status = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        print(
            "error: evidence execution requires a clean source tree",
            file=sys.stderr,
        )
        print(status, file=sys.stderr, end="")
        return 1
    if args.out.exists():
        print(f"error: refusing to overwrite {args.out}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True)
    common.atomic_text(args.out / "campaign.exit", "running\n")
    frozen = args.out / "inputs"
    frozen.mkdir()
    try:
        artifacts, identities, config, config_identity = freeze_inputs(
            args, frozen
        )
        for key in ("gem5", "native16", "native4", "hybrid"):
            artifacts[key].chmod(0o555)
        environment = os.environ.copy()
        environment["LD_LIBRARY_PATH"] = str(frozen.resolve())
        environment["OMP_NUM_THREADS"] = "4"
        environment["OMP_PROC_BIND"] = "false"
        ldd = subprocess.run(
            ["ldd", str(artifacts["gem5"])],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        common.atomic_text(frozen / "gem5.ldd.txt", ldd.stdout + ldd.stderr)
        if (
            ldd.returncode != 0
            or str(artifacts["ramulator_library"]) not in ldd.stdout
        ):
            raise RuntimeError("frozen gem5 did not resolve frozen Ramulator")

        manifest: dict[str, object] = {
            **plan(args),
            "schema": "dx100.gzp_soa_jit_matrix.v2",
            "source": source_identity(),
            "source_status": "clean",
            "artifacts": identities,
            "config_tree": {
                "path": str((frozen / "configs").resolve()),
                **config_identity,
            },
            "mem_channels": args.mem_channels,
            "l3_ports": args.l3_ports,
            "simulated_metric": "simTicks",
            "host_time_metric_authorized": False,
            "checkpoints": {},
            "checkpoint_commands": {},
            "runs": [],
        }
        common.atomic_json(args.out / "manifest.json", manifest)

        checkpoints: dict[str, dict[str, object]] = {}
        checkpoint_commands: dict[str, dict[str, str]] = {}
        checkpoint_jobs: list[tuple[str, str, str | None, Path, str]] = []
        for name, _profile, binary, selector in ARMS:
            directory = args.out / "checkpoints" / name
            directory.mkdir(parents=True)
            options = str(args.n)
            if selector is not None:
                selector_path = directory / "selector.txt"
                common.atomic_text(selector_path, selector + "\n")
                selector_path.chmod(0o444)
                options += f" {selector_path.resolve()}"
            checkpoint_jobs.append(
                (name, binary, selector, directory, options)
            )

        def create_checkpoint(
            job: tuple[str, str, str | None, Path, str]
        ) -> tuple[str, dict[str, object], dict[str, str]]:
            group, binary, selector, directory, options = job
            command = common.checkpoint_command(
                artifacts["gem5"],
                config,
                directory / "gem5",
                artifacts[binary],
                options,
            )
            rc = common.run_logged(
                command, directory / "checkpoint.log", environment
            )
            if rc != 0:
                raise RuntimeError(f"checkpoint {group} failed with rc={rc}")
            selector_identity: dict[str, str] | None = None
            if selector is not None:
                selector_path = directory / "selector.txt"
                selector_identity = {
                    "path": str(selector_path.resolve()),
                    "sha256": common.sha256_file(selector_path),
                }
            checkpoint_identity = {
                "tree": common.tree_identity(directory / "gem5"),
                "arm": group,
                "binary": binary,
                "selector": selector,
                "selector_identity": selector_identity,
            }
            command_path = directory / "checkpoint.command.json"
            command_identity = {
                "path": str(command_path.resolve()),
                "sha256": common.sha256_file(command_path),
            }
            return group, checkpoint_identity, command_identity

        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            for group, identity, command_identity in executor.map(
                create_checkpoint, checkpoint_jobs
            ):
                checkpoints[group] = identity
                checkpoint_commands[group] = command_identity
        manifest["checkpoints"] = checkpoints
        manifest["checkpoint_commands"] = checkpoint_commands
        common.atomic_json(args.out / "manifest.json", manifest)

        restore_jobs: list[
            tuple[str, str, str, str | None, int, Path, list[str]]
        ] = []
        for name, profile, binary, selector in ARMS:
            group = name
            checkpoint = args.out / "checkpoints" / group / "gem5"
            for replica in range(1, args.replicas + 1):
                run = args.out / "arms" / name / f"replica-{replica}"
                run.mkdir(parents=True)
                gem5_args = common.restore_args_for_arm(
                    args.extra_gem5_arg,
                    args.restore_arm_gem5_args,
                    name,
                )
                restore_jobs.append(
                    (
                        name,
                        profile,
                        binary,
                        selector,
                        replica,
                        checkpoint,
                        gem5_args,
                    )
                )

        def restore(
            job: tuple[str, str, str, str | None, int, Path, list[str]]
        ) -> dict[str, object]:
            (
                name,
                profile,
                binary,
                selector,
                replica,
                checkpoint,
                gem5_args,
            ) = job
            group = name
            run = args.out / "arms" / name / f"replica-{replica}"
            options = str(args.n)
            selector_hash = None
            if selector is not None:
                selector_path = (
                    args.out / "checkpoints" / group / "selector.txt"
                )
                options += f" {selector_path.resolve()}"
                selector_hash = common.sha256_file(selector_path)
            command = common.restore_command(
                artifacts["gem5"],
                config,
                run / "gem5",
                checkpoint,
                artifacts[binary],
                options,
                profile,
                artifacts["ramulator_config"],
                args.mem_channels,
                args.l3_ports,
                gem5_args,
            )
            if common.tree_identity(checkpoint) != checkpoints[group]["tree"]:
                raise RuntimeError(
                    f"checkpoint {group} changed before restore"
                )
            rc = common.run_logged(command, run / "restore.log", environment)
            if rc != 0:
                raise RuntimeError(
                    f"{name} replica {replica} failed with rc={rc}"
                )
            if common.tree_identity(checkpoint) != checkpoints[group]["tree"]:
                raise RuntimeError(
                    f"checkpoint {group} changed during restore"
                )
            if (
                selector is not None
                and common.sha256_file(selector_path) != selector_hash
            ):
                raise RuntimeError(f"{name} selector changed during restore")
            return {
                "arm": name,
                "replica": replica,
                "checkpoint_group": group,
                "selector": selector,
                "selector_sha256": selector_hash,
                "gem5_args": gem5_args,
                "command_path": str((run / "restore.command.json").resolve()),
                "command_sha256": common.sha256_file(
                    run / "restore.command.json"
                ),
            }

        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            manifest["runs"] = list(executor.map(restore, restore_jobs))
        common.atomic_json(args.out / "manifest.json", manifest)

        analyzer = (
            ROOT / "experiments/analysis/analyze_gzp_soa_jit_correctness.py"
        )
        result = subprocess.run([sys.executable, str(analyzer), str(args.out)])
        if result.returncode != 0:
            raise RuntimeError("GZP correctness analysis failed")
    except (OSError, RuntimeError) as error:
        common.atomic_text(args.out / "campaign.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1

    common.atomic_text(args.out / "campaign.exit", "0\n")
    common.atomic_text(args.out / "campaign.complete", common.utc_now() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
