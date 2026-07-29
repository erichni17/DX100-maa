#!/usr/bin/env python3

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

LABELS = ("fused16", "compact16", "direct4")


def fail(message: str) -> None:
    raise SystemExit(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ratio(candidate: int, reference: int) -> float:
    if candidate <= 0 or reference <= 0:
        fail("ROI ticks must be positive")
    return candidate / reference


def geometric_mean(values: list[float]) -> float:
    if not values:
        fail("cannot compute an empty geometric mean")
    return math.exp(sum(math.log(value) for value in values) / len(values))


def read_comparison(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        fail(f"missing comparison table: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        rows = {
            row["label"]: row for row in csv.DictReader(stream, delimiter="\t")
        }
    if set(rows) != set(LABELS):
        fail(f"{path} labels are {sorted(rows)}, expected {list(LABELS)}")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("campaign", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest_root = args.manifest.parent
    gathers = [
        config
        for config in manifest.get("configurations", [])
        if config.get("kernel") == "gather"
    ]
    if len(gathers) != 14:
        fail(f"expected 14 FLAG gathers, found {len(gathers)}")

    output_rows = []
    for config in gathers:
        config_id = config["id"]
        case = args.campaign / "cases" / config_id
        for marker in (
            case / "flag_gather_case.pass",
            case / "comparison" / "xrage_comparison.pass",
        ):
            if not marker.is_file():
                fail(f"missing validation marker: {marker}")
        digest_dir = case / "issue-comparison"
        if not any(
            (digest_dir / marker).is_file()
            for marker in (
                "maa_issue_digest_comparison.pass",
                "maa_issue_digest_per_instruction.pass",
            )
        ):
            fail(f"missing issue-digest validation marker: {digest_dir}")
        rows = read_comparison(case / "comparison" / "xrage_comparison.tsv")
        input_path = manifest_root / config["input"]
        if sha256(input_path) != config["input_sha256"]:
            fail(f"input checksum changed for {config_id}")
        input_hashes = {row["input_sha256"] for row in rows.values()}
        if input_hashes != {config["input_sha256"]}:
            fail(f"comparison input hash mismatch for {config_id}")

        ticks = {label: int(rows[label]["roi_simTicks"]) for label in LABELS}
        output_rows.append(
            {
                "id": config_id,
                "pattern_length": int(config["pattern_length"]),
                "pattern_max": int(config["pattern_max"]),
                "fused16_ticks": ticks["fused16"],
                "compact16_ticks": ticks["compact16"],
                "direct4_ticks": ticks["direct4"],
                "compact_vs_fused": ratio(
                    ticks["compact16"], ticks["fused16"]
                ),
                "direct_vs_fused": ratio(ticks["direct4"], ticks["fused16"]),
                "direct_vs_compact": ratio(
                    ticks["direct4"], ticks["compact16"]
                ),
            }
        )

    summaries = {}
    for name in (
        "compact_vs_fused",
        "direct_vs_fused",
        "direct_vs_compact",
    ):
        values = [row[name] for row in output_rows]
        summaries[name] = {
            "geomean_ratio": geometric_mean(values),
            "minimum_ratio": min(values),
            "maximum_ratio": max(values),
        }

    args.output_dir.mkdir(parents=True, exist_ok=False)
    with (args.output_dir / "flag_gather_generalization.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream, fieldnames=output_rows[0].keys(), delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(output_rows)

    report = {
        "manifest_sha256": sha256(args.manifest),
        "configurations": output_rows,
        "summary": summaries,
    }
    (args.output_dir / "flag_gather_generalization.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    markdown = [
        "# FLAG Gather Generalization",
        "",
        "All 14 gathers passed exact-output, artifact, terminal, configuration, "
        "two-channel DRAM, and complete per-instruction source-request digest "
        "checks. Independent instruction completion order is not constrained.",
        "",
        "| Configuration | Length | Compact vs. fused latency | Direct vs. fused latency | Direct vs. compact latency |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in output_rows:
        markdown.append(
            f"| {row['id']} | {row['pattern_length']:,} | "
            f"{100 * (row['compact_vs_fused'] - 1):+.3f}% | "
            f"{100 * (row['direct_vs_fused'] - 1):+.3f}% | "
            f"{100 * (row['direct_vs_compact'] - 1):+.3f}% |"
        )
    markdown.extend(["", "## Equal-Weight Geometric Mean", ""])
    for name, summary in summaries.items():
        markdown.append(
            f"- `{name}`: {100 * (summary['geomean_ratio'] - 1):+.3f}% latency"
        )
    (args.output_dir / "flag_gather_generalization.md").write_text(
        "\n".join(markdown) + "\n", encoding="utf-8"
    )
    (args.output_dir / "flag_gather_generalization.pass").touch()
    print(
        "PASS FLAG gather generalization: "
        f"{len(output_rows)} configurations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
