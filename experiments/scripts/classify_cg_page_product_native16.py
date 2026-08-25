#!/usr/bin/env python3
"""Fail-closed, one-shot classifier for the frozen full-CG native16 oracle.

This program only reads the two pinned raw roots.  Its sole mutation is an
immutable, atomically-published certificate in the candidate root.  It never
launches gem5 or a native workload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

CANDIDATE_NAME = "2026-08-24-cg-page-product-full-precomputed-5d51743b-r2"
BASELINE_NAME = "2026-08-11-cg-bounded-789cc703-full-v8"
CANDIDATE_ROOT = Path("/data1/nier/dx100-runs") / CANDIDATE_NAME
BASELINE_ROOT = Path("/data1/nier/dx100-runs") / BASELINE_NAME
RESULT_NAME = "NATIVE16_ORACLE_RESULT.json"
LEDGER_NAME = "NATIVE16_ORACLE_RESULT.sha256"
GATE_NAME = "NATIVE16_ORACLE_GATE.complete"
FATAL = re.compile(r"panic|fatal|assert|abort|segmentation fault|error:", re.I)
EXIT = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$", re.M
)
QUANTIZED = ("x_q5", "x_q6", "z_q5", "z_q6")
TOLERANCES = {
    "x_sum": 1e-8,
    "x_norm_sq": 1e-8,
    "z_sum": 1e-8,
    "z_norm_sq": 1e-8,
    "rnorm": 1e-3,
    "zeta": 1e-10,
}

# These are externally frozen evidence identities, not values discovered by a
# successful run.  Altering any raw input makes classification fail.
PINNED = {
    "baseline": {
        "analysis.json": "c8290d329b89c3a8e8e938e319502d0d5ebf454009e681499ccbae2dacafa8ed",
        "manifest.txt": "79bbbf15a5d83e51b8035a1a59497df82948c13e5ee8869437414a2557100442",
        "checkpoint-native16.exit": "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
        "checkpoint-native16.identity.sha256": "be9bf4939ce3d6b89aa57cfc1ead1d1abaf71c02407f6c877269b53e14facc90",
        "checkpoint-native16.files.sha256": "e2f1b188549d5921f0eb444e91c291f876bb91d98c466bccfb897b5309d1d887",
        "native16/command.txt": "a338ca6981581a649ec1006d1038904d22c9cfe8fbd461bc1eb3de3379e45fae",
        "native16/exit_code": "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
        "native16/run.log": "99c08fcbe3b121a61db866af4a4aa926b0eaddf87ad516a944784b496404ca73",
        "native16/run/stats.txt": "4122577993c17760b86462bb2bfcb1d87b7d33cf2e3f30a003139f586c0cc070",
        "native16/run/config.ini": "97222d3c69174762885a5a547083d6e8fccbe201df6a1c0c509dd193774002a4",
        "input/artifact_sha256.txt": "28396ca346ba0c62b0344d96fbe3a930386f06021f4fef548f8add4788e9a1bb",
        "input/gem5.opt": "67c338f7e00bed2d5e7bacbf7d2921db1ae8c73ca96d8d39c33384a07c2102ee",
        "input/cg_maa_16K_fp": "91d471340b800b8d230d096017a101517e22e1a50586d7e9e3e24ab440193b4b",
        "input/cg_data_4C.h": "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131",
        "input/libramulator.so": "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
        "input/source.tar": "5d0914c178ee4ef28ab93a17a33248c8ec661e628f4ad2499f3c65eaf29e3dbc",
    },
    "candidate": {
        "manifest.txt": "59bd17ab91537ad2b15ea8a8c45b8f5793eac9ff6fc955d5ff78d636f1ffedb2",
        "CG_REJECTED.status": "b659676ab4c9f00d5474357a9d9f337162c0067883f10eed3ebdc5c46dde7ae5",
        "checkpoint.exit": "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
        "checkpoint.log": "f0a6a92ea561bd3be32589cc7d92b6b45437bb03fa4dae1f8e7ea88c17dc108c",
        "input/artifact_sha256.before": "6baccd197c2e7130c1f8eb5973ba4f876d30b2cabfe50b7549cc23dedd1a972b",
        "input/checkpoint.files.sha256": "bd4d88775b9e4a7776fa73aa7867de8b2d93ecbd965352cc92495447751eb508",
        "run/restore.exit": "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
        "run/restore.log": "e12cad79f4a70bda04790aba3cd5c0fbdb3e86fa785591d281a12474df7e6796",
        "run/stats.txt": "cce10d70ec3ff077fca5a856a70f4c5757ce6d4dc03608bc954d16fdd653c4df",
        "run/config.ini": "d66c8d3330848c137617ecf1b69d0d2f87c6a5bedefd2a1e4ae9c1b7d4b907bf",
        "bin/cg_physical_page_product": "ff03d3ef89761bb956ecdca5030862d15d43ee0784dbe5bff364972b3523fb04",
        "input/cg_data_4C.h": "f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131",
        "input/physical_page_product_soa_jit.selector": "61be146ef89cf032f3f52974f95ace0cdaea123748cfe91e04a3663955d13562",
    },
}
CANDIDATE_EXTERNAL = {
    "/data1/nier/dx100-binaries/gem5-ef070d16bb1b25668fe80468693dade4eeaf1776a72fbc51d7a9ce070e5af483.opt": "ef070d16bb1b25668fe80468693dade4eeaf1776a72fbc51d7a9ce070e5af483",
    "/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so": "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753",
    "/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/benchmarks/NAS/cg/cg.cpp": "d254b68d34ff306a566f6b54256720314f3d1745b13284593b040e87ed544e60",
    "/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/configs/deprecated/example/se.py": "aacc6e624b7ab0e7b032d5cb913974fa790efdca84598bf468c11f14b9575d0f",
    "/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/ext/ramulator2/ramulator2/example_gem5_config.yaml": "aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b",
    "/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/experiments/scripts/run_cg_logical_page_rmw_hybrid.sh": "0276956040d539feb6b25a6272b7a89afd5b5e4b21b46a9d92250fac89c7cee8",
}


class ClassificationError(RuntimeError):
    pass


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ClassificationError(message)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def values(text: str) -> dict[str, str]:
    return {
        key: value
        for token in text.split()
        if "=" in token
        for key, value in (token.split("=", 1),)
    }


def one_line(text: str, prefix: str) -> str:
    matches = [line for line in text.splitlines() if line.startswith(prefix)]
    require(len(matches) == 1, f"requires exactly one {prefix.strip()} marker")
    return matches[0]


def key_values(path: Path) -> dict[str, str]:
    return {
        key: value
        for line in path.read_text().splitlines()
        if "=" in line
        for key, value in (line.split("=", 1),)
    }


def assert_root(candidate: Path, baseline: Path) -> None:
    require(
        candidate == CANDIDATE_ROOT, "candidate root is not externally pinned"
    )
    require(
        baseline == BASELINE_ROOT, "baseline root is not externally pinned"
    )


def verify_hashes(root: Path, pinned: dict[str, str]) -> dict[Path, str]:
    snapshot = {}
    for relative, expected in pinned.items():
        path = root / relative
        require(
            path.is_file() and digest(path) == expected,
            f"pinned hash mismatch: {path}",
        )
        snapshot[path] = expected
    return snapshot


def verify_ledger(path: Path, root: Path) -> None:
    lines = path.read_text().splitlines()
    require(lines, f"empty hash ledger: {path}")
    for number, line in enumerate(lines, 1):
        fields = line.split(maxsplit=1)
        require(
            len(fields) == 2
            and re.fullmatch(r"[0-9a-f]{64}", fields[0]) is not None,
            f"malformed hash ledger line {number}",
        )
        artifact = Path(fields[1].lstrip("*"))
        if not artifact.is_absolute():
            artifact = root / artifact
        require(
            artifact.is_file() and digest(artifact) == fields[0],
            f"ledger mismatch: {artifact}",
        )


def live_process(root: Path) -> str | None:
    needle = str(root).encode()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            command = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        if needle in command and (
            b"gem5" in command
            or b"run_cg_logical_page_rmw_hybrid.sh" in command
        ):
            return f"pid={proc.name} cmdline={command.replace(bytes([0]), b' ').decode(errors='replace')}"
    return None


def require_dead(root: Path) -> None:
    status = root / "RUNNING.status"
    require(
        not status.exists() or status.read_text().strip() != "running",
        "service status is live",
    )
    live = live_process(root)
    require(live is None, f"service process is live: {live}")


def first_stat(path: Path, name: str) -> int:
    text = path.read_text()
    begin = text.find("---------- Begin Simulation Statistics")
    end = text.find("---------- End Simulation Statistics", begin)
    require(begin >= 0 and end > begin, "missing first statistics window")
    matches = re.findall(
        rf"^{re.escape(name)}\s+([0-9]+)\b", text[begin:end], re.M
    )
    require(len(matches) == 1 and int(matches[0]) > 0, f"bad {name}")
    return int(matches[0])


def stat_sum(path: Path, suffix: str) -> int:
    text = path.read_text()
    begin = text.find("---------- Begin Simulation Statistics")
    end = text.find("---------- End Simulation Statistics", begin)
    matches = re.findall(
        rf"^\S*_{re.escape(suffix)}\s+([0-9]+)\b", text[begin:end], re.M
    )
    require(matches, f"missing *_{suffix} counter")
    return sum(map(int, matches))


def relative_delta(candidate: str, reference: str) -> float:
    reference_value = float(reference)
    return abs(float(candidate) - reference_value) / max(
        abs(reference_value), 1e-300
    )


def require_exact_fingerprint(
    candidate: dict[str, str], baseline: dict[str, str]
) -> dict[str, float]:
    for fingerprint in (candidate, baseline):
        require(
            fingerprint.get("elements") == "150000"
            and fingerprint.get("result") == "PASS"
            and fingerprint.get("nonfinite_x") == "0"
            and fingerprint.get("nonfinite_z") == "0",
            "invalid fingerprint",
        )
    for field in QUANTIZED:
        require(
            candidate.get(field) == baseline.get(field),
            f"native16 {field} mismatch",
        )
    deltas = {
        field: relative_delta(candidate[field], baseline[field])
        for field in TOLERANCES
    }
    for field, bound in TOLERANCES.items():
        require(
            deltas[field] <= bound,
            f"native16 scalar tolerance failed: {field}",
        )
    return deltas


def require_geometry(path: Path, required: tuple[str, ...]) -> None:
    lines = set(path.read_text().splitlines())
    for value in required:
        require(value in lines, f"config geometry lacks {value}")
    require(
        sum(
            line in {"[system.mem_ctrls0]", "[system.mem_ctrls1]"}
            for line in lines
        )
        == 2,
        "config geometry does not have two memory controllers",
    )


def require_terminal_counts(numbers: dict[str, int]) -> int:
    windows = numbers["full_windows"]
    require(
        windows == 10960
        and numbers["physical_page_product_windows"] == windows
        and numbers["staged_index_words"]
        == numbers["product_words"]
        == windows * 16384
        and numbers["index_publish_pages"]
        == numbers["product_publish_pages"]
        == windows * 4
        and numbers["q_spmv_eligible_windows"]
        == numbers["q_spmv_routed_windows"]
        == 8768
        and numbers["residual_spmv_eligible_windows"]
        == numbers["residual_spmv_routed_windows"]
        == 2192,
        "terminal mechanism closure failed",
    )
    return windows


def classify(
    candidate: Path, baseline: Path, *, allow_existing: bool = False
) -> tuple[dict[str, object], dict[Path, str]]:
    assert_root(candidate, baseline)
    for root in (candidate, baseline):
        require(root.is_dir(), f"missing root: {root}")
        require_dead(root)
    sealed = tuple(
        candidate / name for name in (RESULT_NAME, LEDGER_NAME, GATE_NAME)
    )
    if allow_existing:
        require(
            all(path.is_file() for path in sealed[:2]),
            "incomplete existing certificate",
        )
    else:
        require(
            not any(path.exists() for path in sealed),
            "refusing to overwrite one-shot certificate",
        )
    snapshot = verify_hashes(baseline, PINNED["baseline"])
    snapshot.update(verify_hashes(candidate, PINNED["candidate"]))
    for raw, expected in CANDIDATE_EXTERNAL.items():
        path = Path(raw)
        require(
            path.is_file() and digest(path) == expected,
            f"pinned external hash mismatch: {path}",
        )
        snapshot[path] = expected
    verify_ledger(
        baseline / "checkpoint-native16.files.sha256",
        baseline / "checkpoint-native16",
    )
    verify_ledger(
        candidate / "input/checkpoint.files.sha256", candidate / "checkpoint"
    )
    require(
        (baseline / "checkpoint-native16.exit").read_text().strip() == "0",
        "native16 checkpoint failed",
    )
    require(
        (baseline / "native16/exit_code").read_text().strip() == "0",
        "native16 run failed",
    )
    require(
        (candidate / "checkpoint.exit").read_text().strip() == "0",
        "candidate checkpoint failed",
    )
    require(
        (candidate / "run/restore.exit").read_text().strip() == "0",
        "candidate restore failed",
    )
    rejected = key_values(candidate / "CG_REJECTED.status")
    require(
        rejected
        == {
            "classification": "REJECT_CORRECTNESS_GATE",
            "reason": "x_q5_x_q6_z_q5_z_q6_mismatch",
            "mechanism_terminal": "PASS",
            "scalar_tolerances": "PASS",
            "performance_promotion": "PROHIBITED",
        },
        "bounded4 rejection is not preserved",
    )
    candidate_log = (candidate / "run/restore.log").read_text()
    baseline_log = (baseline / "native16/run.log").read_text()
    for log, arm in ((candidate_log, "candidate"), (baseline_log, "native16")):
        require(FATAL.search(log) is None, f"fatal evidence in {arm} log")
        require(
            len(EXIT.findall(log)) == 1 and log.count("ROI End!!!") == 1,
            f"bad {arm} completion markers",
        )
    candidate_fp = values(one_line(candidate_log, "CG_FINGERPRINT "))
    baseline_fp = values(one_line(baseline_log, "CG_FINGERPRINT "))
    deltas = require_exact_fingerprint(candidate_fp, baseline_fp)
    terminal = values(one_line(candidate_log, "CG_LOGICAL16_RMW_TERMINAL "))
    selection = values(one_line(candidate_log, "CG_LOGICAL16_RMW_SELECTION "))
    require(
        terminal.get("result") == "PASS"
        and terminal.get("treatment") == "physical_page_product_soa_jit"
        and terminal.get("producer") == "physical_page_mul_response_publish",
        "candidate terminal is invalid",
    )
    require(
        selection.get("treatment") == terminal.get("treatment")
        and selection.get("producer") == terminal.get("producer")
        and selection.get("host_payload_access") == "0"
        and terminal.get("host_payload_access") == "0",
        "selection closure failed",
    )
    fields = (
        "full_windows",
        "staged_index_words",
        "product_words",
        "index_publish_pages",
        "product_publish_pages",
        "physical_page_product_windows",
        "q_spmv_eligible_windows",
        "q_spmv_routed_windows",
        "residual_spmv_eligible_windows",
        "residual_spmv_routed_windows",
    )
    try:
        numbers = {field: int(terminal[field]) for field in fields}
    except (KeyError, ValueError) as error:
        raise ClassificationError(f"bad terminal counter: {error}") from error
    windows = require_terminal_counts(numbers)
    candidate_ticks = first_stat(candidate / "run/stats.txt", "simTicks")
    native_ticks = first_stat(baseline / "native16/run/stats.txt", "simTicks")
    require(
        stat_sum(candidate / "run/stats.txt", "IND_SoaJitInstructions")
        == windows
        and stat_sum(
            candidate / "run/stats.txt", "IND_SoaJitTerminalCompletions"
        )
        == windows
        and stat_sum(candidate / "run/stats.txt", "IND_SoaJitSelected")
        == windows * 16384
        and stat_sum(
            candidate / "run/stats.txt", "IND_SoaJitPredicateRejected"
        )
        == 0
        and stat_sum(candidate / "run/stats.txt", "IND_SoaJitAliasesApplied")
        == windows * 16384
        and stat_sum(
            candidate / "run/stats.txt", "IND_BoundedGlobalMergeFallbacks"
        )
        == 0,
        "SoA/JIT counter closure failed",
    )
    issues = stat_sum(candidate / "run/stats.txt", "STR_PublishIssues")
    accepts = stat_sum(candidate / "run/stats.txt", "STR_PublishAccepts")
    responses = stat_sum(
        candidate / "run/stats.txt", "STR_PublishWriteResponses"
    )
    terminals = stat_sum(candidate / "run/stats.txt", "STR_PublishTerminals")
    require(
        issues == accepts == responses == windows * 8 * 256
        and terminals == windows * 8,
        "publisher closure failed",
    )
    require_geometry(
        candidate / "run/config.ini",
        (
            "num_maas=1",
            "num_tiles_per_core=8",
            "num_tile_elements=16384",
            "physical_tile_elements=4096",
            "logical_tile_page_scheduler=false",
            "num_offset_table_entries=16384",
            "num_offset_table_epoch_entries=16384",
            "num_initial_row_table_slices=32",
            "soa_jit_predicate_active_credits=16",
            "soa_jit_active_value_owners=32",
        ),
    )
    require_geometry(
        baseline / "native16/run/config.ini",
        (
            "num_maas=1",
            "num_tiles_per_core=8",
            "num_tile_elements=16384",
            "physical_tile_elements=16384",
            "num_offset_table_entries=16384",
            "num_offset_table_epoch_entries=16384",
            "num_initial_row_table_slices=16",
        ),
    )
    ratio = candidate_ticks / native_ticks
    result = {
        "schema": "dx100.cg.page_product.native16_oracle.v1",
        "candidate_root": str(candidate),
        "baseline_root": str(baseline),
        "correctness": "PASS_NATIVE16_ORACLE",
        "performance": "REJECT_SLOWER",
        "candidate_simTicks": candidate_ticks,
        "native16_simTicks": native_ticks,
        "candidate_to_native16_simTicks_ratio": ratio,
        "native_reruns": 0,
        "bounded4_wrapper_rejection": "PRESERVED_REJECT_CORRECTNESS_GATE",
        "fingerprint": {field: candidate_fp[field] for field in QUANTIZED},
        "scalar_relative_deltas": deltas,
        "publisher_issue_accept_response": [issues, accepts, responses],
        "publisher_terminals": terminals,
    }
    for path, expected in snapshot.items():
        require(digest(path) == expected, f"raw evidence changed: {path}")
    return result, snapshot


def write_new(path: Path, text: str) -> Path:
    temporary = path.with_name(path.name + ".tmp")
    require(
        not temporary.exists() and not path.exists(),
        f"refusing existing output: {path}",
    )
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644
    )
    with os.fdopen(descriptor, "w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    return temporary


def verify_result_ledger(
    ledger_path: Path, snapshot: dict[Path, str], result_path: Path
) -> None:
    """Verify the sealed raw-evidence/result set, not mutable tool source."""
    entries: dict[Path, str] = {}
    for number, line in enumerate(ledger_path.read_text().splitlines(), 1):
        fields = line.split(maxsplit=1)
        require(
            len(fields) == 2
            and re.fullmatch(r"[0-9a-f]{64}", fields[0]) is not None,
            f"malformed result ledger line {number}",
        )
        path = Path(fields[1].lstrip("*")).resolve()
        require(path not in entries, f"duplicate result ledger path: {path}")
        entries[path] = fields[0]
    expected = {path.resolve(): value for path, value in snapshot.items()}
    expected[result_path.resolve()] = digest(result_path)
    require(set(entries) == set(expected), "result ledger artifact set mismatch")
    require(
        {path: entries.get(path) for path in expected} == expected,
        "result ledger does not match the raw evidence snapshot",
    )
    for path, expected_digest in expected.items():
        require(
            path.is_file() and digest(path) == expected_digest,
            f"result ledger mismatch: {path}",
        )


def validate_seal(
    candidate: Path, baseline: Path, *, require_gate: bool = True
) -> dict[str, object]:
    assert_root(candidate, baseline)
    result_path, ledger_path, gate_path = (
        candidate / name for name in (RESULT_NAME, LEDGER_NAME, GATE_NAME)
    )
    if require_gate:
        require(
            gate_path.read_text() == "PASS_NATIVE16_ORACLE\n",
            "bad native16 gate",
        )
    else:
        require(not gate_path.exists(), "gate exists before validation")
    result = json.loads(result_path.read_text())
    regenerated, snapshot = classify_existing(candidate, baseline)
    verify_result_ledger(ledger_path, snapshot, result_path)
    require(result == regenerated, "sealed result disagrees with raw evidence")
    for path, expected in snapshot.items():
        require(digest(path) == expected, f"raw evidence changed: {path}")
    return result


def classify_existing(
    candidate: Path, baseline: Path
) -> tuple[dict[str, object], dict[Path, str]]:
    # Reopen raw evidence without moving or rewriting any sealed artifact.
    return classify(candidate, baseline, allow_existing=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate_root", type=Path)
    parser.add_argument("--baseline-root", type=Path, default=BASELINE_ROOT)
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    candidate = args.candidate_root.resolve()
    baseline = args.baseline_root.resolve()
    if args.validate:
        print(
            json.dumps(
                validate_seal(candidate, baseline), indent=2, sort_keys=True
            )
        )
        return
    result, snapshot = classify(candidate, baseline)
    result_path = candidate / RESULT_NAME
    ledger_path = candidate / LEDGER_NAME
    gate_path = candidate / GATE_NAME
    contents = json.dumps(result, indent=2, sort_keys=True) + "\n"
    result_tmp = write_new(result_path, contents)
    ledger = "".join(
        f"{digest(path)}  {path}\n" for path in sorted(snapshot, key=str)
    )
    ledger += (
        f"{hashlib.sha256(contents.encode()).hexdigest()}  {result_path}\n"
    )
    ledger_tmp = write_new(ledger_path, ledger)
    os.replace(result_tmp, result_path)
    os.replace(ledger_tmp, ledger_path)
    validate_seal(candidate, baseline, require_gate=False)
    gate_tmp = write_new(gate_path, "PASS_NATIVE16_ORACLE\n")
    os.replace(gate_tmp, gate_path)
    try:
        validate_seal(candidate, baseline)
    except Exception:
        gate_path.unlink(missing_ok=True)
        raise
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ClassificationError, OSError, ValueError) as error:
        raise SystemExit(f"native16 classifier failed: {error}") from error
