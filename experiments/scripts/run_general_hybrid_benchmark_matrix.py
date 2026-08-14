#!/usr/bin/env python3
"""Run provenance-frozen native16/native4/general-hybrid comparisons.

Hybrid arms restore one deferred-treatment checkpoint.  The standard arms are
the full-generation ordinary stream control, page-gated ordinary stream
control, and the token-bound one-page STREAM_LD correctness control.  An API
microbenchmark may additionally request the two-alternating-page control.
Future materializers are passed explicitly with --future-arm; the runner does
not relabel token-bound STREAM_LD as the final optimized treatment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/deprecated/example/se.py"
RAMULATOR_CONFIG = ROOT / "ext/ramulator2/ramulator2/example_gem5_config.yaml"

PROFILE = {
    "native16": {
        "logical": 16384,
        "physical": 16384,
        "row_slices": 16,
        "row_rows": 64,
        "row_entries": 8,
        "offset_entries": 16384,
        "offset_epoch_entries": 16384,
    },
    "native4": {
        "logical": 4096,
        "physical": 4096,
        "row_slices": 16,
        "row_rows": 64,
        "row_entries": 8,
        "offset_entries": 4096,
        "offset_epoch_entries": 4096,
    },
    "hybrid": {
        "logical": 16384,
        "physical": 4096,
        "row_slices": 16,
        "row_rows": 64,
        "row_entries": 8,
        "offset_entries": 16384,
        "offset_epoch_entries": 16384,
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_stable_artifact(source: Path, destination: Path) -> str:
    """Copy an immutable experiment input, rejecting concurrent rewrites."""
    before = sha256_file(source)
    shutil.copy2(source, destination)
    after = sha256_file(source)
    frozen = sha256_file(destination)
    if before != after or after != frozen:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"artifact changed while being frozen: {source}")
    return frozen


def atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def atomic_json(path: Path, value: object) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def selector_payload(workload: str, selector: str) -> str:
    if workload == "api":
        api_modes = {
            "stream_control": "paged",
            "page_gated": "paged_overlap",
            "token_stream_ld": "token_stream_ld",
            "token_stream_ld_page0_prearm": "token_stream_ld_page0_prearm",
            "token_stream_ld_pingpong": "token_stream_ld_pingpong",
        }
        return f"{api_modes.get(selector, selector)} 4096"
    return selector


def parse_future_arm(value: str) -> dict[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("future arm must be NAME=SELECTOR")
    name, selector = value.split("=", 1)
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
    if not name or any(character not in allowed for character in name):
        raise argparse.ArgumentTypeError("future arm name is not path-safe")
    if not selector or "\n" in selector:
        raise argparse.ArgumentTypeError("future arm selector is empty")
    return {"name": name, "selector": selector}


def make_arms(
    workload: str,
    has_hybrid: bool,
    pingpong: bool,
    future: list[dict[str, str]],
    page0_prearm: bool = False,
) -> list[dict[str, object]]:
    if has_hybrid and workload in ("gapbs-pr", "gapbs-bfs"):
        raise ValueError(
            "GAPBS has exact native16/native4 controls but no wired general "
            "hybrid consumer"
        )
    arms: list[dict[str, object]] = [
        {
            "name": "native16",
            "profile": "native16",
            "binary": "native16",
            "checkpoint_group": "native16",
            "selector": None,
            "role": "native_control",
        },
        {
            "name": "native4",
            "profile": "native4",
            "binary": "native4",
            "checkpoint_group": "native4",
            "selector": None,
            "role": "native_control",
        },
    ]
    if not has_hybrid:
        if pingpong or page0_prearm or future:
            raise ValueError("hybrid-only arms require --hybrid")
        return arms
    for selector, role in (
        ("stream_control", "ordinary_stream_control"),
        ("page_gated", "page_gated_stream_control"),
        ("token_stream_ld", "token_stream_ld_correctness_control"),
    ):
        arms.append(
            {
                "name": f"hybrid_{selector}",
                "profile": "hybrid",
                "binary": "hybrid",
                "checkpoint_group": "hybrid",
                "selector": selector_payload(workload, selector),
                "role": role,
            }
        )
    if page0_prearm:
        if workload != "api":
            raise ValueError(
                "the page-zero prearm guest is supported only by the API "
                "microbenchmark"
            )
        selector = "token_stream_ld_page0_prearm"
        arms.append(
            {
                "name": f"hybrid_{selector}",
                "profile": "hybrid",
                "binary": "hybrid",
                "checkpoint_group": "hybrid",
                "selector": selector_payload(workload, selector),
                "role": "token_stream_ld_page0_prearm_correctness_control",
            }
        )
    if pingpong:
        if workload != "api":
            raise ValueError(
                "the current two-alternating-page API is supported only by "
                "the API microbenchmark"
            )
        selector = "token_stream_ld_pingpong"
        arms.append(
            {
                "name": f"hybrid_{selector}",
                "profile": "hybrid",
                "binary": "hybrid",
                "checkpoint_group": "hybrid",
                "selector": selector_payload(workload, selector),
                "role": "token_stream_ld_two_page_correctness_control",
            }
        )
    existing = {str(arm["name"]) for arm in arms}
    for item in future:
        name = item["name"]
        if name in existing:
            raise ValueError(f"duplicate arm name: {name}")
        existing.add(name)
        arms.append(
            {
                "name": name,
                "profile": "hybrid",
                "binary": "hybrid",
                "checkpoint_group": "hybrid",
                "selector": selector_payload(workload, item["selector"]),
                "role": "future_explicit_treatment",
            }
        )
    return arms


def profile_args(profile_name: str) -> list[str]:
    profile = PROFILE[profile_name]
    return [
        "--maa",
        f"--maa_num_tile_elements={profile['logical']}",
        f"--maa_physical_tile_elements={profile['physical']}",
        f"--maa_num_initial_row_table_slices={profile['row_slices']}",
        f"--maa_num_row_table_rows_per_slice={profile['row_rows']}",
        "--maa_num_row_table_entries_per_subslice_row="
        f"{profile['row_entries']}",
        f"--maa_num_offset_table_entries={profile['offset_entries']}",
        "--maa_num_offset_table_epoch_entries="
        f"{profile['offset_epoch_entries']}",
    ]


def common_restore_args(
    ramulator_config: Path, mem_channels: int, l3_ports: int = 4
) -> list[str]:
    return [
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
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
        f"--l3_ports={l3_ports}",
        "--cacheline_size=64",
        "--mem-type",
        "Ramulator2",
        "--ramulator-config",
        str(ramulator_config),
        "--mem-channels",
        str(mem_channels),
        "--maa_virtual_grow_order",
        "--maa_virtual_index_force_cache",
        "--maa_virtual_index_buffer_lines=128",
        "--maa_virtual_combine_slots=384",
        "--maa_virtual_combine_words=4096",
        "--maa_virtual_combine_ways=4",
        "--maa_virtual_response_slots=96",
        "--maa_virtual_response_word_pool=480",
        "--maa_virtual_words_per_cycle=4",
        "--maa_virtual_max_outstanding_writes=64",
        "--maa_virtual_masked_writes",
        "--maa_direct_retirement_line_handoff",
    ]


def render_options(
    template: str, selector: Path | None, inputs: list[Path] | None = None
) -> str:
    has_placeholder = "{selector}" in template
    if selector is None and has_placeholder:
        raise ValueError("native options must not contain {selector}")
    if selector is not None and not has_placeholder:
        raise ValueError("hybrid options must contain {selector}")
    values = {"selector": str(selector)}
    for index, path in enumerate(inputs or []):
        values[f"input{index}"] = str(path)
    try:
        return template.format(**values)
    except KeyError as error:
        raise ValueError(
            f"unknown options placeholder: {error.args[0]}"
        ) from error


def checkpoint_command(
    gem5: Path,
    config: Path,
    outdir: Path,
    binary: Path,
    options: str,
) -> list[str]:
    return [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={outdir}",
        "--debug-flags=MAAVirtualTrace",
        "--debug-file=virtual_trace.log",
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
        options,
    ]


def restore_command(
    gem5: Path,
    config: Path,
    outdir: Path,
    checkpoint: Path,
    binary: Path,
    options: str,
    profile: str,
    ramulator_config: Path,
    mem_channels: int,
    l3_ports: int,
    extra: list[str],
) -> list[str]:
    return [
        str(gem5),
        "--listener-mode=off",
        f"--outdir={outdir}",
        "--debug-flags=MAAVirtualTrace",
        "--debug-file=virtual_trace.log",
        str(config),
        *common_restore_args(ramulator_config, mem_channels, l3_ports),
        f"--checkpoint-dir={checkpoint}",
        *profile_args(profile),
        *extra,
        "--cmd",
        str(binary),
        "--options",
        options,
    ]


def tree_identity(path: Path) -> dict[str, object]:
    files: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if item.is_file():
            files[str(item.relative_to(path))] = sha256_file(item)
    if not files:
        raise RuntimeError(f"checkpoint contains no files: {path}")
    digest = hashlib.sha256()
    for name, value in files.items():
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(value.encode())
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "files": files}


def freeze_config_tree(
    config: Path, repository_config_root: Path, destination: Path
) -> tuple[Path, dict[str, object]]:
    """Freeze the config with the sibling modules its imports require."""
    source = config.resolve()
    config_root = repository_config_root.resolve()
    try:
        relative = source.relative_to(config_root)
    except ValueError:
        # Preserve at least the immediate sibling modules of a custom config.
        config_root = source.parent
        relative = Path(source.name)
    shutil.copytree(config_root, destination, symlinks=True)
    frozen_config = destination / relative
    if not frozen_config.is_file():
        raise RuntimeError("frozen gem5 config is missing")
    return frozen_config.resolve(), tree_identity(destination)


def run_logged(
    command: list[str], log: Path, environment: dict[str, str]
) -> int:
    atomic_json(log.with_suffix(".command.json"), command)
    atomic_text(log.with_suffix(".command.txt"), shlex.join(command) + "\n")
    with log.open("wb") as output:
        result = subprocess.run(
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=environment,
            check=False,
        )
    atomic_text(log.with_suffix(".exit"), f"{result.returncode}\n")
    return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workload",
        required=True,
        choices=(
            "api",
            "cg",
            "ume-gzp",
            "ume-gzz",
            "gapbs-pr",
            "gapbs-bfs",
            "xrage",
        ),
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--gem5", required=True, type=Path)
    parser.add_argument("--ramulator-library", required=True, type=Path)
    parser.add_argument(
        "--ramulator-config", type=Path, default=RAMULATOR_CONFIG
    )
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--native16", required=True, type=Path)
    parser.add_argument("--native4", required=True, type=Path)
    parser.add_argument("--hybrid", type=Path)
    parser.add_argument("--native16-options", default="")
    parser.add_argument("--native4-options", default="")
    parser.add_argument("--hybrid-options", default="")
    parser.add_argument(
        "--workload-input", action="append", type=Path, default=[]
    )
    parser.add_argument("--replicas", type=int, default=1)
    parser.add_argument("--mem-channels", type=int, default=2)
    parser.add_argument(
        "--l3-ports",
        type=int,
        default=4,
        help=(
            "LLC acceptance ports (default: 4); use a separate campaign for "
            "each sensitivity point"
        ),
    )
    parser.add_argument("--pingpong", action="store_true")
    parser.add_argument("--page0-prearm", action="store_true")
    parser.add_argument(
        "--future-arm", action="append", default=[], type=parse_future_arm
    )
    parser.add_argument("--extra-gem5-arg", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.replicas < 1 or args.mem_channels < 1:
        parser.error("replicas and memory channels must be positive")
    if not 1 <= args.l3_ports <= 16:
        parser.error("--l3-ports must be in [1, 16]")
    if args.hybrid and "{selector}" not in args.hybrid_options:
        parser.error("--hybrid-options must contain {selector}")
    if not args.hybrid and args.hybrid_options:
        parser.error("--hybrid-options requires --hybrid")
    for extra in args.extra_gem5_arg:
        if not extra.startswith("--") or any(c.isspace() for c in extra):
            parser.error("each --extra-gem5-arg must be one option token")
    return args


def require_inputs(args: argparse.Namespace) -> None:
    paths = [
        args.gem5,
        args.ramulator_library,
        args.ramulator_config,
        args.config,
        args.native16,
        args.native4,
        *args.workload_input,
    ]
    if args.hybrid:
        paths.append(args.hybrid)
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError("missing input artifacts: " + ", ".join(missing))


def main() -> int:
    args = parse_args()
    try:
        arms = make_arms(
            args.workload,
            args.hybrid is not None,
            args.pingpong,
            args.future_arm,
            args.page0_prearm,
        )
        require_inputs(args)
        render_options(args.native16_options, None, args.workload_input)
        render_options(args.native4_options, None, args.workload_input)
        if args.hybrid:
            render_options(
                args.hybrid_options,
                args.out / "treatment.txt",
                args.workload_input,
            )
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if not args.execute:
        plan = {
            "schema": "dx100.general_hybrid_plan.v1",
            "workload": args.workload,
            "profiles": PROFILE,
            "arms": arms,
            "replicas": args.replicas,
            "l3_ports": args.l3_ports,
            "note": (
                "token_stream_ld arms are correctness controls; explicit "
                "future arms are not assumed equivalent"
            ),
        }
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    source_status = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if source_status:
        print(
            "error: evidence execution requires a clean worktree",
            file=sys.stderr,
        )
        print(source_status, file=sys.stderr, end="")
        return 1
    if args.out.exists():
        print(f"error: refusing to overwrite {args.out}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True)
    atomic_text(args.out / "campaign.exit", "running\n")
    frozen = args.out / "inputs"
    frozen.mkdir()
    source_artifacts: dict[str, Path] = {
        "gem5": args.gem5,
        "ramulator_library": args.ramulator_library,
        "ramulator_config": args.ramulator_config,
        "native16": args.native16,
        "native4": args.native4,
    }
    if args.hybrid:
        source_artifacts["hybrid"] = args.hybrid
    for index, path in enumerate(args.workload_input):
        source_artifacts[f"workload_input_{index}"] = path

    frozen_artifacts: dict[str, Path] = {}
    fixed_names = {
        "gem5": "gem5.opt",
        "ramulator_library": "libramulator.so",
        "ramulator_config": "ramulator.yaml",
    }
    frozen_hashes: dict[str, str] = {}
    for name, source in source_artifacts.items():
        destination = frozen / fixed_names.get(name, name + source.suffix)
        frozen_hashes[name] = copy_stable_artifact(
            source.resolve(), destination
        )
        frozen_artifacts[name] = destination.resolve()
    frozen_config, config_tree_identity = freeze_config_tree(
        args.config, ROOT / "configs", frozen / "configs"
    )
    frozen_artifacts["config"] = frozen_config
    for key in ("gem5", "native16", "native4", "hybrid"):
        if key in frozen_artifacts:
            frozen_artifacts[key].chmod(0o555)

    library_dir = str(frozen.resolve())
    environment = os.environ.copy()
    environment["LD_LIBRARY_PATH"] = library_dir + (
        ":" + environment["LD_LIBRARY_PATH"]
        if environment.get("LD_LIBRARY_PATH")
        else ""
    )
    environment["OMP_NUM_THREADS"] = "4"
    environment["OMP_PROC_BIND"] = "false"
    ldd = subprocess.run(
        ["ldd", str(frozen_artifacts["gem5"])],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    atomic_text(frozen / "gem5.ldd.txt", ldd.stdout + ldd.stderr)
    expected_library = str(frozen_artifacts["ramulator_library"])
    if ldd.returncode != 0 or expected_library not in ldd.stdout:
        atomic_text(args.out / "campaign.exit", "1\n")
        print(
            "error: frozen gem5 did not resolve frozen Ramulator",
            file=sys.stderr,
        )
        return 1

    selector = (args.out / "treatment.txt").resolve()
    frozen_inputs = [
        frozen_artifacts[f"workload_input_{index}"]
        for index in range(len(args.workload_input))
    ]
    options = {
        "native16": render_options(args.native16_options, None, frozen_inputs),
        "native4": render_options(args.native4_options, None, frozen_inputs),
        "hybrid": render_options(args.hybrid_options, selector, frozen_inputs)
        if args.hybrid
        else "",
    }
    artifact_identity = {
        name: {
            "path": str(path),
            "sha256": frozen_hashes.get(name, sha256_file(path)),
        }
        for name, path in frozen_artifacts.items()
    }
    artifact_identity["config_tree"] = {
        "path": str((frozen / "configs").resolve()),
        **config_tree_identity,
    }
    manifest: dict[str, object] = {
        "schema": "dx100.general_hybrid_matrix.v1",
        "created_utc": utc_now(),
        "workload": args.workload,
        "source_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "source_status": "clean",
        "profiles": PROFILE,
        "arms": arms,
        "replicas": args.replicas,
        "mem_channels": args.mem_channels,
        "l3_ports": args.l3_ports,
        "options": options,
        "selector_path": str(selector) if args.hybrid else None,
        "artifacts": artifact_identity,
        "extra_gem5_args": args.extra_gem5_arg,
        "interpretation": {
            "token_stream_ld": "one-page correctness control",
            "token_stream_ld_page0_prearm": (
                "page-zero prearm correctness control; dormant until its "
                "exact virtual producer registers"
            ),
            "token_stream_ld_pingpong": (
                "optional two-alternating-page correctness control"
            ),
            "ordinary_stream_store_contention": "must be reported",
            "future_arm": "explicit treatment, never inferred from control",
        },
    }
    atomic_json(args.out / "manifest.json", manifest)

    binaries = {
        key: frozen_artifacts[key]
        for key in ("native16", "native4", "hybrid")
        if key in frozen_artifacts
    }
    frozen_config = frozen_artifacts["config"]
    frozen_ramulator_config = frozen_artifacts["ramulator_config"]
    checkpoint_identity: dict[str, dict[str, str]] = {}
    groups: dict[str, dict[str, object]] = {}
    for arm in arms:
        groups.setdefault(str(arm["checkpoint_group"]), arm)

    try:
        for group, arm in groups.items():
            group_dir = args.out / "checkpoints" / group
            group_dir.mkdir(parents=True)
            binary_key = str(arm["binary"])
            command = checkpoint_command(
                frozen_artifacts["gem5"],
                frozen_config,
                group_dir / "gem5",
                binaries[binary_key],
                options[binary_key],
            )
            rc = run_logged(command, group_dir / "checkpoint.log", environment)
            if rc != 0:
                raise RuntimeError(f"checkpoint {group} failed with rc={rc}")
            checkpoint_identity[group] = tree_identity(group_dir / "gem5")
            atomic_json(
                group_dir / "identity.json", checkpoint_identity[group]
            )

        for arm in arms:
            arm_name = str(arm["name"])
            binary_key = str(arm["binary"])
            group = str(arm["checkpoint_group"])
            if arm["selector"] is not None:
                atomic_text(selector, str(arm["selector"]) + "\n")
            for replica in range(1, args.replicas + 1):
                run_dir = args.out / "arms" / arm_name / f"replica-{replica}"
                run_dir.mkdir(parents=True)
                if arm["selector"] is not None:
                    atomic_text(
                        run_dir / "treatment.txt", selector.read_text()
                    )
                command = restore_command(
                    frozen_artifacts["gem5"],
                    frozen_config,
                    run_dir / "gem5",
                    args.out / "checkpoints" / group / "gem5",
                    binaries[binary_key],
                    options[binary_key],
                    str(arm["profile"]),
                    frozen_ramulator_config,
                    args.mem_channels,
                    args.l3_ports,
                    args.extra_gem5_arg,
                )
                rc = run_logged(command, run_dir / "restore.log", environment)
                if rc != 0:
                    raise RuntimeError(
                        f"restore {arm_name} replica {replica} failed with rc={rc}"
                    )
                after = tree_identity(
                    args.out / "checkpoints" / group / "gem5"
                )
                if after["sha256"] != checkpoint_identity[group]["sha256"]:
                    raise RuntimeError(
                        f"checkpoint {group} mutated during restore"
                    )
        manifest["checkpoint_identity"] = checkpoint_identity
        atomic_json(args.out / "manifest.json", manifest)
        analyzer = ROOT / (
            "experiments/analysis/analyze_general_hybrid_benchmark_matrix.py"
        )
        result = subprocess.run(
            [sys.executable, str(analyzer), str(args.out)], check=False
        )
        if result.returncode != 0:
            raise RuntimeError("post-run evidence validation failed")
    except RuntimeError as error:
        atomic_text(args.out / "campaign.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1

    atomic_text(args.out / "campaign.exit", "0\n")
    atomic_text(args.out / "campaign.complete", utc_now() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
