#!/usr/bin/env python3
"""Add feeder-matched native controls to the accepted equal-work r4 matrix.

Only two gem5 restores are launched: native16 and native4x4 with the direct-
index feeder set to 64 lines.  The accepted four-arm predecessor, including
its executable, guest, treatment-neutral checkpoint, inputs, and raw arms, is
validated in place and never modified.  A bubblewrap read-only overlay supplies
each deferred native treatment at the absolute path frozen into the checkpoint.

The successful successor is sealed read-only and joins the four predecessor
arms with the two new controls.  ``validate`` is independently read-only.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import (
    Mapping,
    Sequence,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.scripts import run_hybrid_equal_work_micro_matrix as base

PREDECESSOR = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "hybrid-equal-work-micro-20260828-20260828-094827-85a96b10/"
    "evidence/hybrid-equal-work-micro-r4"
)
PREDECESSOR_SELECTOR = PREDECESSOR / "treatment.txt"
BWRAP = Path("/usr/bin/bwrap")
SOURCE_BLOB = "70c18986046234d706094dae7a09f1d369b8d3b1"
PREDECESSOR_HASHES = {
    "artifacts.sha256": (
        "d6bd4adcf1fdd22cc24884ab9421070125087ef556dfeb1462d6c98056873f82"
    ),
    "matrix.tsv": (
        "3f47aaf17cf43dc288f6765e3c46721a0cb39c2760bcc9fa52176c794e159d54"
    ),
    "result.json": (
        "d44609f28a30e46648dca4febfe7ff0b43d47fe08140dbb356c5597ebe01b870"
    ),
}

PREDECESSOR_ARM = {
    "native16_f64": "native16",
    "native4_f64": "native4",
}
NEW_ARMS = (
    base.ArmSpec(
        "native16_f64",
        "native_direct",
        16_384,
        16_384,
        16_384,
        64,
        False,
        1,
        1,
        1,
    ),
    base.ArmSpec(
        "native4_f64",
        "native_direct",
        4_096,
        16_384,
        4_096,
        64,
        False,
        4,
        4,
        4,
    ),
)
ALL_ARM_NAMES = (
    "native16",
    "native4",
    "hybrid1",
    "hybrid64",
    "native16_f64",
    "native4_f64",
)
WORK_COUNTERS = (
    "simInsts",
    "indirect_ops",
    "stream_writes",
    "scalar_ops",
    "index_words",
)


class SuccessorError(RuntimeError):
    """Fail-closed successor error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SuccessorError(message)


def sha256_file(path: Path) -> str:
    return base.sha256_file(path)


def committed_runner() -> dict[str, str]:
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    committed = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{commit}:{relative}"]
    )
    require(
        committed == Path(__file__).read_bytes(), "runner is not committed"
    )
    require(
        not subprocess.check_output(
            ["git", "-C", str(ROOT), "status", "--short"], text=True
        ),
        "refusing evidence launch from a dirty worktree",
    )
    return {
        "runner_source_commit": commit,
        "runner_sha256": hashlib.sha256(committed).hexdigest(),
    }


def verify_source_contract() -> dict[str, object]:
    commit = base.SIMULATOR_SOURCE_COMMIT
    blob = subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT),
            "rev-parse",
            f"{commit}:src/mem/MAA/IndirectAccess.cc",
        ],
        text=True,
    ).strip()
    require(blob == SOURCE_BLOB, "frozen direct-index source blob changed")
    source = subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT),
            "show",
            f"{commit}:src/mem/MAA/IndirectAccess.cc",
        ],
        text=True,
    )
    required_fragments = (
        "Instruction::OpcodeType::INDIR_LD_INDEX",
        "const size_t line_capacity =\n"
        "        static_cast<size_t>(direct_index_buffer_lines);",
        "direct_index_pending_lines.size() +\n"
        "               direct_index_ready_lines.size() < line_capacity",
        "IND_VirtIndexLineHighWater",
        "IND_VirtIndexWordHighWater",
    )
    for fragment in required_fragments:
        require(
            fragment in source, f"missing frozen source contract: {fragment}"
        )
    return {
        "simulator_source_commit": commit,
        "indirect_access_blob": blob,
        "native_direct_opcode_uses_feeder": True,
        "feeder_capacity_expression": (
            "pending direct-index lines + ready direct-index lines "
            "< virtual_index_buffer_lines"
        ),
        "activation_counters": [
            "IND_VirtIndexLineHighWater",
            "IND_VirtIndexWordHighWater",
        ],
    }


def verify_runtime_python(launch: Mapping[str, object]) -> dict[str, str]:
    command = json.loads(
        (PREDECESSOR / "arms/native16/command.json").read_text()
    )
    se_path = next(
        Path(token)
        for token in command
        if token.endswith("configs/deprecated/example/se.py")
    )
    runtime_root = se_path.parents[3]
    hashes = launch.get("runtime_python_sha256")
    require(
        isinstance(hashes, dict), "predecessor lacks runtime Python hashes"
    )
    for relative, expected in hashes.items():
        path = runtime_root / relative
        require(path.is_file(), f"missing frozen runtime Python: {path}")
        require(
            sha256_file(path) == expected,
            f"frozen runtime Python changed: {relative}",
        )
    return {str(key): str(value) for key, value in hashes.items()}


def verify_predecessor() -> dict[str, object]:
    for relative, expected in PREDECESSOR_HASHES.items():
        path = PREDECESSOR / relative
        require(path.is_file(), f"missing predecessor authority: {path}")
        require(
            sha256_file(path) == expected,
            f"predecessor authority changed: {relative}",
        )
    result = base.validate(PREDECESSOR)
    require(
        result.get("decision") == "ACCEPT_ALL_FOUR_ARMS", "r4 not accepted"
    )
    require(result.get("terminal") is True, "r4 is not terminal")
    launch = json.loads((PREDECESSOR / "launch_manifest.json").read_text())
    require(
        launch.get("checkpoint_identity") == result.get("checkpoint_identity"),
        "predecessor checkpoint identity mismatch",
    )
    require(
        launch.get("workload_sha256") == result.get("workload_sha256"),
        "predecessor workload identity mismatch",
    )
    runtime_hashes = verify_runtime_python(launch)
    return {
        "result": result,
        "launch": launch,
        "runtime_python_sha256": runtime_hashes,
        "selector_sha256": sha256_file(PREDECESSOR_SELECTOR),
    }


def set_option(command: list[str], option: str, value: object) -> None:
    prefix = f"{option}="
    matches = [
        index
        for index, token in enumerate(command)
        if token.startswith(prefix)
    ]
    require(len(matches) == 1, f"expected exactly one {option}")
    command[matches[0]] = f"{prefix}{value}"


def replace_outdir(command: list[str], out: Path) -> None:
    set_option(command, "--outdir", out)


def normalized_command(command: Sequence[str]) -> list[str]:
    return [
        token
        for token in command
        if not token.startswith("--outdir=")
        and not token.startswith("--maa_virtual_index_buffer_lines=")
    ]


def command_for(arm: base.ArmSpec, run: Path) -> list[str]:
    predecessor_name = PREDECESSOR_ARM[arm.name]
    command = json.loads(
        (PREDECESSOR / "arms" / predecessor_name / "command.json").read_text()
    )
    require(isinstance(command, list), "predecessor command is not a list")
    replace_outdir(command, run)
    set_option(command, "--maa_virtual_index_buffer_lines", arm.feeder_lines)
    require(command[0] == str(PREDECESSOR / "input/gem5.opt"), "gem5 changed")
    require(
        f"--checkpoint-dir={PREDECESSOR / 'checkpoint'}" in command,
        "checkpoint changed",
    )
    require(str(PREDECESSOR / "input/workload") in command, "guest changed")
    require(
        command.count(f"deferred {PREDECESSOR_SELECTOR}") == 1,
        "frozen selector path changed",
    )
    prior = json.loads(
        (PREDECESSOR / "arms" / predecessor_name / "command.json").read_text()
    )
    require(
        normalized_command(command) == normalized_command(prior),
        f"{arm.name}: command changed outside output and feeder depth",
    )
    return command


def wrapped_command(
    root: Path, treatment: Path, command: Sequence[str]
) -> list[str]:
    require(BWRAP.is_file(), f"missing bubblewrap: {BWRAP}")
    root = root.resolve()
    treatment = treatment.resolve()
    require(treatment.is_relative_to(root), "treatment is outside successor")
    return [
        str(BWRAP),
        "--die-with-parent",
        "--ro-bind",
        "/",
        "/",
        "--bind",
        str(root),
        str(root),
        "--ro-bind",
        str(treatment),
        str(PREDECESSOR_SELECTOR),
        *command,
    ]


def live_checkpoint_users() -> list[dict[str, object]]:
    needle = f"--checkpoint-dir={PREDECESSOR / 'checkpoint'}"
    users: list[dict[str, object]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
            stat = (entry / "stat").read_text()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        command = raw.replace(b"\0", b" ").decode(errors="replace")
        if needle not in command:
            continue
        closing = stat.rfind(")")
        fields = stat[closing + 2 :].split() if closing >= 0 else []
        users.append(
            {
                "pid": int(entry.name),
                "proc_start_ticks": int(fields[19]) if len(fields) > 19 else 0,
                "command": command,
            }
        )
    return users


def preflight() -> dict[str, object]:
    authority = verify_predecessor()
    require(
        not live_checkpoint_users(),
        "frozen checkpoint has a live restore owner",
    )
    committed = committed_runner()
    source = verify_source_contract()
    frozen = base.preflight(
        PREDECESSOR / "input/gem5.opt",
        PREDECESSOR / "input/libramulator.so",
    )
    require(
        frozen["gem5_sha256"] == authority["result"]["gem5_sha256"],
        "frozen gem5 authority mismatch",
    )
    return {
        **committed,
        **source,
        "gem5_sha256": authority["result"]["gem5_sha256"],
        "ramulator_sha256": authority["launch"]["ramulator_sha256"],
        "workload_sha256": authority["result"]["workload_sha256"],
        "checkpoint_identity": authority["result"]["checkpoint_identity"],
        "predecessor_result_sha256": PREDECESSOR_HASHES["result.json"],
        "predecessor_ledger_sha256": PREDECESSOR_HASHES["artifacts.sha256"],
        "predecessor_matrix_sha256": PREDECESSOR_HASHES["matrix.tsv"],
        "predecessor_selector_sha256_before": authority["selector_sha256"],
        "runtime_python_sha256": authority["runtime_python_sha256"],
        "bubblewrap_sha256": sha256_file(BWRAP),
        "selector_isolation": (
            "read-only per-arm bind overlay at the predecessor absolute path"
        ),
    }


def old_index_line_hwm(name: str) -> int:
    stats = base.first_stats_section(
        PREDECESSOR / "arms" / name / "run/stats.txt"
    )
    return base.summed_stat(stats, "IND_VirtIndexLineHighWater")


def classify_new_arm(root: Path, arm: base.ArmSpec) -> dict[str, object]:
    item = base.classify_arm(root, arm)
    stats = base.first_stats_section(
        root / "arms" / arm.name / "run/stats.txt"
    )
    item["counters"]["index_line_hwm"] = base.summed_stat(
        stats, "IND_VirtIndexLineHighWater"
    )
    return item


def validate_native_work(
    predecessor: Mapping[str, object],
    candidate: Mapping[str, object],
    arm: base.ArmSpec,
) -> dict[str, int]:
    require(
        candidate["output_hash"] == predecessor["output_hash"],
        f"{arm.name}: output changed",
    )
    old = predecessor["counters"]
    new = candidate["counters"]
    for field in WORK_COUNTERS:
        require(
            new[field] == old[field], f"{arm.name}: work changed for {field}"
        )
    old_lines = old_index_line_hwm(PREDECESSOR_ARM[arm.name])
    old_words = int(old["index_hwm"])
    new_lines = int(new["index_line_hwm"])
    new_words = int(new["index_hwm"])
    require(new_lines > old_lines, f"{arm.name}: feeder line high water inert")
    require(new_words > old_words, f"{arm.name}: feeder word high water inert")
    require(
        new_lines <= arm.feeder_lines * arm.expected_indirect_ops,
        f"{arm.name}: feeder line high water exceeds capacity",
    )
    require(
        new_words
        <= (
            arm.feeder_lines
            * base.WORDS_PER_INDEX_LINE
            * arm.expected_indirect_ops
        ),
        f"{arm.name}: feeder word high water exceeds capacity",
    )
    return {
        "feeder1_line_high_water": old_lines,
        "feeder64_line_high_water": new_lines,
        "feeder1_word_high_water": old_words,
        "feeder64_word_high_water": new_words,
    }


def comparison(
    reference_name: str,
    candidate_name: str,
    arms: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    reference = int(arms[reference_name]["counters"]["simTicks"])
    candidate = int(arms[candidate_name]["counters"]["simTicks"])
    return {
        "reference": reference_name,
        "candidate": candidate_name,
        "reference_simTicks": reference,
        "candidate_simTicks": candidate,
        "speedup_reference_over_candidate": reference / candidate,
        "candidate_latency_change_fraction": candidate / reference - 1.0,
    }


def classify_successor(root: Path) -> dict[str, object]:
    authority = verify_predecessor()
    manifest = json.loads((root / "manifest.json").read_text())
    require(
        manifest.get("schema") == "dx100.hybrid_feeder_matched_native.v1",
        "successor manifest schema",
    )
    for key in ("gem5_sha256", "workload_sha256", "checkpoint_identity"):
        require(
            manifest.get(key) == authority["result"].get(key),
            f"successor manifest changed {key}",
        )
    require(
        manifest.get("predecessor_result_sha256")
        == PREDECESSOR_HASHES["result.json"],
        "successor predecessor result authority changed",
    )
    require(
        manifest.get("native_restores_launched") == 2
        and manifest.get("full_application_runs") == 0
        and manifest.get("accepted_arms_rerun") == 0,
        "successor launch scope changed",
    )

    predecessor_arms = copy.deepcopy(authority["result"]["arms"])
    arms: dict[str, dict[str, object]] = dict(predecessor_arms)
    activation: dict[str, dict[str, int]] = {}
    for arm in NEW_ARMS:
        arm_root = root / "arms" / arm.name
        arm_manifest = json.loads((arm_root / "arm.json").read_text())
        require(
            arm_manifest.get("spec") == asdict(arm), f"{arm.name}: arm spec"
        )
        require(
            arm_manifest.get("workload_sha256")
            == authority["result"]["workload_sha256"],
            f"{arm.name}: workload identity",
        )
        require(
            arm_manifest.get("checkpoint_identity")
            == authority["result"]["checkpoint_identity"],
            f"{arm.name}: checkpoint identity",
        )
        require(
            arm_manifest.get("treatment_sha256")
            == sha256_file(
                PREDECESSOR
                / "arms"
                / PREDECESSOR_ARM[arm.name]
                / "treatment.txt"
            ),
            f"{arm.name}: deferred treatment changed",
        )
        command = json.loads((arm_root / "command.json").read_text())
        expected = command_for(arm, arm_root / "run")
        require(command == expected, f"{arm.name}: command changed")
        wrapper = json.loads((arm_root / "wrapped_command.json").read_text())
        require(
            wrapper
            == wrapped_command(root, arm_root / "treatment.txt", command),
            f"{arm.name}: selector isolation changed",
        )
        process = json.loads((arm_root / "process.json").read_text())
        expected_command_hash = hashlib.sha256(
            json.dumps(wrapper, separators=(",", ":")).encode()
        ).hexdigest()
        require(
            process.get("command_sha256") == expected_command_hash,
            f"{arm.name}: process command identity",
        )
        item = classify_new_arm(root, arm)
        base_name = PREDECESSOR_ARM[arm.name]
        activation[arm.name] = validate_native_work(
            predecessor_arms[base_name], item, arm
        )
        arms[arm.name] = item

    require(tuple(arms) == ALL_ARM_NAMES, "six-arm join changed")
    require(
        len({str(item["output_hash"]) for item in arms.values()}) == 1,
        "six-arm output hashes differ",
    )
    comparisons = {
        "hybrid1_vs_native4_f1": comparison("native4", "hybrid1", arms),
        "hybrid64_vs_native4_f64": comparison("native4_f64", "hybrid64", arms),
        "hybrid64_vs_native16_f64": comparison(
            "native16_f64", "hybrid64", arms
        ),
        "native16_feeder64_vs_feeder1": comparison(
            "native16", "native16_f64", arms
        ),
        "native4_feeder64_vs_feeder1": comparison(
            "native4", "native4_f64", arms
        ),
    }
    return {
        "schema": "dx100.hybrid_feeder_matched_native.result.v1",
        "terminal": True,
        "decision": "ACCEPT_ALL_SIX_ARMS",
        "performance_metric": "simTicks",
        "repetitions_per_arm": 1,
        "same_binary": True,
        "same_guest": True,
        "same_checkpoint_input": True,
        "workload_sha256": authority["result"]["workload_sha256"],
        "gem5_sha256": authority["result"]["gem5_sha256"],
        "checkpoint_identity": authority["result"]["checkpoint_identity"],
        "predecessor_result_sha256": PREDECESSOR_HASHES["result.json"],
        "arms": arms,
        "native_feeder_activation": activation,
        "comparisons": comparisons,
        "limitations": [
            "one deterministic gem5 observation per arm",
            "microbenchmark evidence only; no full application was launched",
            "speed comparisons apply only to the exact frozen binary/config",
            "native4 arms are four 4K operations in the shared T16K logical "
            "aperture, not true T4096/API-aperture runs",
            "feeder capacity cost is not area, power, or Fmax evidence",
        ],
    }


def write_matrix(root: Path, result: Mapping[str, object]) -> None:
    fields = (
        "simTicks",
        "simInsts",
        "indirect_ops",
        "stream_writes",
        "scalar_ops",
        "index_words",
        "index_hwm",
    )
    with (root / "matrix.tsv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(("arm", "classification", "output_hash", *fields))
        for name in ALL_ARM_NAMES:
            arm = result["arms"][name]
            counters = arm["counters"]
            writer.writerow(
                (
                    name,
                    arm["classification"],
                    arm["output_hash"],
                    *(counters[field] for field in fields),
                )
            )


def successor_artifacts(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path != root / "artifacts.sha256"
    )


def write_ledger(root: Path) -> None:
    lines = [
        f"{sha256_file(path)}  {path.relative_to(root)}"
        for path in successor_artifacts(root)
    ]
    (root / "artifacts.sha256").write_text("\n".join(lines) + "\n")


def validate_ledger(root: Path) -> None:
    ledger = root / "artifacts.sha256"
    require(ledger.is_file(), "missing successor artifact ledger")
    seen: set[str] = set()
    for line in ledger.read_text().splitlines():
        digest, relative = line.split("  ", 1)
        require(
            re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "bad digest"
        )
        require(
            relative not in seen, f"duplicate successor artifact: {relative}"
        )
        seen.add(relative)
        require(
            sha256_file(root / relative) == digest,
            f"successor artifact changed: {relative}",
        )
    actual = {
        str(path.relative_to(root)) for path in successor_artifacts(root)
    }
    require(seen == actual, "successor artifact set changed")


def make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.chmod(path.stat().st_mode & ~0o222)
    directories = (item for item in root.rglob("*") if item.is_dir())
    for path in sorted(directories, reverse=True):
        path.chmod(path.stat().st_mode & ~0o222)
    root.chmod(root.stat().st_mode & ~0o222)


def validate_read_only(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        require(
            path.stat().st_mode & 0o222 == 0,
            f"successor is writable: {path}",
        )


def execute(root: Path) -> dict[str, object]:
    require(not root.exists(), f"refusing to overwrite successor: {root}")
    authority = preflight()
    root.mkdir(parents=True)
    (root / "arms").mkdir()
    manifest = {
        "schema": "dx100.hybrid_feeder_matched_native.v1",
        **authority,
        "predecessor_root": str(PREDECESSOR),
        "new_arms": [asdict(arm) for arm in NEW_ARMS],
        "native_restores_launched": 2,
        "full_application_runs": 0,
        "accepted_arms_rerun": 0,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    environment = os.environ.copy()
    library = str((PREDECESSOR / "input").resolve())
    prior = environment.get("LD_LIBRARY_PATH")
    environment["LD_LIBRARY_PATH"] = f"{library}:{prior}" if prior else library
    environment["OMP_PROC_BIND"] = "false"
    environment["OMP_NUM_THREADS"] = "4"
    try:
        for arm in NEW_ARMS:
            arm_root = root / "arms" / arm.name
            arm_root.mkdir()
            treatment = arm_root / "treatment.txt"
            treatment.write_text(arm.treatment)
            command = command_for(arm, arm_root / "run")
            wrapper = wrapped_command(root, treatment, command)
            (arm_root / "command.json").write_text(
                json.dumps(command, indent=2) + "\n"
            )
            (arm_root / "wrapped_command.json").write_text(
                json.dumps(wrapper, indent=2) + "\n"
            )
            (arm_root / "arm.json").write_text(
                json.dumps(
                    {
                        "name": arm.name,
                        "spec": asdict(arm),
                        "workload_sha256": authority["workload_sha256"],
                        "checkpoint_identity": authority[
                            "checkpoint_identity"
                        ],
                        "treatment_sha256": sha256_file(treatment),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            returncode = base.run_command(
                wrapper,
                arm_root / "restore.log",
                environment,
                arm_root / "process.json",
            )
            (arm_root / "restore.exit").write_text(f"{returncode}\n")
            require(
                returncode == 0, f"{arm.name}: restore exited {returncode}"
            )

        post = verify_predecessor()
        require(
            post["selector_sha256"]
            == authority["predecessor_selector_sha256_before"],
            "predecessor selector was modified",
        )
        result = classify_successor(root)
        (root / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
        write_matrix(root, result)
        (root / "gate.complete").write_text(
            "ACCEPT_ALL_SIX_ARMS\n"
            "same_binary=true\n"
            "same_guest=true\n"
            "same_checkpoint_input=true\n"
            "native_restores_launched=2\n"
            "accepted_arms_rerun=0\n"
            "full_application_runs=0\n"
            "performance_metric=simTicks\n"
        )
        write_ledger(root)
        validate_ledger(root)
        recomputed = classify_successor(root)
        require(recomputed == result, "pre-seal classification changed")
        make_read_only(root)
        validate(root)
        return result
    except BaseException as error:
        if root.stat().st_mode & 0o222:
            (root / "matrix.failed").write_text("failed\n")
            (root / "failure.json").write_text(
                json.dumps(
                    {
                        "error_type": type(error).__name__,
                        "message": str(error),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
        raise


def validate(root: Path) -> dict[str, object]:
    validate_read_only(root)
    validate_ledger(root)
    recomputed = classify_successor(root)
    sealed = json.loads((root / "result.json").read_text())
    require(
        recomputed == sealed, "sealed successor differs from classification"
    )
    require(
        (root / "gate.complete").read_text().splitlines()[0]
        == "ACCEPT_ALL_SIX_ARMS",
        "successor gate changed",
    )
    return recomputed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)
    run_parser = subparsers.add_parser(
        "run", help="launch only the two new restores"
    )
    run_parser.add_argument("out", type=Path)
    validate_parser = subparsers.add_parser(
        "validate",
        help="read-only independent validation of the six-arm successor",
    )
    validate_parser.add_argument("out", type=Path)
    subparsers.add_parser(
        "preflight", help="validate frozen authority without launch"
    )
    args = parser.parse_args(argv)
    try:
        if args.action == "run":
            result = execute(args.out.resolve())
        elif args.action == "validate":
            result = validate(args.out.resolve())
        else:
            result = preflight()
    except (
        SuccessorError,
        base.MatrixError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.action == "preflight":
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result["comparisons"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
