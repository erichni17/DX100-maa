#!/usr/bin/env python3
"""Run the exact full-GZP pre-A owner-capacity same-checkpoint gate.

This is deliberately a restore-only campaign.  It materializes the accepted
pre-A command from the frozen f2865321 evidence, changing exactly one gem5
option between arms: ``maa_soa_jit_active_value_owners`` (32 or 64).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

FROZEN_PAIR = Path(
    "/data1/nier/dx100-runs/2026-08-14-gzp-pre-a-pair-f2865321-r2"
)
FROZEN_SOURCE = Path(
    "/data1/nier/dx100-runs/2026-08-14-gzp-soa-jit-coherent-0568953c-r2"
)
TEMPLATE_COMMAND = FROZEN_PAIR / "pre_a" / "command.json"
CHECKPOINT = FROZEN_SOURCE / "checkpoints/volume_only_soa_jit/gem5"
RAMULATOR = FROZEN_SOURCE / "inputs/libramulator.so"
EXPECTED_GEM5_SHA256 = (
    "8402919dfb871e5052d2b1f9548fb916fe0d6858df0c572fecafd8cba8cbef50"
)
EXPECTED_CONFIG_SHA256 = (
    "aacc6e624b7ab0e7b032d5cb913974fa790efdca84598bf468c11f14b9575d0f"
)
EXPECTED_GUEST_SHA256 = (
    "b6811a68b70e62d751f68a51eb86fbb4006340be5b5effba8342f674a9b0c4cf"
)
EXPECTED_RAMULATOR_SHA256 = (
    "76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753"
)
EXPECTED_OUTPUT_HASH = "11225737641199706160"
FULL_ELEMENTS = 1_000_000
WINDOW_ELEMENTS = 16_384
REPLICAS = ("replica-1", "replica-2")
ARMS = (("owners-32", 32), ("owners-64", 64))
FATAL_RE = re.compile(
    r"\b(?:panic|fatal|segmentation fault|assertion)\b", re.I
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--expected-gem5-sha256", default=EXPECTED_GEM5_SHA256)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{64}", args.expected_gem5_sha256):
        parser.error(
            "--expected-gem5-sha256 must be 64 lowercase hex characters"
        )
    return args


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def atomic_text(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value)
    temporary.replace(path)


def command_option(command: list[str], name: str) -> str:
    values = [
        item.split("=", 1)[1]
        for item in command
        if item.startswith(name + "=")
    ]
    if len(values) != 1:
        raise RuntimeError(
            f"expected exactly one {name}= option, got {len(values)}"
        )
    return values[0]


def template() -> list[str]:
    command = json.loads(TEMPLATE_COMMAND.read_text())
    if not isinstance(command, list) or not all(
        isinstance(item, str) for item in command
    ):
        raise RuntimeError("frozen pre-A command is not a string argv list")
    if command_option(command, "--checkpoint-dir") != str(CHECKPOINT):
        raise RuntimeError(
            "frozen command does not restore the accepted checkpoint"
        )
    if command_option(command, "--maa_soa_jit_active_value_owners") != "32":
        raise RuntimeError(
            "frozen command no longer records the 32-owner baseline"
        )
    if command.count("--maa_soa_jit_pre_a_value_lookahead") != 1:
        raise RuntimeError("frozen command must enable pre-A exactly once")
    if "--cmd" not in command or command[command.index("--cmd") + 1] != str(
        FROZEN_SOURCE / "inputs/hybrid"
    ):
        raise RuntimeError(
            "frozen command does not bind the accepted GZP guest"
        )
    if (
        command[command.index("--options") + 1]
        != f"{FULL_ELEMENTS} {FROZEN_SOURCE / 'checkpoints/volume_only_soa_jit/selector.txt'}"
    ):
        raise RuntimeError("frozen command does not bind the full fixed input")
    return command


def frozen_config(command: list[str]) -> Path:
    configs = [
        Path(item)
        for item in command
        if item.endswith("/configs/deprecated/example/se.py")
    ]
    if len(configs) != 1:
        raise RuntimeError("frozen command must identify exactly one se.py")
    return configs[0]


def checkpoint_file() -> Path:
    files = sorted(CHECKPOINT.glob("cpt.*/m5.cpt"))
    if len(files) != 1:
        raise RuntimeError(
            "accepted checkpoint must contain exactly one m5.cpt"
        )
    return files[0]


def materialize_command(
    base: list[str], gem5: Path, outdir: Path, owners: int
) -> list[str]:
    command = list(base)
    command[0] = str(gem5.resolve())
    command[
        command.index(
            next(item for item in command if item.startswith("--outdir="))
        )
    ] = f"--outdir={outdir}"
    owner = next(
        item
        for item in command
        if item.startswith("--maa_soa_jit_active_value_owners=")
    )
    command[
        command.index(owner)
    ] = f"--maa_soa_jit_active_value_owners={owners}"
    return command


def normalized_command(command: list[str]) -> list[str]:
    return [
        "--outdir=<RUN>"
        if item.startswith("--outdir=")
        else "--maa_soa_jit_active_value_owners=<OWNERS>"
        if item.startswith("--maa_soa_jit_active_value_owners=")
        else item
        for item in command
    ]


def plan(args: argparse.Namespace, base: list[str]) -> dict[str, object]:
    config = frozen_config(base)
    return {
        "schema": "dx100.gzp_owner_pre_a_same_checkpoint.v1",
        "scope": "exact full GZP accepted row-directed pre-A hybrid owner-capacity pair",
        "elements": FULL_ELEMENTS,
        "replicas_per_arm": len(REPLICAS),
        "parallel_restores": len(REPLICAS) * len(ARMS),
        "timeout_seconds": None,
        "arms": [
            {"name": name, "maa_soa_jit_active_value_owners": owners}
            for name, owners in ARMS
        ],
        "treatment_delta": "maa_soa_jit_active_value_owners=32 versus 64 only",
        "shared_checkpoint": str(CHECKPOINT),
        "frozen_pair": str(FROZEN_PAIR),
        "frozen_command": str(TEMPLATE_COMMAND),
        "frozen_config": str(config),
        "pre_a_enabled": True,
        "expected_output_hash": EXPECTED_OUTPUT_HASH,
        "host_time_metric_authorized": False,
        "simulated_metric": "simTicks",
    }


def fields(line: str) -> dict[str, str]:
    return dict(token.split("=", 1) for token in line.split() if "=" in token)


def exactly_one(lines: list[str], prefix: str) -> dict[str, str]:
    found = [line for line in lines if line.startswith(prefix)]
    if len(found) != 1:
        raise RuntimeError(f"expected one {prefix}, found {len(found)}")
    return fields(found[0])


def parse_pair(value: str, label: str) -> tuple[int, int]:
    parts = value.split("/")
    if len(parts) != 2:
        raise RuntimeError(f"invalid {label}: {value}")
    return int(parts[0]), int(parts[1])


def first_stats(path: Path) -> dict[str, int]:
    stats: dict[str, int] = {}
    active = False
    complete = False
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if not active and not complete:
                active = True
            continue
        if active and line.startswith("---------- End Simulation Statistics"):
            complete = True
            break
        if active:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    stats[parts[0]] = int(float(parts[1]))
                except (ValueError, OverflowError):
                    pass
    if not complete:
        raise RuntimeError(f"missing complete first stats window: {path}")
    return stats


def stat(stats: dict[str, int], suffix: str) -> int:
    values = [value for name, value in stats.items() if name.endswith(suffix)]
    if len(values) != 1:
        raise RuntimeError(
            f"expected one stats suffix {suffix}, got {len(values)}"
        )
    return values[0]


TRACE_STAT = {
    "IND_SoaJitSelected": "selected",
    "IND_SoaJitPredicateRejected": "rejected",
    "IND_SoaJitAReadIssues": "a_reads_issue",
    "IND_SoaJitAReadResponses": "a_reads_response",
    "IND_SoaJitValueReadIssues": "value_reads_issue",
    "IND_SoaJitValueReadResponses": "value_reads_response",
    "IND_SoaJitValueFills": "fills",
    "IND_SoaJitValueCachedResponses": "cached",
    "IND_SoaJitValueDeliveries": "deliveries",
    "IND_SoaJitLookaheadIssues": "lookahead_issue",
    "IND_SoaJitLookaheadResponses": "lookahead_response",
    "IND_SoaJitPreAValueIssues": "pre_a_issue",
    "IND_SoaJitPreAValueReadyAtAResponse": "pre_a_ready",
    "IND_SoaJitPreAValueUses": "pre_a_uses",
    "IND_SoaJitAliasesApplied": "aliases",
    "IND_SoaJitAWriteIssues": "a_writes_issue",
    "IND_SoaJitAWriteResponses": "a_writes_response",
    "IND_SoaJitValueEvictions": "evictions",
    "IND_SoaJitValueStalls": "value_stalls",
    "IND_SoaJitContextStalls": "context_stalls",
}


def trace_rows(path: Path, owners: int) -> list[dict[str, int]]:
    rows: list[dict[str, int]] = []
    for line in path.read_text(errors="replace").splitlines():
        if "event=soa_jit_complete" not in line or "terminal=1" not in line:
            continue
        event = fields(line)
        required = (
            "selected",
            "predicate_rejected",
            "a_reads",
            "value_reads",
            "fills",
            "cached",
            "deliveries",
            "aliases",
            "lookahead",
            "pre_a",
            "a_writes",
            "active_value_owners",
            "evictions",
            "value_stalls",
            "stalls",
            "pre_a_enable",
            "terminal",
        )
        if any(name not in event for name in required):
            raise RuntimeError("incomplete terminal SoA/JIT trace ledger")
        if (
            event.get("predicate_mode") != "separate_array"
            or event["pre_a_enable"] != "1"
            or event["terminal"] != "1"
        ):
            raise RuntimeError(
                "terminal trace is not the accepted pre-A GZP mode"
            )
        if (
            int(event["active_value_owners"]) != owners
            or int(event.get("max_value_owners", "-1")) != 128
        ):
            raise RuntimeError(
                "terminal trace owner capacity does not match arm"
            )
        if (
            int(event["selected"]) + int(event["predicate_rejected"])
            != WINDOW_ELEMENTS
        ):
            raise RuntimeError(
                "terminal trace does not close the logical 16K window"
            )
        row: dict[str, int] = {
            "selected": int(event["selected"]),
            "rejected": int(event["predicate_rejected"]),
            "evictions": int(event["evictions"]),
            "value_stalls": int(event["value_stalls"]),
            "context_stalls": int(event["stalls"]),
        }
        for key, value in (
            ("a_reads", event["a_reads"]),
            ("value_reads", event["value_reads"]),
            ("lookahead", event["lookahead"]),
            ("a_writes", event["a_writes"]),
        ):
            issued, responded = parse_pair(value, key)
            row[key + "_issue"] = issued
            row[key + "_response"] = responded
            if issued != responded:
                raise RuntimeError(f"unclosed {key} ledger")
        pre_issue, pre_ready, pre_uses = (
            int(value) for value in event["pre_a"].split("/")
        )
        row.update(
            {
                "pre_a_issue": pre_issue,
                "pre_a_ready": pre_ready,
                "pre_a_uses": pre_uses,
            }
        )
        row.update(
            {
                name: int(event[name])
                for name in ("fills", "cached", "deliveries", "aliases")
            }
        )
        if (
            row["value_reads_issue"] != row["fills"]
            or row["fills"] != row["cached"]
        ):
            raise RuntimeError("unclosed value read/fill ledger")
        if (
            row["deliveries"] != row["selected"]
            or row["aliases"] != row["selected"]
        ):
            raise RuntimeError("unclosed ordered value delivery ledger")
        if (
            row["pre_a_issue"] <= 0
            or row["pre_a_issue"] != row["pre_a_uses"]
            or row["pre_a_ready"] > row["pre_a_issue"]
        ):
            raise RuntimeError("unclosed active pre-A ledger")
        rows.append(row)
    if len(rows) != FULL_ELEMENTS // WINDOW_ELEMENTS:
        raise RuntimeError(f"expected 61 terminal traces, got {len(rows)}")
    return rows


def analyze_run(
    run: Path, arm: str, replica: str, owners: int
) -> dict[str, int | str]:
    if (run / "restore.exit").read_text().strip() != "0":
        raise RuntimeError(f"{arm}/{replica}: restore exit is not zero")
    log = (run / "restore.log").read_text(errors="replace")
    if len(
        re.findall(
            r"Exiting @ tick \d+ because m5_exit instruction encountered", log
        )
    ) != 1 or FATAL_RE.search(log):
        raise RuntimeError(
            f"{arm}/{replica}: restore did not terminate cleanly"
        )
    lines = log.splitlines()
    output = exactly_one(lines, "UME_OUTPUT_FP ")
    reference = exactly_one(lines, "UME_REFERENCE_PASS ")
    terminal = exactly_one(lines, "UME_GZP_TERMINAL ")
    if (
        output.get("output_hash") != EXPECTED_OUTPUT_HASH
        or output.get("nonfinite") != "0"
    ):
        raise RuntimeError(f"{arm}/{replica}: output hash gate failed")
    if (
        reference.get("point_volume_errors") != "0"
        or reference.get("point_gradient_errors") != "0"
    ):
        raise RuntimeError(f"{arm}/{replica}: reference gate failed")
    if (
        terminal.get("result") != "PASS"
        or terminal.get("treatment") != "volume_only_soa_jit"
        or terminal.get("full_windows") != "0"
        or terminal.get("volume_only_windows") != "61"
        or terminal.get("published_predicates") != "0"
        or terminal.get("published_gradient_values") != "0"
    ):
        raise RuntimeError(f"{arm}/{replica}: GZP terminal gate failed")
    trace = trace_rows(run / "gem5" / "virtual_trace.log", owners)
    totals = {
        key: sum(row[key] for row in trace) for key in TRACE_STAT.values()
    }
    stats = first_stats(run / "gem5" / "stats.txt")
    for suffix, key in TRACE_STAT.items():
        if stat(stats, suffix) != totals[key]:
            raise RuntimeError(
                f"{arm}/{replica}: stats/trace mismatch for {suffix}"
            )
    if stat(stats, "IND_SoaJitTerminalCompletions") != len(trace) or stat(
        stats, "IND_SoaJitActiveValueOwners"
    ) != owners * len(trace):
        raise RuntimeError(
            f"{arm}/{replica}: terminal owner ledger did not close"
        )
    return {
        "arm": arm,
        "replica": replica,
        "owners": owners,
        "simTicks": stat(stats, "simTicks"),
        "output_hash": output["output_hash"],
        "terminal_windows": len(trace),
        "value_cache_hwm": stat(stats, "IND_SoaJitValueCacheHighWater"),
        **totals,
    }


def main() -> int:
    args = parse_args()
    base = template()
    campaign_plan = plan(args, base)
    if not args.execute:
        print(json.dumps(campaign_plan, indent=2, sort_keys=True))
        return 0
    required = (
        args.gem5,
        frozen_config(base),
        FROZEN_SOURCE / "inputs/hybrid",
        RAMULATOR,
        checkpoint_file(),
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SystemExit(
            "missing frozen execution inputs: " + ", ".join(missing)
        )
    if not os.access(args.gem5, os.X_OK) or args.outdir.exists():
        raise SystemExit("gem5 must be executable and --outdir must not exist")
    if (
        sha256(args.gem5) != args.expected_gem5_sha256
        or sha256(frozen_config(base)) != EXPECTED_CONFIG_SHA256
        or sha256(FROZEN_SOURCE / "inputs/hybrid") != EXPECTED_GUEST_SHA256
        or sha256(RAMULATOR) != EXPECTED_RAMULATOR_SHA256
    ):
        raise SystemExit(
            "frozen binary/config/guest/ramulator identity mismatch"
        )
    args.outdir.mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "OMP_NUM_THREADS": "4",
            "OMP_PROC_BIND": "false",
            "LD_LIBRARY_PATH": str(RAMULATOR.parent)
            + (
                ":" + env["LD_LIBRARY_PATH"]
                if env.get("LD_LIBRARY_PATH")
                else ""
            ),
        }
    )
    runs: list[dict[str, object]] = []
    processes: list[
        tuple[dict[str, object], subprocess.Popen[bytes], object]
    ] = []
    for arm, owners in ARMS:
        for replica in REPLICAS:
            run = args.outdir / "arms" / arm / replica
            gem5_out = run / "gem5"
            run.mkdir(parents=True)
            command = materialize_command(base, args.gem5, gem5_out, owners)
            atomic_json(run / "restore.command.json", command)
            atomic_text(
                run / "restore.command.txt", shlex.join(command) + "\n"
            )
            record = {
                "arm": arm,
                "replica": replica,
                "owners": owners,
                "command_sha256": sha256(run / "restore.command.json"),
                "command": command,
            }
            log = (run / "restore.log").open("wb")
            processes.append(
                (
                    record,
                    subprocess.Popen(
                        command, stdout=log, stderr=subprocess.STDOUT, env=env
                    ),
                    log,
                )
            )
    for record, process, log in processes:
        rc = (
            process.wait()
        )  # Deliberately no timeout: full simulations are not time-capped.
        log.close()
        run = (
            args.outdir / "arms" / str(record["arm"]) / str(record["replica"])
        )
        atomic_text(run / "restore.exit", f"{rc}\n")
        runs.append(record)
    atomic_json(
        args.outdir / "exits.json",
        {
            f"{item['arm']}/{item['replica']}": int(
                (
                    args.outdir
                    / "arms"
                    / str(item["arm"])
                    / str(item["replica"])
                    / "restore.exit"
                ).read_text()
            )
            for item in runs
        },
    )
    try:
        normal = normalized_command(runs[0]["command"])
        if (
            any(normalized_command(item["command"]) != normal for item in runs)
            or {
                command_option(
                    item["command"], "--maa_soa_jit_active_value_owners"
                )
                for item in runs
                if item["arm"] == "owners-32"
            }
            != {"32"}
            or {
                command_option(
                    item["command"], "--maa_soa_jit_active_value_owners"
                )
                for item in runs
                if item["arm"] == "owners-64"
            }
            != {"64"}
        ):
            raise RuntimeError(
                "materialized commands differ outside owner capacity/outdir"
            )
        rows = [
            analyze_run(
                args.outdir / "arms" / str(item["arm"]) / str(item["replica"]),
                str(item["arm"]),
                str(item["replica"]),
                int(item["owners"]),
            )
            for item in runs
        ]
        for arm, owners in ARMS:
            arm_rows = [row for row in rows if row["arm"] == arm]
            if (
                len(arm_rows) != 2
                or len({row["output_hash"] for row in arm_rows}) != 1
                or len({row["simTicks"] for row in arm_rows}) != 1
            ):
                raise RuntimeError(
                    f"{arm}: replicas do not close deterministically"
                )
        semantic_keys = (
            "output_hash",
            "terminal_windows",
            "selected",
            "rejected",
            "deliveries",
            "aliases",
            "a_reads_issue",
            "a_reads_response",
            "a_writes_issue",
            "a_writes_response",
        )
        comparisons = []
        promote = True
        for replica in REPLICAS:
            control = next(
                row
                for row in rows
                if row["arm"] == "owners-32" and row["replica"] == replica
            )
            treatment = next(
                row
                for row in rows
                if row["arm"] == "owners-64" and row["replica"] == replica
            )
            if any(control[key] != treatment[key] for key in semantic_keys):
                raise RuntimeError(
                    f"{replica}: owner arms changed semantic work or A traffic"
                )
            wins = (
                treatment["simTicks"] < control["simTicks"]
                and treatment["evictions"] < control["evictions"]
            )
            promote &= wins
            comparisons.append(
                {
                    "replica": replica,
                    "control_simTicks": control["simTicks"],
                    "treatment_simTicks": treatment["simTicks"],
                    "speedup": control["simTicks"] / treatment["simTicks"],
                    "control_evictions": control["evictions"],
                    "treatment_evictions": treatment["evictions"],
                    "wins": wins,
                }
            )
        matrix = {
            "rows": rows,
            "simulated_metric": "simTicks",
            "host_time_metric_authorized": False,
        }
        atomic_json(args.outdir / "matrix.json", matrix)
        with (args.outdir / "matrix.tsv").open("w", newline="") as output:
            writer = csv.DictWriter(
                output, fieldnames=list(rows[0]), delimiter="\t"
            )
            writer.writeheader()
            writer.writerows(rows)
        manifest = {
            **campaign_plan,
            "gem5": {
                "path": str(args.gem5.resolve()),
                "sha256": sha256(args.gem5),
            },
            "frozen": {
                "command_sha256": sha256(TEMPLATE_COMMAND),
                "config_sha256": sha256(frozen_config(base)),
                "guest_sha256": sha256(FROZEN_SOURCE / "inputs/hybrid"),
                "ramulator_sha256": sha256(RAMULATOR),
            },
            "runs": [
                {key: value for key, value in item.items() if key != "command"}
                for item in runs
            ],
        }
        atomic_json(args.outdir / "manifest.json", manifest)
        atomic_json(
            args.outdir / "decision.json",
            {
                "decision": "PROMOTE" if promote else "REJECT",
                "reason": (
                    "owner64 lowers simTicks and evictions in both exact replicas"
                    if promote
                    else "owner64 does not lower simTicks and evictions in both exact replicas"
                ),
                "treatment_delta": campaign_plan["treatment_delta"],
                "comparisons": comparisons,
            },
        )
    except Exception as error:
        atomic_json(
            args.outdir / "decision.json",
            {"decision": "REJECT", "reason": str(error)},
        )
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
