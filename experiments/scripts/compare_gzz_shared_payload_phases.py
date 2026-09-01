#!/usr/bin/env python3
"""Compare two completed GZZ strict arms without rerunning either workload."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re

COUNTERS = (
    "simTicks",
    "IND_StrictTwoPhaseAIssueCycles",
    "IND_StrictTwoPhaseBackingCycles",
    "IND_StrictTwoPhaseBFetchCycles",
    "IND_StrictTwoPhaseConsumerCycles",
    "IND_VirtResponseSlotHighWater",
    "IND_VirtResponseWordHighWater",
    "IND_VirtSharedPayloadHighWater",
    "IND_VirtBuildRounds",
    "IND_VirtFanoutScanCycles",
    "IND_VirtFanoutScanWaitCycles",
)


def digest(path: pathlib.Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def parse_first_stats(path: pathlib.Path) -> dict[str, float]:
    section = 0
    values: dict[str, list[float]] = {name: [] for name in COUNTERS}
    for line in path.read_text().splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            section += 1
            continue
        if line.startswith("---------- End Simulation Statistics"):
            if section == 1:
                break
            continue
        if section != 1:
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        key = fields[0]
        for name in COUNTERS:
            if key == name or key.endswith("_" + name):
                values[name].append(float(fields[1]))
    result: dict[str, float] = {}
    for name, matches in values.items():
        if (
            name
            in (
                "IND_VirtSharedPayloadHighWater",
                "IND_VirtFanoutScanCycles",
                "IND_VirtFanoutScanWaitCycles",
            )
            and not matches
        ):
            result[name] = 0.0
            continue
        if not matches or (name == "simTicks" and len(matches) != 1):
            raise ValueError(
                f"expected first-window {name}, found {len(matches)}"
            )
        result[name] = matches[0] if name == "simTicks" else sum(matches)
    return result


def parse_event(path: pathlib.Path, event: str) -> dict[str, str]:
    matches = []
    pattern = re.compile(rf"(?:^|\s)event={re.escape(event)}(?:\s|$)")
    for line in path.read_text().splitlines():
        if pattern.search(line):
            matches.append(line)
    if len(matches) != 1:
        raise ValueError(f"expected one {event} event, found {len(matches)}")
    return {
        key: value
        for key, value in re.findall(r"([A-Za-z0-9_]+)=([^\s]+)", matches[0])
    }


def ratio(candidate: float, reference: float) -> float:
    if reference <= 0:
        raise ValueError("reference metric must be positive")
    return candidate / reference


def compare(
    reference_stats_path: pathlib.Path,
    reference_trace_path: pathlib.Path,
    candidate_stats_path: pathlib.Path,
    candidate_trace_path: pathlib.Path,
) -> dict:
    reference = parse_first_stats(reference_stats_path)
    candidate = parse_first_stats(candidate_stats_path)
    reference_timing = parse_event(
        reference_trace_path, "strict_two_phase_timing"
    )
    candidate_timing = parse_event(
        candidate_trace_path, "strict_two_phase_timing"
    )
    reference_shared = parse_event(
        reference_trace_path, "shared_result_payload_complete"
    )
    candidate_shared = parse_event(
        candidate_trace_path, "shared_result_payload_complete"
    )

    comparisons = {}
    for name in COUNTERS:
        if reference[name] == 0:
            comparisons[name] = {
                "reference": reference[name],
                "candidate": candidate[name],
                "ratio": None,
                "delta": candidate[name] - reference[name],
            }
        else:
            comparisons[name] = {
                "reference": reference[name],
                "candidate": candidate[name],
                "ratio": ratio(candidate[name], reference[name]),
                "delta": candidate[name] - reference[name],
            }

    mlp_collapse = (
        candidate["IND_VirtResponseSlotHighWater"]
        <= max(1.0, reference["IND_VirtResponseSlotHighWater"] / 8.0)
        and comparisons["IND_StrictTwoPhaseAIssueCycles"]["ratio"] > 2.0
        and comparisons["IND_StrictTwoPhaseBackingCycles"]["ratio"] > 2.0
    )
    stable_front_end = (
        abs(comparisons["IND_StrictTwoPhaseBFetchCycles"]["ratio"] - 1.0)
        <= 0.02
        and abs(comparisons["IND_StrictTwoPhaseConsumerCycles"]["ratio"] - 1.0)
        <= 0.02
    )
    return {
        "schema": "dx100.gzz_shared_payload_phase_comparison.v1",
        "historical_cross_binary": True,
        "performance_attribution": False,
        "diagnosis": {
            "stable_front_end_and_consumer": stable_front_end,
            "source_mlp_collapse": mlp_collapse,
            "classification": (
                "SOURCE_MLP_COLLAPSE"
                if mlp_collapse and stable_front_end
                else "UNRESOLVED"
            ),
        },
        "comparisons": comparisons,
        "trace": {
            "reference_timing": reference_timing,
            "candidate_timing": candidate_timing,
            "reference_shared": reference_shared,
            "candidate_shared": candidate_shared,
        },
        "artifacts": {
            "analyzer": {
                "path": str(pathlib.Path(__file__).resolve()),
                "sha256": digest(pathlib.Path(__file__).resolve()),
            },
            "reference_stats": {
                "path": str(reference_stats_path),
                "sha256": digest(reference_stats_path),
            },
            "reference_trace": {
                "path": str(reference_trace_path),
                "sha256": digest(reference_trace_path),
            },
            "candidate_stats": {
                "path": str(candidate_stats_path),
                "sha256": digest(candidate_stats_path),
            },
            "candidate_trace": {
                "path": str(candidate_trace_path),
                "sha256": digest(candidate_trace_path),
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-stats", type=pathlib.Path, required=True)
    parser.add_argument("--reference-trace", type=pathlib.Path, required=True)
    parser.add_argument("--candidate-stats", type=pathlib.Path, required=True)
    parser.add_argument("--candidate-trace", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    result = compare(
        args.reference_stats,
        args.reference_trace,
        args.candidate_stats,
        args.candidate_trace,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["diagnosis"], sort_keys=True))
    return 0 if result["diagnosis"]["classification"] != "UNRESOLVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
