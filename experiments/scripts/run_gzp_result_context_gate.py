#!/usr/bin/env python3
"""Run an exact two-replica full-GZP masked-index context32/context64 gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(
    "/data1/nier/dx100-runs/" "2026-08-14-gzp-masked-index-full-a3d0bba5-r1"
)
SOURCE_COMMAND = SOURCE / "runs/masked_index/restore.command.json"
CHECKPOINT = SOURCE / "checkpoint"
GUEST = SOURCE / "inputs/gradzatp_maa_16K_general_soa_jit_fp"
SELECTOR = SOURCE / "inputs/treatment.txt"
RAMULATOR_LIBRARY = Path(
    "/data1/nier/dx100-runs/"
    "2026-08-14-gzp-soa-jit-optimized-prepublisher-fbec9dbe-r1/"
    "inputs/libramulator.so"
)
EXPECTED_OUTPUT_HASH = "11225737641199706160"
EXPECTED_SELECTOR_HASH = (
    "32ebe0418fb690b057b08babaf5d1e7b05e65705f2c6ec776576cd810e86190a"
)
EXPECTED_GUEST_HASH = (
    "00980813e3bbcd74aec84d4352c545f5ff956485cac99c456fadfddfcab8ecda"
)
FULL_WINDOWS = 61
WINDOW_ELEMENTS = 16384
REPLICAS = (1, 2)
ARMS = (("control", 32), ("treatment", 64))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--gem5", type=Path, default=ROOT / "build/X86/gem5.opt"
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        candidate for candidate in root.rglob("*") if candidate.is_file()
    ):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "little"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def write_text(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value)
    temporary.replace(path)


def write_json(path: Path, value: object) -> None:
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def parse_fields(line: str) -> dict[str, str]:
    return dict(
        token.split("=", 1) for token in line.split()[1:] if "=" in token
    )


def exactly_one(lines: list[str], prefix: str) -> dict[str, str]:
    matches = [line for line in lines if line.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {prefix!r}, found {len(matches)}")
    return parse_fields(matches[0])


def first_stats(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    active = False
    complete = False
    for line in path.read_text().splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if not active and not complete:
                active = True
            continue
        if line.startswith("---------- End Simulation Statistics") and active:
            complete = True
            break
        if not active:
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            result[fields[0]] = int(float(fields[1]))
        except (ValueError, OverflowError):
            pass
    if not complete or "simTicks" not in result:
        raise RuntimeError(f"missing complete first stats window: {path}")
    return result


def stat_sum(stats: dict[str, int], suffix: str) -> int:
    values = [value for name, value in stats.items() if name.endswith(suffix)]
    if not values:
        raise RuntimeError(f"missing stats suffix {suffix}")
    return sum(values)


def command_for(gem5: Path, out: Path, contexts: int) -> list[str]:
    command = json.loads(SOURCE_COMMAND.read_text())
    command[0] = str(gem5.resolve())
    config = next(
        item
        for item in command
        if item.endswith("/configs/deprecated/example/se.py")
    )
    command[command.index(config)] = str(
        (ROOT / "configs/deprecated/example/se.py").resolve()
    )
    outdir = next(item for item in command if item.startswith("--outdir="))
    command[command.index(outdir)] = f"--outdir={out / 'gem5'}"
    context_option = next(
        item
        for item in command
        if item.startswith("--maa_soa_jit_active_contexts=")
    )
    command[
        command.index(context_option)
    ] = f"--maa_soa_jit_active_contexts={contexts}"
    owner_option = next(
        item
        for item in command
        if item.startswith("--maa_soa_jit_active_value_owners=")
    )
    command[
        command.index(owner_option)
    ] = "--maa_soa_jit_active_value_owners=64"
    pre_a = "--maa_soa_jit_pre_a_value_lookahead"
    if pre_a not in command:
        command.insert(command.index("--cmd"), pre_a)
    return command


def analyze(
    run: Path, arm: str, contexts: int, replica: int
) -> dict[str, int | str]:
    if (run / "restore.exit").read_text().strip() != "0":
        raise RuntimeError(f"replica {replica} {arm}: restore failed")
    log = (run / "restore.log").read_text(errors="replace")
    lines = log.splitlines()
    if (
        len(
            re.findall(
                r"Exiting @ tick \d+ because m5_exit instruction encountered",
                log,
            )
        )
        != 1
    ):
        raise RuntimeError(
            f"replica {replica} {arm}: terminal marker mismatch"
        )
    if re.search(
        r"\b(?:panic|fatal|segmentation fault|Assertion)\b", log, re.I
    ):
        raise RuntimeError(f"replica {replica} {arm}: fatal marker")
    output = exactly_one(lines, "UME_OUTPUT_FP ")
    reference = exactly_one(lines, "UME_REFERENCE_PASS ")
    ledger = exactly_one(lines, "UME_GZP_MASKED_INDEX_LEDGER ")
    terminal = exactly_one(lines, "UME_GZP_TERMINAL ")
    if (
        output.get("output_hash") != EXPECTED_OUTPUT_HASH
        or output.get("nonfinite") != "0"
        or reference.get("point_volume_errors") != "0"
        or reference.get("point_gradient_errors") != "0"
        or reference.get("elements") != "1180000"
    ):
        raise RuntimeError(f"replica {replica} {arm}: exact reference failed")
    zero_fields = (
        "active_uint32_max",
        "active_illegal_index",
        "inactive_legal_index",
        "inactive_non_sentinel",
    )
    if (
        ledger.get("result") != "PASS"
        or ledger.get("exact_equivalence") != "1"
        or any(ledger.get(field) != "0" for field in zero_fields)
        or terminal.get("treatment") != "volume_masked_index_soa_jit"
        or terminal.get("publisher") != "masked_index_no_predicate_publication"
        or terminal.get("predicate_publications") != "0"
        or terminal.get("result") != "PASS"
    ):
        raise RuntimeError(
            f"replica {replica} {arm}: masked-index ledger failed"
        )

    selected = int(ledger["full_selected"])
    rejected = int(ledger["full_rejected"])
    if selected + rejected != FULL_WINDOWS * WINDOW_ELEMENTS:
        raise RuntimeError(
            f"replica {replica} {arm}: incomplete classification"
        )
    stats = first_stats(run / "gem5/stats.txt")
    checks = {
        "selected": stat_sum(stats, "IND_SoaJitSelected"),
        "rejected": stat_sum(stats, "IND_SoaJitPredicateRejected"),
        "a_read_issues": stat_sum(stats, "IND_SoaJitAReadIssues"),
        "a_read_responses": stat_sum(stats, "IND_SoaJitAReadResponses"),
        "a_write_issues": stat_sum(stats, "IND_SoaJitAWriteIssues"),
        "a_write_responses": stat_sum(stats, "IND_SoaJitAWriteResponses"),
        "value_issues": stat_sum(stats, "IND_SoaJitValueReadIssues"),
        "value_responses": stat_sum(stats, "IND_SoaJitValueReadResponses"),
        "value_fills": stat_sum(stats, "IND_SoaJitValueFills"),
        "deliveries": stat_sum(stats, "IND_SoaJitValueDeliveries"),
        "lookahead_issues": stat_sum(stats, "IND_SoaJitLookaheadIssues"),
        "lookahead_responses": stat_sum(stats, "IND_SoaJitLookaheadResponses"),
        "pre_a_issues": stat_sum(stats, "IND_SoaJitPreAValueIssues"),
        "pre_a_uses": stat_sum(stats, "IND_SoaJitPreAValueUses"),
        "terminal_completions": stat_sum(
            stats, "IND_SoaJitTerminalCompletions"
        ),
        "context_stalls": stat_sum(stats, "IND_SoaJitContextStalls"),
    }
    if checks["selected"] != selected or checks["rejected"] != rejected:
        raise RuntimeError(
            f"replica {replica} {arm}: runtime classification mismatch"
        )
    if not (
        checks["a_read_issues"]
        == checks["a_read_responses"]
        == checks["a_write_issues"]
        == checks["a_write_responses"]
    ):
        raise RuntimeError(f"replica {replica} {arm}: A ledger failed")
    if not (
        checks["value_issues"]
        == checks["value_responses"]
        == checks["value_fills"]
        and checks["deliveries"]
        == checks["lookahead_issues"]
        == checks["lookahead_responses"]
        == selected
        and checks["pre_a_issues"] == checks["pre_a_uses"] > 0
        and checks["terminal_completions"] == FULL_WINDOWS
        and stat_sum(stats, "IND_SoaJitPredicateLineReads") == 0
        and stat_sum(stats, "IND_SoaJitPredicateLineResponses") == 0
    ):
        raise RuntimeError(
            f"replica {replica} {arm}: value/terminal ledger failed"
        )

    terminal_traces: list[str] = []
    result_traces: list[str] = []
    with (run / "gem5/virtual_trace.log").open(errors="replace") as trace:
        for line in trace:
            if "terminal=1" not in line:
                continue
            if "event=soa_jit_complete " in line:
                terminal_traces.append(line)
            elif "event=soa_jit_result_pipeline " in line:
                result_traces.append(line)
    if (
        len(terminal_traces) != FULL_WINDOWS
        or len(result_traces) != FULL_WINDOWS
    ):
        raise RuntimeError(
            f"replica {replica} {arm}: trace terminal count failed"
        )
    for line in terminal_traces:
        fields = parse_fields(line)
        if (
            fields.get("predicate_mode") != "masked_index"
            or fields.get("pre_a_enable") != "1"
            or fields.get("active_value_owners") != "64"
            or fields.get("active_contexts") != str(contexts)
            or fields.get("masked_index_additional_buffer_bytes") != "0"
        ):
            raise RuntimeError(
                f"replica {replica} {arm}: accepted-control trace failed"
            )
    for line in result_traces:
        fields = parse_fields(line)
        if (
            fields.get("active_contexts") != str(contexts)
            or fields.get("fixed_result_payload_bytes") != "4096"
            or fields.get("fixed_lookahead_value_payload_bytes") != "4096"
            or fields.get("active_lookahead_value_payload_bytes")
            != str(contexts * 64)
            or fields.get("incremental_lookahead_value_payload_bytes_vs_32")
            != "2048"
            or fields.get("fixed_max_transient_write_payload_bytes") != "4096"
            or fields.get("active_max_transient_write_payload_bytes")
            != str(contexts * 64)
            or fields.get(
                "incremental_max_transient_write_payload_bytes_vs_32"
            )
            != "2048"
            or fields.get("incremental_result_total_state_bytes_vs_32")
            != "17408"
            or fields.get("incremental_result_total_nonpayload_bytes_vs_32")
            != "13312"
        ):
            raise RuntimeError(f"replica {replica} {arm}: byte ledger failed")

    return {
        "replica": replica,
        "arm": arm,
        "contexts": contexts,
        "simTicks": stats["simTicks"],
        "context_stalls": checks["context_stalls"],
        "selected": selected,
        "rejected": rejected,
        "a_reads": checks["a_read_issues"],
        "a_writes": checks["a_write_issues"],
        "value_reads": checks["value_issues"],
        "pre_a_uses": checks["pre_a_uses"],
        "fixed_result_payload_bytes": 4096,
        "fixed_lookahead_value_payload_bytes": 4096,
        "max_transient_write_payload_bytes": contexts * 64,
        "incremental_total_nonpayload_bytes_vs_32": 13312,
        "incremental_total_state_bytes_vs_32": 17408,
        "output_hash": EXPECTED_OUTPUT_HASH,
    }


def main() -> int:
    args = parse_args()
    source_status = subprocess.check_output(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    if source_status:
        raise SystemExit("refusing evidence run from a dirty source tree")
    required_files = (
        args.gem5,
        SOURCE_COMMAND,
        GUEST,
        SELECTOR,
        RAMULATOR_LIBRARY,
    )
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing or not CHECKPOINT.is_dir():
        raise SystemExit("missing frozen inputs: " + ", ".join(missing))
    if args.out.exists():
        raise SystemExit(f"refusing existing output: {args.out}")
    if (
        sha256(GUEST) != EXPECTED_GUEST_HASH
        or sha256(SELECTOR) != EXPECTED_SELECTOR_HASH
    ):
        raise SystemExit("frozen masked-index guest/selector identity changed")
    args.out.mkdir(parents=True)
    write_text(args.out / "campaign.exit", "running\n")
    checkpoint_hash = tree_sha256(CHECKPOINT)
    selector_hash = sha256(SELECTOR)
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = "4"
    env["OMP_PROC_BIND"] = "false"
    env["LD_LIBRARY_PATH"] = str(RAMULATOR_LIBRARY.parent)
    rows: list[dict[str, int | str]] = []
    try:
        for replica in REPLICAS:
            processes: list[
                tuple[str, int, Path, subprocess.Popen[bytes], object]
            ] = []
            for arm, contexts in ARMS:
                run = args.out / f"replica-{replica}" / arm
                (run / "gem5").mkdir(parents=True)
                command = command_for(args.gem5, run, contexts)
                write_json(run / "restore.command.json", command)
                write_text(
                    run / "restore.command.txt", shlex.join(command) + "\n"
                )
                log = (run / "restore.log").open("wb")
                process = subprocess.Popen(
                    command, stdout=log, stderr=subprocess.STDOUT, env=env
                )
                processes.append((arm, contexts, run, process, log))
            for arm, contexts, run, process, log in processes:
                returncode = process.wait()
                log.close()
                write_text(run / "restore.exit", f"{returncode}\n")
                rows.append(analyze(run, arm, contexts, replica))
            if sha256(SELECTOR) != selector_hash:
                raise RuntimeError("shared selector changed during restores")
        if tree_sha256(CHECKPOINT) != checkpoint_hash:
            raise RuntimeError("shared checkpoint changed during restores")
        for arm, _contexts in ARMS:
            replicas = [row for row in rows if row["arm"] == arm]
            comparable = {key for key in replicas[0] if key != "replica"}
            if any(
                any(row[key] != replicas[0][key] for key in comparable)
                for row in replicas[1:]
            ):
                raise RuntimeError(f"{arm}: replicas are not exact")
        control = next(
            row
            for row in rows
            if row["replica"] == 1 and row["arm"] == "control"
        )
        treatment = next(
            row
            for row in rows
            if row["replica"] == 1 and row["arm"] == "treatment"
        )
        summary = {
            "control_simTicks": control["simTicks"],
            "treatment_simTicks": treatment["simTicks"],
            "delta_ticks": int(control["simTicks"])
            - int(treatment["simTicks"]),
            "speedup": int(control["simTicks"]) / int(treatment["simTicks"]),
            "control_context_stalls": control["context_stalls"],
            "treatment_context_stalls": treatment["context_stalls"],
        }
        manifest = {
            "schema": "dx100.gzp_result_context_gate.v1",
            "source_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "source_status": source_status,
            "gem5_sha256": sha256(args.gem5),
            "guest_sha256": sha256(GUEST),
            "selector_sha256": selector_hash,
            "checkpoint_sha256": checkpoint_hash,
            "ramulator_sha256": sha256(RAMULATOR_LIBRARY),
            "replicas_per_arm": 2,
            "timeout_seconds": 0,
            "only_treatment": "maa_soa_jit_active_contexts:32->64",
            "fixed_controls": "masked_index=1,pre_a=1,value_owners=64",
        }
        write_json(args.out / "manifest.json", manifest)
        write_json(
            args.out / "results.json", {"rows": rows, "summary": summary}
        )
        header = list(rows[0])
        tsv = ["\t".join(header)] + [
            "\t".join(str(row[key]) for key in header) for row in rows
        ]
        write_text(args.out / "results.tsv", "\n".join(tsv) + "\n")
        write_text(
            args.out / "summary.txt",
            "".join(f"{key}={value}\n" for key, value in summary.items()),
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        write_text(args.out / "campaign.exit", "1\n")
        print(f"error: {error}", file=sys.stderr)
        return 1
    write_text(args.out / "campaign.exit", "0\n")
    print((args.out / "results.tsv").read_text(), end="")
    print((args.out / "summary.txt").read_text(), end="")
    print("GZP_RESULT_CONTEXT_GATE_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
