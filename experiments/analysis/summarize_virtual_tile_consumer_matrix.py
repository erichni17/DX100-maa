#!/usr/bin/env python3
"""Fail-closed summary of matched virtual-tile consumer gem5 runs.

The input is deliberately individual run directories rather than a campaign
directory: this makes every comparison and every named reference explicit.
Only simulated ticks are used for performance arithmetic.
"""

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

M5_EXIT_RE = re.compile(
    r"^Exiting @ tick [0-9]+ because m5_exit instruction encountered$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COUNTERS = (
    "index_words",
    "index_hwm",
    "write_issues",
    "write_completions",
    "indirect_spd_reads",
    "stream_spd_reads",
    "stream_writes",
    "alu_compute_cycles",
)


def _assignment(value: str, option: str) -> tuple[str, str]:
    if value.count("=") != 1:
        raise ValueError(f"{option} must be NAME=VALUE: {value!r}")
    name, assigned = value.split("=", 1)
    if not name or not assigned:
        raise ValueError(
            f"{option} must have nonempty NAME and VALUE: {value!r}"
        )
    return name, assigned


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"missing required evidence: {path}")
    return path.read_text(encoding="utf-8", errors="strict")


def _read_exit(case: Path, name: str) -> None:
    value = _read_text(case / name).strip()
    if value != "0":
        raise ValueError(f"{case}: {name} must be exactly 0, got {value!r}")


def _read_result(case: Path) -> dict[str, str]:
    path = case / "result.tsv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    if len(rows) != 1 or not rows[0] or None in rows[0]:
        raise ValueError(
            f"{case}: result.tsv must contain exactly one valid row"
        )
    row = rows[0]
    required = ("case", "output_hash", "simTicks", *COUNTERS)
    missing = [key for key in required if not row.get(key)]
    if missing:
        raise ValueError(
            f"{case}: result.tsv missing required fields: {', '.join(missing)}"
        )
    try:
        if int(row["simTicks"]) <= 0:
            raise ValueError
        for key in COUNTERS:
            int(row[key])
    except ValueError as error:
        raise ValueError(
            f"{case}: result.tsv has invalid numeric evidence"
        ) from error
    return row


def _read_manifest_commit(case: Path) -> str:
    values: dict[str, str] = {}
    for line in _read_text(case / "manifest.txt").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    commit = values.get("source_commit", "")
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", commit):
        raise ValueError(
            f"{case}: manifest source_commit is missing or invalid"
        )
    return commit.lower()


def _artifact_hashes(case: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for line in _read_text(case / "artifact_sha256.txt").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2 or not SHA256_RE.fullmatch(parts[0]):
            raise ValueError(
                f"{case}: invalid artifact_sha256 entry: {line!r}"
            )
        name = Path(parts[1].strip()).name
        if name in hashes:
            raise ValueError(f"{case}: duplicate artifact basename: {name}")
        hashes[name] = parts[0]
    gem5 = hashes.get("gem5.opt")
    binaries = [
        value
        for name, value in hashes.items()
        if re.fullmatch(r"test_virtual_tile_consumer(?:_T[0-9]+)?", name)
    ]
    if gem5 is None or len(binaries) != 1:
        raise ValueError(
            f"{case}: need one gem5.opt and one virtual-tile workload binary hash"
        )
    return {"gem5_sha256": gem5, "workload_sha256": binaries[0]}


def _validate_log(case: Path) -> None:
    lines = _read_text(case / "restore.log").splitlines()
    matches = sum(bool(M5_EXIT_RE.fullmatch(line)) for line in lines)
    if matches != 1:
        raise ValueError(
            f"{case}: restore.log needs exactly one m5_exit terminal marker"
        )


def load_case(label: str, directory: Path | str) -> dict[str, object]:
    directory = Path(directory).resolve()
    if not directory.is_dir():
        raise ValueError(f"{label}: run directory does not exist: {directory}")
    _read_exit(directory, "checkpoint.exit")
    _read_exit(directory, "restore.exit")
    _validate_log(directory)
    result = _read_result(directory)
    return {
        "label": label,
        "path": str(directory),
        "case": result["case"],
        "commit": _read_manifest_commit(directory),
        "output_hash": result["output_hash"],
        "simTicks": int(result["simTicks"]),
        "counters": {key: int(result[key]) for key in COUNTERS},
        "hashes": _artifact_hashes(directory),
        "evidence_qualified": True,
    }


def summarize(
    cases: dict[str, Path], references: dict[str, str]
) -> dict[str, object]:
    if not cases:
        raise ValueError("at least one --case is required")
    if not references:
        raise ValueError("at least one --reference is required")
    loaded = {
        label: load_case(label, path) for label, path in sorted(cases.items())
    }
    for name, label in references.items():
        if label not in loaded:
            raise ValueError(
                f"reference {name!r} names unknown case {label!r}"
            )
    outputs = {str(case["output_hash"]) for case in loaded.values()}
    gem5_hashes = {
        str(case["hashes"]["gem5_sha256"]) for case in loaded.values()
    }
    workload_hashes = {
        str(case["hashes"]["workload_sha256"]) for case in loaded.values()
    }
    if len(outputs) != 1:
        raise ValueError("output hashes differ across cases")
    if len(gem5_hashes) != 1:
        raise ValueError("gem5 SHA-256 differs across cases")
    if len(workload_hashes) != 1:
        raise ValueError("workload binary SHA-256 differs across cases")
    comparisons = []
    for reference_name, reference_label in sorted(references.items()):
        reference = loaded[reference_label]
        for candidate_label, candidate in loaded.items():
            comparisons.append(
                {
                    "reference": reference_name,
                    "reference_label": reference_label,
                    "candidate": candidate_label,
                    "reference_simTicks": reference["simTicks"],
                    "candidate_simTicks": candidate["simTicks"],
                    "latency_delta": candidate["simTicks"]
                    / reference["simTicks"]
                    - 1,
                    "speedup": reference["simTicks"] / candidate["simTicks"],
                }
            )
    return {
        "schema_version": 1,
        "qualification": {
            "qualified": True,
            "requirements": [
                "checkpoint.exit=0",
                "restore.exit=0",
                "one valid result.tsv row",
                "one m5_exit terminal marker",
                "exact output hash equality",
                "identical gem5 and workload SHA-256",
                "performance uses simTicks only",
            ],
        },
        "references": dict(sorted(references.items())),
        "cases": list(loaded.values()),
        "comparisons": comparisons,
    }


def render_markdown(summary: dict[str, object]) -> str:
    lines = [
        "# Virtual-tile consumer matrix",
        "",
        "Qualified: yes — checkpoint/restore exits, one result row, one m5_exit marker, exact output, and gem5/workload hashes all match; performance uses simTicks only.",
        "",
        "| Case | simTicks | Commit | Output hash | gem5 SHA-256 | Workload SHA-256 | Path |",
        "|---|---:|---|---|---|---|---|",
    ]
    for case in summary["cases"]:
        hashes = case["hashes"]
        lines.append(
            f"| {case['label']} | {case['simTicks']} | {case['commit']} | "
            f"{case['output_hash']} | {hashes['gem5_sha256']} | "
            f"{hashes['workload_sha256']} | `{case['path']}` |"
        )
    lines += ["", "| Case | Selected mechanism counters |", "|---|---|"]
    for case in summary["cases"]:
        counters = ", ".join(
            f"{key}={value}" for key, value in case["counters"].items()
        )
        lines.append(f"| {case['label']} | {counters} |")
    lines += [
        "",
        "| Reference | Candidate | Latency delta | Speedup |",
        "|---|---|---:|---:|",
    ]
    for item in summary["comparisons"]:
        lines.append(
            f"| {item['reference']} ({item['reference_label']}) | {item['candidate']} | {item['latency_delta']:+.6f} | {item['speedup']:.6f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", action="append", default=[], metavar="LABEL=RUN_DIR"
    )
    parser.add_argument(
        "--reference", action="append", default=[], metavar="NAME=LABEL"
    )
    parser.add_argument("--json", type=Path, required=True, dest="json_path")
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    cases = dict(_assignment(value, "--case") for value in args.case)
    references = dict(
        _assignment(value, "--reference") for value in args.reference
    )
    if len(cases) != len(args.case) or len(references) != len(args.reference):
        raise SystemExit("duplicate case label or reference name")
    summary = summarize(cases, references)
    args.json_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    markdown = render_markdown(summary)
    if args.markdown:
        args.markdown.write_text(markdown, encoding="utf-8")
    print(markdown, end="")


if __name__ == "__main__":
    main()
