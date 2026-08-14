#!/usr/bin/env python3
"""Replay one new gem5 treatment from a completed hybrid matrix checkpoint.

This is deliberately narrower than ``run_general_hybrid_benchmark_matrix.py``:
it never creates a checkpoint or runs a matrix.  It accepts a completed source
matrix, binds one existing control arm to its checkpoint group, and executes
one differently named restore with the source arm's frozen guest/options/
selector/profile.  The only permitted experiment deltas are the candidate
gem5 binary, candidate config tree, and explicitly supplied non-conflicting
sole-arm gem5 options.

The runner is fail-closed.  In particular, it refuses source roots that are
incomplete or whose checkpoint/artifact identities no longer match their
manifest.  A candidate result is reported only after the source checkpoint is
identical both before and after restore and the candidate has the same exact
workload certificate as the selected source control.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ANALYZER_PATH = (
    ROOT / "experiments/analysis/analyze_general_hybrid_benchmark_matrix.py"
)
MATRIX_SCHEMA = "dx100.general_hybrid_matrix.v1"
REPLAY_SCHEMA = "dx100.general_hybrid_single_treatment_replay.v1"
REPORT_SCHEMA = "dx100.general_hybrid_single_treatment_report.v1"
SUPPORTED_WORKLOADS = frozenset(("api", "cg", "ume-gzp", "ume-gzz"))
SAFE_NAME = re.compile(r"[a-z][a-z0-9_-]*\Z")

# These are the only replay-safe profiles.  The selected source arm must have
# exactly this geometry; the source manifest's arbitrary profile payload is
# never allowed to choose restore geometry for a new candidate.
PROFILE = {
    "hybrid": {
        "logical": 16384,
        "physical": 4096,
        "row_slices": 16,
        "row_rows": 64,
        "row_entries": 8,
        "offset_entries": 16384,
        "offset_epoch_entries": 16384,
    }
}

# The candidate may add a treatment knob, but may not replace a restored
# checkpoint, guest, selector, profile, memory system, debug evidence, or an
# inherited source option.  One option token is intentionally the only shape
# accepted by the CLI.
PROTECTED_OPTION_KEYS = frozenset(
    (
        "--outdir",
        "--checkpoint-dir",
        "--cmd",
        "--options",
        "--ramulator-config",
        "--cpu-type",
        "--mem-size",
        "--sys-clock",
        "--cpu-clock",
        "--mem-channels",
        "--l3-ports",
        "--cacheline_size",
        "--mem-type",
        "--debug-flags",
        "--debug-file",
        "--listener-mode",
        "--maa",
        "--maa_num_tile_elements",
        "--maa_physical_tile_elements",
        "--maa_num_initial_row_table_slices",
        "--maa_num_row_table_rows_per_slice",
        "--maa_num_row_table_entries_per_subslice_row",
        "--maa_num_offset_table_entries",
        "--maa_num_offset_table_epoch_entries",
    )
)


def load_analyzer() -> Any:
    spec = importlib.util.spec_from_file_location(
        "general_hybrid_analyzer", ANALYZER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the general-hybrid analyzer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYZER = load_analyzer()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_text(path: Path, content: str, mode: int | None = None) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
    if mode is not None:
        path.chmod(mode)


def atomic_json(path: Path, value: object, immutable: bool = False) -> None:
    atomic_text(
        path,
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        0o444 if immutable else None,
    )


def require_safe_name(value: str, label: str) -> str:
    if not SAFE_NAME.fullmatch(value):
        raise ValueError(f"{label} is not path-safe")
    return value


def option_key(value: str) -> str:
    return value.split("=", 1)[0]


def parse_sole_arm_gem5_arg(value: str) -> str:
    if not value.startswith("--") or any(
        character.isspace() for character in value
    ):
        raise argparse.ArgumentTypeError(
            "sole-arm gem5 arg must be one --option token"
        )
    if option_key(value) in PROTECTED_OPTION_KEYS:
        raise argparse.ArgumentTypeError(
            "sole-arm gem5 arg overrides a frozen replay invariant"
        )
    return value


def require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError(f"{label} is not a regular file: {path}")
    return resolved


def inside(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} escapes source matrix root") from error
    return resolved


def tree_identity(path: Path) -> dict[str, object]:
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"checkpoint is not a regular directory: {path}")
    files: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"checkpoint contains symlink: {item}")
        if item.is_file():
            files[str(item.relative_to(path))] = sha256_file(item)
        elif not item.is_dir():
            raise ValueError(f"checkpoint contains non-regular entry: {item}")
    if not files:
        raise ValueError(f"checkpoint contains no files: {path}")
    digest = hashlib.sha256()
    for name, value in files.items():
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(value.encode())
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "files": files}


def read_exit(path: Path, label: str) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        raise ValueError(f"invalid {label} exit marker: {path}") from error


def regular_text(path: Path, label: str) -> str:
    resolved = require_file(path, label)
    return resolved.read_text(encoding="utf-8", errors="replace")


def source_artifact(
    source_root: Path, manifest: dict[str, object], name: str
) -> tuple[Path, str]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("source matrix has no artifact identity map")
    record = artifacts.get(name)
    if not isinstance(record, dict):
        raise ValueError(f"source matrix has no {name} artifact")
    raw_path = record.get("path")
    expected = record.get("sha256")
    if not isinstance(raw_path, str) or not isinstance(expected, str):
        raise ValueError(f"source {name} artifact identity is malformed")
    path = inside(
        require_file(Path(raw_path), f"source {name} artifact"),
        source_root,
        name,
    )
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"source {name} artifact hash mismatch")
    return path, actual


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
    ramulator_config: Path, mem_channels: int, l3_ports: int
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


def checked_source_options(
    manifest: dict[str, object],
    binary_name: str,
    selector_path: Path,
    destination: Path,
) -> str:
    options = manifest.get("options")
    if not isinstance(options, dict) or not isinstance(
        options.get(binary_name), str
    ):
        raise ValueError("source control has no frozen options string")
    try:
        tokens = shlex.split(str(options[binary_name]))
    except ValueError as error:
        raise ValueError("source options are not shell-tokenizable") from error
    matches = [
        index
        for index, token in enumerate(tokens)
        if token == str(selector_path)
    ]
    if len(matches) != 1:
        raise ValueError(
            "source options do not contain exactly one selector path"
        )
    tokens[matches[0]] = str(destination)
    return shlex.join(tokens)


def safe_source_args(
    manifest: dict[str, object], control_name: str
) -> list[str]:
    values = manifest.get("extra_gem5_args", [])
    mapping = manifest.get("restore_arm_gem5_args", {})
    if not isinstance(values, list) or not isinstance(mapping, dict):
        raise ValueError("source restore argument map is malformed")
    selected = mapping.get(control_name, [])
    if not isinstance(selected, list):
        raise ValueError("selected source restore arguments are malformed")
    result: list[str] = []
    for value in [*values, *selected]:
        if not isinstance(value, str):
            raise ValueError("source restore argument is not a string")
        result.append(parse_sole_arm_gem5_arg(value))
    return result


def freeze_file(source: Path, destination: Path) -> str:
    before = sha256_file(source)
    shutil.copy2(source, destination)
    after = sha256_file(source)
    frozen = sha256_file(destination)
    if before != after or after != frozen:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"artifact changed while being frozen: {source}")
    return frozen


def freeze_config_tree(
    config: Path, destination: Path
) -> tuple[Path, dict[str, object]]:
    source = require_file(config, "candidate config")
    repository_configs = (ROOT / "configs").resolve()
    try:
        relative = source.relative_to(repository_configs)
        source_root = repository_configs
    except ValueError:
        source_root = source.parent
        relative = Path(source.name)
    try:
        destination.resolve().relative_to(source_root)
    except ValueError:
        pass
    else:
        raise ValueError(
            "candidate config freeze destination is inside its source tree"
        )
    before = tree_identity_for_regular_tree(
        source_root, "candidate config tree"
    )
    shutil.copytree(source_root, destination, symlinks=False)
    after = tree_identity_for_regular_tree(
        source_root, "candidate config tree"
    )
    frozen = tree_identity_for_regular_tree(
        destination, "frozen candidate config tree"
    )
    if before != after or after != frozen:
        raise RuntimeError("candidate config tree changed while being frozen")
    frozen_config = (destination / relative).resolve()
    if not frozen_config.is_file():
        raise RuntimeError("frozen candidate config is missing")
    return frozen_config, frozen


def tree_identity_for_regular_tree(
    path: Path, label: str
) -> dict[str, object]:
    if not path.is_dir() or path.is_symlink():
        raise ValueError(f"{label} is not a regular directory")
    files: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise ValueError(f"{label} contains symlink: {item}")
        if item.is_file():
            files[str(item.relative_to(path))] = sha256_file(item)
        elif not item.is_dir():
            raise ValueError(f"{label} contains non-regular entry: {item}")
    if not files:
        raise ValueError(f"{label} contains no files")
    digest = hashlib.sha256()
    for name, value in files.items():
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(value.encode())
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "files": files}


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


def validate_terminal_run(
    run_dir: Path, workload: str, selector: str | None, label: str
) -> dict[str, object]:
    if read_exit(run_dir / "restore.exit", label) != 0:
        raise ValueError(f"{label}: nonzero restore exit")
    log = regular_text(run_dir / "restore.log", f"{label} restore log")
    if ANALYZER.FATAL.search(log):
        raise ValueError(f"{label}: fatal text in restore log")
    if len(ANALYZER.EXIT.findall(log)) != 1:
        raise ValueError(f"{label}: expected exactly one terminal m5_exit")
    if selector is not None:
        observed = regular_text(
            run_dir / "treatment.txt", f"{label} treatment selector"
        ).strip()
        if observed != selector:
            raise ValueError(f"{label}: selector mismatch")
        mode = selector.split()[0]
        if f"mode={mode}" not in log:
            raise ValueError(f"{label}: restored selector marker is missing")
    key, certificate = ANALYZER.correctness(workload, log)
    stats = ANALYZER.first_stats(run_dir / "gem5" / "stats.txt")
    return {
        "correctness_key": key,
        "certificate": certificate,
        "first_roi_simTicks": int(stats["simTicks"]),
        "restore_log_sha256": sha256_file(run_dir / "restore.log"),
        "stats_sha256": sha256_file(run_dir / "gem5" / "stats.txt"),
    }


def source_checkpoint(
    source_root: Path, manifest: dict[str, object], group: str
) -> tuple[Path, dict[str, object]]:
    all_identities = manifest.get("checkpoint_identity")
    if not isinstance(all_identities, dict) or not isinstance(
        all_identities.get(group), dict
    ):
        raise ValueError("source matrix has no selected checkpoint identity")
    checkpoint = inside(
        source_root / "checkpoints" / group / "gem5", source_root, "checkpoint"
    )
    actual = tree_identity(checkpoint)
    expected = all_identities[group]
    if actual != expected:
        raise ValueError("source checkpoint identity mismatch")
    return checkpoint, actual


def source_manifest(source_root: Path) -> tuple[dict[str, object], str]:
    if read_exit(source_root / "campaign.exit", "source campaign") != 0:
        raise ValueError("source matrix did not complete successfully")
    complete = regular_text(
        source_root / "campaign.complete", "source completion marker"
    ).strip()
    if not complete:
        raise ValueError("source matrix completion marker is empty")
    manifest_path = require_file(
        source_root / "manifest.json", "source manifest"
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("source manifest is not valid JSON") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != MATRIX_SCHEMA
    ):
        raise ValueError("unsupported or missing source matrix manifest")
    if manifest.get("source_status") != "clean":
        raise ValueError("source matrix was not created from a clean source")
    return manifest, sha256_file(manifest_path)


def selected_source(
    source_root: Path,
    manifest: dict[str, object],
    checkpoint_group: str,
    control_arm: str,
) -> dict[str, object]:
    workload = manifest.get("workload")
    if workload not in SUPPORTED_WORKLOADS:
        raise ValueError(
            f"unsupported workload for checkpoint replay: {workload}"
        )
    replicas = manifest.get("replicas")
    if not isinstance(replicas, int) or replicas < 1:
        raise ValueError("source matrix replica contract is invalid")
    arms = manifest.get("arms")
    if not isinstance(arms, list):
        raise ValueError("source matrix arms are malformed")
    names = [arm.get("name") for arm in arms if isinstance(arm, dict)]
    if len(names) != len(arms) or len(set(names)) != len(names):
        raise ValueError("source matrix arm names are invalid or duplicate")
    required = {
        "native16",
        "native4",
        "hybrid_stream_control",
        "hybrid_page_gated",
        "hybrid_token_stream_ld",
    }
    if not required.issubset(set(names)):
        raise ValueError(
            "source matrix is incomplete: required arms are missing"
        )
    matches = [
        arm
        for arm in arms
        if isinstance(arm, dict) and arm.get("name") == control_arm
    ]
    if len(matches) != 1:
        raise ValueError("selected source control arm is missing or ambiguous")
    control = matches[0]
    if control.get("checkpoint_group") != checkpoint_group:
        raise ValueError("selected source control/checkpoint group mismatch")
    role = control.get("role")
    if not isinstance(role, str) or "control" not in role:
        raise ValueError("selected source arm is not a control")
    profile_name = control.get("profile")
    profiles = manifest.get("profiles")
    if (
        profile_name not in PROFILE
        or not isinstance(profiles, dict)
        or profiles.get(profile_name) != PROFILE[profile_name]
    ):
        raise ValueError("selected source profile mismatch")
    binary_name = control.get("binary")
    selector = control.get("selector")
    if (
        not isinstance(binary_name, str)
        or not isinstance(selector, str)
        or not selector.strip()
    ):
        raise ValueError("selected source arm lacks frozen binary or selector")
    selector_path = manifest.get("selector_path")
    if not isinstance(selector_path, str):
        raise ValueError("source matrix selector path is missing")
    selector_path_checked = inside(
        require_file(Path(selector_path), "source selector path"),
        source_root,
        "selector",
    )

    # Validate every declared run, not merely the chosen control.  A success
    # marker is not accepted as proof that the source matrix is complete.
    all_keys: set[str] = set()
    control_records: list[dict[str, object]] = []
    for arm in arms:
        assert isinstance(arm, dict)
        arm_name = str(arm["name"])
        arm_selector = arm.get("selector")
        if arm_selector is not None and not isinstance(arm_selector, str):
            raise ValueError(f"{arm_name}: source selector is malformed")
        for replica in range(1, replicas + 1):
            record = validate_terminal_run(
                source_root / "arms" / arm_name / f"replica-{replica}",
                str(workload),
                arm_selector,
                f"source {arm_name}/{replica}",
            )
            all_keys.add(str(record["correctness_key"]))
            if arm_name == control_arm:
                control_records.append(record)
    if len(all_keys) != 1:
        raise ValueError("source matrix exact correctness keys do not match")
    if not control_records:
        raise ValueError("selected source control has no complete replica")
    control_keys = {
        str(record["correctness_key"]) for record in control_records
    }
    if len(control_keys) != 1:
        raise ValueError(
            "selected source control replicas do not exactly match"
        )

    checkpoint, identity = source_checkpoint(
        source_root, manifest, checkpoint_group
    )
    guest, guest_hash = source_artifact(source_root, manifest, binary_name)
    source_gem5, source_gem5_hash = source_artifact(
        source_root, manifest, "gem5"
    )
    ramulator_library, ramulator_library_hash = source_artifact(
        source_root, manifest, "ramulator_library"
    )
    ramulator_config, ramulator_config_hash = source_artifact(
        source_root, manifest, "ramulator_config"
    )
    mem_channels = manifest.get("mem_channels")
    l3_ports = manifest.get("l3_ports")
    if not isinstance(mem_channels, int) or mem_channels < 1:
        raise ValueError("source matrix memory-channel contract is invalid")
    if not isinstance(l3_ports, int) or not 1 <= l3_ports <= 16:
        raise ValueError("source matrix LLC-port contract is invalid")
    return {
        "workload": str(workload),
        "replicas": replicas,
        "control": control,
        "profile": str(profile_name),
        "binary_name": binary_name,
        "selector": selector,
        "selector_path": selector_path_checked,
        "checkpoint": checkpoint,
        "checkpoint_identity": identity,
        "control_records": control_records,
        "source_control_key": next(iter(control_keys)),
        "guest": guest,
        "guest_hash": guest_hash,
        "source_gem5": source_gem5,
        "source_gem5_hash": source_gem5_hash,
        "ramulator_library": ramulator_library,
        "ramulator_library_hash": ramulator_library_hash,
        "ramulator_config": ramulator_config,
        "ramulator_config_hash": ramulator_config_hash,
        "mem_channels": mem_channels,
        "l3_ports": l3_ports,
        "source_args": safe_source_args(manifest, control_arm),
    }


def source_clean() -> tuple[str, str]:
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise RuntimeError(
            "evidence execution requires a clean source worktree"
        )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return commit, "clean"


def require_nonconflicting_candidate_args(
    source_args: list[str], candidate_args: list[str]
) -> None:
    source_keys = {option_key(value) for value in source_args}
    candidate_keys = [option_key(value) for value in candidate_args]
    if len(candidate_keys) != len(set(candidate_keys)):
        raise ValueError("duplicate sole-arm gem5 option")
    overlap = sorted(source_keys.intersection(candidate_keys))
    if overlap:
        raise ValueError(
            "sole-arm gem5 option conflicts with frozen source option: "
            + ", ".join(overlap)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-matrix", required=True, type=Path)
    parser.add_argument(
        "--checkpoint-group",
        required=True,
        type=lambda value: require_safe_name(value, "checkpoint group"),
    )
    parser.add_argument(
        "--control-arm",
        required=True,
        type=lambda value: require_safe_name(value, "control arm"),
    )
    parser.add_argument(
        "--treatment-name",
        required=True,
        type=lambda value: require_safe_name(value, "treatment name"),
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--gem5", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--sole-arm-gem5-arg",
        action="append",
        default=[],
        type=parse_sole_arm_gem5_arg,
    )
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def immutable_plan(
    source_root: Path,
    manifest_hash: str,
    source: dict[str, object],
    treatment_name: str,
    candidate_gem5: Path,
    candidate_config: Path,
    candidate_args: list[str],
) -> dict[str, object]:
    return {
        "schema": REPLAY_SCHEMA,
        "source_root": str(source_root),
        "source_manifest_sha256": manifest_hash,
        "workload": source["workload"],
        "checkpoint_group": str(source["control"]["checkpoint_group"]),
        "control_arm": str(source["control"]["name"]),
        "treatment_name": treatment_name,
        "frozen_source_arm": {
            "profile": source["profile"],
            "binary": source["binary_name"],
            "selector": source["selector"],
            "options_reused": True,
        },
        "source_checkpoint_identity_sha256": source["checkpoint_identity"][
            "sha256"
        ],
        "source_control_exact_key": source["source_control_key"],
        "candidate_gem5": str(candidate_gem5),
        "candidate_config": str(candidate_config),
        "sole_arm_gem5_args": candidate_args,
        "execute": False,
    }


def execute(
    args: argparse.Namespace,
    source_root: Path,
    manifest: dict[str, object],
    manifest_hash: str,
    source: dict[str, object],
) -> None:
    if args.out.exists():
        raise ValueError(f"refusing to overwrite existing output: {args.out}")
    output = args.out.resolve()
    try:
        output.relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("output must be outside the source worktree")
    source_commit, source_status = source_clean()
    output.mkdir(parents=True)
    atomic_text(output / "campaign.exit", "running\n")
    try:
        inputs = output / "inputs"
        inputs.mkdir()
        candidate_gem5 = inputs / "candidate_gem5.opt"
        candidate_gem5_hash = freeze_file(
            require_file(args.gem5, "candidate gem5"), candidate_gem5
        )
        candidate_gem5.chmod(0o555)
        guest = inputs / "source_guest"
        guest_hash = freeze_file(Path(source["guest"]), guest)
        guest.chmod(0o555)
        ramulator_library = inputs / "libramulator.so"
        ramulator_library_hash = freeze_file(
            Path(source["ramulator_library"]), ramulator_library
        )
        ramulator_config = inputs / "ramulator.yaml"
        ramulator_config_hash = freeze_file(
            Path(source["ramulator_config"]), ramulator_config
        )
        candidate_config, candidate_config_identity = freeze_config_tree(
            args.config, inputs / "candidate-configs"
        )
        if (
            guest_hash != source["guest_hash"]
            or ramulator_library_hash != source["ramulator_library_hash"]
            or ramulator_config_hash != source["ramulator_config_hash"]
        ):
            raise RuntimeError(
                "source frozen input changed while staging replay"
            )

        environment = os.environ.copy()
        environment["LD_LIBRARY_PATH"] = str(inputs) + (
            ":" + environment["LD_LIBRARY_PATH"]
            if environment.get("LD_LIBRARY_PATH")
            else ""
        )
        environment["OMP_NUM_THREADS"] = "4"
        environment["OMP_PROC_BIND"] = "false"
        ldd = subprocess.run(
            ["ldd", str(candidate_gem5)],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        atomic_text(inputs / "candidate_gem5.ldd.txt", ldd.stdout + ldd.stderr)
        if ldd.returncode != 0 or str(ramulator_library) not in ldd.stdout:
            raise RuntimeError(
                "candidate gem5 did not resolve frozen source Ramulator"
            )

        run = output / "treatment" / args.treatment_name
        run.mkdir(parents=True)
        selector = run / "treatment.txt"
        atomic_text(selector, str(source["selector"]) + "\n")
        options = checked_source_options(
            manifest,
            str(source["binary_name"]),
            Path(source["selector_path"]),
            selector,
        )
        extra = [*source["source_args"], *args.sole_arm_gem5_arg]
        before = source_checkpoint(
            source_root, manifest, args.checkpoint_group
        )[1]
        command = restore_command(
            candidate_gem5,
            candidate_config,
            run / "gem5",
            Path(source["checkpoint"]),
            guest,
            options,
            str(source["profile"]),
            ramulator_config,
            int(source["mem_channels"]),
            int(source["l3_ports"]),
            extra,
        )
        rc = run_logged(command, run / "restore.log", environment)
        try:
            after = source_checkpoint(
                source_root, manifest, args.checkpoint_group
            )[1]
        except ValueError as error:
            raise RuntimeError(
                "source checkpoint mutated during restore"
            ) from error
        if after != before or after != source["checkpoint_identity"]:
            raise RuntimeError("source checkpoint mutated during restore")
        if rc != 0:
            raise RuntimeError(f"treatment restore failed with rc={rc}")
        treatment = validate_terminal_run(
            run,
            str(source["workload"]),
            str(source["selector"]),
            args.treatment_name,
        )
        if treatment["correctness_key"] != source["source_control_key"]:
            raise RuntimeError(
                "treatment exact correctness key differs from source control"
            )
        source_clean()
        _, post_manifest_hash = source_manifest(source_root)
        if post_manifest_hash != manifest_hash:
            raise RuntimeError("source matrix manifest mutated during replay")

        provenance = {
            "source_root": str(source_root),
            "source_manifest_sha256": manifest_hash,
            "source_commit": manifest.get("source_commit"),
            "source_status": manifest.get("source_status"),
            "checkpoint_group": args.checkpoint_group,
            "checkpoint_identity_before": before,
            "checkpoint_identity_after": after,
            "control_arm": source["control"],
            "control_records": source["control_records"],
        }
        binary_hashes = {
            "source_gem5_sha256": source["source_gem5_hash"],
            "candidate_gem5_sha256": candidate_gem5_hash,
            "source_guest_sha256": guest_hash,
        }
        report = {
            "schema": REPORT_SCHEMA,
            "status": "PASS",
            "created_utc": utc_now(),
            "workload": source["workload"],
            "treatment_name": args.treatment_name,
            "exact_correctness_key": treatment["correctness_key"],
            "source_control_exact_key": source["source_control_key"],
            "source_control_first_roi_simTicks": [
                record["first_roi_simTicks"]
                for record in source["control_records"]
            ],
            "treatment_first_roi_simTicks": treatment["first_roi_simTicks"],
            "treatment_certificate": treatment["certificate"],
            "binary_hashes": binary_hashes,
            "source_provenance": provenance,
        }
        replay_manifest = {
            "schema": REPLAY_SCHEMA,
            "created_utc": utc_now(),
            "source_worktree_commit": source_commit,
            "source_worktree_status": source_status,
            "source_provenance": provenance,
            "frozen_source_arm": {
                "profile": source["profile"],
                "binary": source["binary_name"],
                "selector": source["selector"],
                "options": options,
            },
            "candidate": {
                "gem5": {
                    "path": str(candidate_gem5),
                    "sha256": candidate_gem5_hash,
                },
                "config_tree": {
                    "path": str((inputs / "candidate-configs").resolve()),
                    **candidate_config_identity,
                },
                "sole_arm_gem5_args": args.sole_arm_gem5_arg,
            },
            "source_inputs": {
                "guest": {"path": str(guest), "sha256": guest_hash},
                "ramulator_library": {
                    "path": str(ramulator_library),
                    "sha256": ramulator_library_hash,
                },
                "ramulator_config": {
                    "path": str(ramulator_config),
                    "sha256": ramulator_config_hash,
                },
                "source_gem5_sha256": source["source_gem5_hash"],
            },
            "treatment": treatment,
        }
        atomic_json(output / "manifest.json", replay_manifest, immutable=True)
        atomic_json(output / "report.json", report, immutable=True)
        atomic_text(output / "campaign.exit", "0\n")
        atomic_text(output / "campaign.complete", utc_now() + "\n")
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        atomic_text(output / "campaign.exit", "1\n")
        raise error


def main() -> int:
    args = parse_args()
    try:
        if args.out.exists():
            raise ValueError(
                f"refusing to overwrite existing output: {args.out}"
            )
        source_root = args.source_matrix.resolve()
        if not source_root.is_dir() or source_root.is_symlink():
            raise ValueError("source matrix root is not a regular directory")
        manifest, manifest_hash = source_manifest(source_root)
        source = selected_source(
            source_root, manifest, args.checkpoint_group, args.control_arm
        )
        if args.treatment_name in {
            str(arm["name"]) for arm in manifest["arms"]
        }:
            raise ValueError(
                "treatment name collides with a source matrix arm"
            )
        require_nonconflicting_candidate_args(
            list(source["source_args"]), args.sole_arm_gem5_arg
        )
        candidate_gem5 = require_file(args.gem5, "candidate gem5")
        candidate_config = require_file(args.config, "candidate config")
        if not args.execute:
            print(
                json.dumps(
                    immutable_plan(
                        source_root,
                        manifest_hash,
                        source,
                        args.treatment_name,
                        candidate_gem5,
                        candidate_config,
                        args.sole_arm_gem5_arg,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        execute(args, source_root, manifest, manifest_hash, source)
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
