#!/usr/bin/env python3
"""Validate the physical tile sweep and produce its source table and SVG."""

import argparse
import csv
import hashlib
import html
import json
import math
import re
from collections import Counter
from pathlib import Path

TILES = (1024, 2048, 4096, 8192, 16384, 32768, 65536)
TILE_LABELS = {
    1024: "1K",
    2048: "2K",
    4096: "4K",
    8192: "8K",
    16384: "16K",
    32768: "32K",
    65536: "64K",
}
TERMINAL_STATES = {"completed", "failed", "skipped"}
COLORS = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#000000",
    "#7A5195",
    "#EF5675",
    "#2F4B7C",
    "#7F7F7F",
)


def read_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text())


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text)
    temporary.replace(path)


def atomic_json(path, document):
    atomic_text(path, json.dumps(document, indent=2, sort_keys=True) + "\n")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_tsv(path):
    if not path.exists():
        return []
    with path.open(newline="") as source:
        return list(csv.DictReader(source, delimiter="\t"))


def stats_ticks(path):
    if not path.exists():
        return []
    values = []
    with path.open(errors="replace") as source:
        for line in source:
            fields = line.split()
            if len(fields) >= 2 and fields[0] == "simTicks":
                try:
                    values.append(int(fields[1]))
                except ValueError:
                    pass
    return values


def scan_log(path, oracle_kind):
    result = {
        "m5_exit": False,
        "panic_or_fatal": False,
        "is_exit_policy": False,
        "markers": [],
    }
    if not path.exists():
        return result
    patterns = {
        "bfs": re.compile(r"^BFS_FP .*invalid_chains=0 "),
        "sssp": re.compile(r"^SSSP_FINGERPRINT .*result=PASS$"),
        "bc": re.compile(r"^BC_VALIDATION_END result=PASS$"),
        "is": re.compile(r"^IS_VERIFY .*result=PASS$"),
        "cg": re.compile(r"^CG_FINGERPRINT mode=MAA .*result=PASS$"),
        "ume": re.compile(r"^(?:UME_OUTPUT_FP|UME_REFERENCE_PASS) "),
        "xrage": re.compile(r"^SPATTER_FP .*mismatches=0 "),
    }
    marker_pattern = patterns.get(oracle_kind)
    with path.open(errors="replace") as source:
        for line in source:
            line = line.rstrip("\n")
            lowered = line.lower()
            if re.search(r"Exiting @ tick .*m5_exit instruction encountered", line):
                result["m5_exit"] = True
            if "panic:" in lowered or "fatal:" in lowered:
                result["panic_or_fatal"] = True
            if line == "IS_ROI_EXIT_POLICY dump_stats_verify_m5_exit":
                result["is_exit_policy"] = True
            if marker_pattern and marker_pattern.search(line):
                result["markers"].append(line)
    return result


def parse_positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def select_latest(rows, filters, tile):
    matches = []
    for index, row in enumerate(rows):
        if parse_positive_int(row.get("tile")) != tile:
            continue
        if all(str(row.get(key, "")) == str(value) for key, value in filters.items()):
            matches.append((row.get("timestamp", ""), index, row))
    if not matches:
        return None
    return max(matches, key=lambda item: (item[0], item[1]))[2]


def task_state(state, task_id):
    if not state:
        return {"state": "pending", "reason": "workflow state not created"}
    return state.get("tasks", {}).get(
        task_id,
        {"state": "pending", "reason": "task absent from workflow state"},
    )


def validate_row(row, oracle_kind, expected_hash=None, prior=False):
    notes = []
    if row is None:
        return False, None, "", ["result row missing"]
    if row.get("rc") != "0":
        notes.append(f"wrapper rc={row.get('rc', 'missing')}")
    ticks = parse_positive_int(row.get("simTicks"))
    if ticks is None:
        notes.append("positive simTicks missing")
    outdir = Path(row.get("outdir", ""))
    if not row.get("outdir"):
        notes.append("outdir missing")
    recorded_stats_ticks = stats_ticks(outdir / "stats.txt")
    if prior:
        if ticks is not None and ticks not in recorded_stats_ticks:
            notes.append(
                f"simTicks absent from stats sections ({ticks} not in {recorded_stats_ticks})"
            )
    elif ticks is not None and (
        not recorded_stats_ticks or recorded_stats_ticks[0] != ticks
    ):
        first = recorded_stats_ticks[0] if recorded_stats_ticks else None
        notes.append(f"first-ROI simTicks mismatch ({first} != {ticks})")
    log = scan_log(outdir / "run.log", None if prior else oracle_kind)
    if not log["m5_exit"]:
        notes.append("clean m5_exit marker missing")
    if log["panic_or_fatal"]:
        notes.append("panic/fatal found in run.log")

    oracle_id = "accepted prior handoff; wrapper rc=0"
    if not prior:
        markers = log["markers"]
        if oracle_kind == "xrage":
            if len(markers) != 9:
                notes.append(f"expected 9 SPATTER_FP markers, found {len(markers)}")
            oracle_id = "\n".join(markers)
        elif oracle_kind == "ume":
            fingerprint = [
                line for line in markers if line.startswith("UME_OUTPUT_FP ")
            ]
            reference = [
                line for line in markers if line.startswith("UME_REFERENCE_PASS ")
            ]
            if len(fingerprint) != 1 or len(reference) != 1:
                notes.append(
                    "expected exactly one UME_OUTPUT_FP and UME_REFERENCE_PASS marker"
                )
            oracle_id = "\n".join(fingerprint + reference)
            if expected_hash is not None:
                expected = f"UME_OUTPUT_FP output_hash={expected_hash} nonfinite=0"
                if fingerprint != [expected]:
                    notes.append(
                        f"exact UME output fingerprint mismatch (expected {expected_hash})"
                    )
                if row.get("output_hash") != str(expected_hash):
                    notes.append("results.tsv output_hash mismatch")
        else:
            if len(markers) != 1:
                notes.append(
                    f"expected exactly one {oracle_kind} correctness marker, found {len(markers)}"
                )
            oracle_id = markers[0] if markers else ""
            if oracle_kind == "is" and not log["is_exit_policy"]:
                notes.append("corrected IS ROI-exit policy marker missing")
    return not notes, ticks, oracle_id, notes


def workflow_terminal(state):
    if not state or not state.get("tasks"):
        return False
    return all(
        record.get("state") in TERMINAL_STATES for record in state["tasks"].values()
    )


def workflow_counts(state):
    if not state:
        return {"missing": 1}
    return dict(
        Counter(item.get("state", "unknown") for item in state["tasks"].values())
    )


def specs(run_root, prior_gapbs, prior_hashjoin):
    return [
        {
            "id": "gapbs-pr-s22",
            "label": "GAPBS PageRank S22",
            "source": prior_gapbs,
            "filters": {"kernel": "pr", "scale": "22", "iters": "1"},
            "prior": True,
        },
        {
            "id": "hashjoin-prh-2m",
            "label": "HashJoin PRH 2M/2M",
            "sources": prior_hashjoin,
            "filters": {
                "kernel": "PRH",
                "r_size": "2000000",
                "s_size": "2000000",
            },
            "prior": True,
        },
        {
            "id": "hashjoin-pro-2m",
            "label": "HashJoin PRO 2M/2M",
            "sources": prior_hashjoin,
            "filters": {
                "kernel": "PRO",
                "r_size": "2000000",
                "s_size": "2000000",
            },
            "prior": True,
        },
        {
            "id": "gapbs-bfs-s22",
            "label": "GAPBS BFS S22",
            "source": run_root / "gapbs_recovery2/results.tsv",
            "filters": {"kernel": "bfs", "scale": "22", "iters": "1"},
            "oracle": "bfs",
            "task": "gapbs-bfs-t{tile}",
            "workflow": "recovery_normal",
            "compare_oracle": True,
        },
        {
            "id": "gapbs-sssp-s22",
            "label": "GAPBS SSSP S22",
            "source": run_root / "gapbs_recovery2/results.tsv",
            "filters": {"kernel": "sssp", "scale": "22", "iters": "1"},
            "oracle": "sssp",
            "task": "gapbs-sssp-t{tile}",
            "workflow": "recovery_normal",
            "compare_oracle": True,
        },
        {
            "id": "gapbs-bc-s22",
            "label": "GAPBS BC S22",
            "source": run_root / "gapbs_recovery2/results.tsv",
            "filters": {"kernel": "bc", "scale": "22", "iters": "1"},
            "oracle": "bc",
            "task": "gapbs-bc-t{tile}",
            "workflow": "recovery_normal",
        },
        {
            "id": "nas-is-full",
            "label": "NAS IS full class",
            "source": run_root / "is_recovery2/results.tsv",
            "filters": {"small": "0"},
            "oracle": "is",
            "task": "nas-is-t{tile}",
            "workflow": "recovery_is",
            "workflow_by_tile": {16384: "recovery_is_gate"},
        },
        {
            "id": "nas-cg",
            "label": "NAS CG",
            "source": run_root / "cg_recovery2/results.tsv",
            "filters": {},
            "oracle": "cg",
            "task": "nas-cg-t{tile}",
            "workflow": "recovery_normal",
            "compare_oracle": True,
        },
        {
            "id": "ume-gradzatp",
            "label": "UME gradzatp n=1M",
            "sources": [
                run_root / "ume_recovery2/results.tsv",
                run_root / "ume/results_oracle_v2.tsv",
            ],
            "filters": {"kernel": "gradzatp", "n": "1000000"},
            "oracle": "ume",
            "expected_hash": 11225737641199706160,
            "task": "ume-gradzatp-t{tile}",
            "workflow": "recovery_normal",
            "workflow_by_tile": {65536: "original"},
            "compare_oracle": True,
        },
        {
            "id": "ume-gradzatz",
            "label": "UME gradzatz n=1M",
            "sources": [
                run_root / "ume_recovery2/results.tsv",
                run_root / "ume/results_oracle_v2.tsv",
            ],
            "filters": {"kernel": "gradzatz", "n": "1000000"},
            "oracle": "ume",
            "expected_hash": 9234467062988358067,
            "task": "ume-gradzatz-t{tile}",
            "workflow": "recovery_normal",
            "workflow_by_tile": {65536: "original"},
            "compare_oracle": True,
        },
        {
            "id": "xrage-all",
            "label": "XRAGE all.json",
            "source": run_root / "xrage_recovery2/results.tsv",
            "filters": {},
            "oracle": "xrage",
            "task": "xrage-t{tile}",
            "workflow": "recovery_normal",
            "compare_oracle": True,
            "unsupported": {
                65536: "No 64K Spatter build target in the frozen artifact"
            },
        },
    ]


def build_rows(workload_specs, states):
    rows = []
    issues = []
    for spec in workload_specs:
        source_paths = spec.get("sources", [spec.get("source")])
        source_paths = [path for path in source_paths if path is not None]
        source_rows = []
        for path in source_paths:
            source_rows.extend(read_tsv(path))
        workload_rows = []
        for tile in TILES:
            base = {
                "workload_id": spec["id"],
                "workload": spec["label"],
                "tile": tile,
                "tile_label": TILE_LABELS[tile],
                "status": "pending",
                "simTicks": None,
                "performance_16k": None,
                "rc": "",
                "oracle": "",
                "evidence_source": ";".join(str(path) for path in source_paths),
                "outdir": "",
                "note": "",
            }
            unsupported = spec.get("unsupported", {}).get(tile)
            if unsupported:
                base.update(status="unsupported", note=unsupported)
                workload_rows.append(base)
                continue

            row = select_latest(source_rows, spec.get("filters", {}), tile)
            if not spec.get("prior"):
                workflow = spec.get("workflow_by_tile", {}).get(tile, spec["workflow"])
                state = task_state(states.get(workflow), spec["task"].format(tile=tile))
                current = state.get("state", "pending")
                if current != "completed":
                    note = state.get("reason", "")
                    if current == "failed":
                        note = f"workflow task failed rc={state.get('returncode', 'unknown')}"
                    base.update(status=current, note=note)
                    if row:
                        base.update(rc=row.get("rc", ""), outdir=row.get("outdir", ""))
                    workload_rows.append(base)
                    continue

            valid, ticks, oracle_id, notes = validate_row(
                row,
                spec.get("oracle"),
                expected_hash=spec.get("expected_hash"),
                prior=spec.get("prior", False),
            )
            base.update(
                status="valid" if valid else "failed",
                simTicks=ticks,
                rc=row.get("rc", "") if row else "",
                oracle=oracle_id,
                outdir=row.get("outdir", "") if row else "",
                note="; ".join(notes),
            )
            workload_rows.append(base)

        if spec.get("compare_oracle"):
            valid_oracles = {
                item["oracle"]
                for item in workload_rows
                if item["status"] == "valid" and item["oracle"]
            }
            if len(valid_oracles) > 1:
                issue = f"{spec['label']}: cross-tile oracle mismatch"
                issues.append(issue)
                for item in workload_rows:
                    if item["status"] == "valid":
                        item["status"] = "failed"
                        item["note"] = issue

        reference = next(
            (
                item["simTicks"]
                for item in workload_rows
                if item["tile"] == 16384 and item["status"] == "valid"
            ),
            None,
        )
        if reference is None:
            issues.append(f"{spec['label']}: valid 16K normalization point missing")
        else:
            for item in workload_rows:
                if item["status"] == "valid" and item["simTicks"]:
                    item["performance_16k"] = reference / item["simTicks"]
        rows.extend(workload_rows)
    return rows, issues


def write_source_tsv(path, rows):
    fields = (
        "workload_id",
        "workload",
        "tile",
        "tile_label",
        "status",
        "simTicks",
        "performance_16k",
        "rc",
        "oracle",
        "evidence_source",
        "outdir",
        "note",
    )
    temporary = path.with_name(f".{path.name}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", newline="") as output:
        writer = csv.DictWriter(output, fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            formatted = dict(row)
            if formatted["performance_16k"] is not None:
                formatted["performance_16k"] = f"{formatted['performance_16k']:.9f}"
            if formatted["simTicks"] is None:
                formatted["simTicks"] = ""
            writer.writerow(formatted)
    temporary.replace(path)


def svg_plot(path, workload_specs, rows):
    width, height = 1480, 900
    left, right, top, bottom = 105, 370, 70, 100
    plot_width = width - left - right
    plot_height = height - top - bottom
    valid = [
        item["performance_16k"]
        for item in rows
        if item["status"] == "valid" and item["performance_16k"] is not None
    ]
    y_max = max(1.2, max(valid, default=1.0) * 1.08)
    step = 0.2 if y_max <= 2.0 else 0.5
    y_max = math.ceil(y_max / step) * step

    def x(tile):
        return left + math.log2(tile / 1024) / 6 * plot_width

    def y(value):
        return top + (y_max - value) / y_max * plot_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        "<style>text{font-family:Arial,sans-serif;fill:#222}.axis{stroke:#222;stroke-width:1.5}.grid{stroke:#ddd;stroke-width:1}.curve{fill:none;stroke-width:2.4}.marker{stroke-width:1.5}</style>",
        f'<text x="{left}" y="32" font-size="24" font-weight="bold">DX100 physical tile-size sweep</text>',
        f'<text x="{left}" y="55" font-size="14">Performance = simTicks(16K) / simTicks(tile); higher is better</text>',
    ]
    tick = 0.0
    while tick <= y_max + 1e-9:
        yy = y(tick)
        parts.append(
            f'<line class="grid" x1="{left}" y1="{yy:.2f}" x2="{left + plot_width}" y2="{yy:.2f}"/>'
        )
        parts.append(
            f'<text x="{left - 12}" y="{yy + 5:.2f}" text-anchor="end" font-size="13">{tick:.1f}</text>'
        )
        tick += step
    highlight = x(16384)
    parts.append(
        f'<line x1="{highlight:.2f}" y1="{top}" x2="{highlight:.2f}" y2="{top + plot_height}" stroke="#666" stroke-width="2" stroke-dasharray="7 5"/>'
    )
    parts.append(
        f'<text x="{highlight + 7:.2f}" y="{top + 17}" font-size="12" fill="#555">original DX100 point</text>'
    )
    parts.append(
        f'<line class="axis" x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}"/>'
    )
    parts.append(
        f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}"/>'
    )
    for tile in TILES:
        xx = x(tile)
        parts.append(
            f'<line class="axis" x1="{xx:.2f}" y1="{top + plot_height}" x2="{xx:.2f}" y2="{top + plot_height + 6}"/>'
        )
        parts.append(
            f'<text x="{xx:.2f}" y="{top + plot_height + 25}" text-anchor="middle" font-size="14">{TILE_LABELS[tile]}</text>'
        )
    parts.append(
        f'<text x="{left + plot_width / 2:.2f}" y="{height - 25}" text-anchor="middle" font-size="16">Physical tile elements (log2 scale)</text>'
    )
    parts.append(
        f'<text x="27" y="{top + plot_height / 2:.2f}" transform="rotate(-90 27 {top + plot_height / 2:.2f})" text-anchor="middle" font-size="16">Relative performance</text>'
    )

    row_map = {(item["workload_id"], item["tile"]): item for item in rows}
    for index, spec in enumerate(workload_specs):
        color = COLORS[index % len(COLORS)]
        items = [row_map[(spec["id"], tile)] for tile in TILES]
        for first, second in zip(items, items[1:]):
            if (
                first["status"] == "valid"
                and second["status"] == "valid"
                and first["performance_16k"] is not None
                and second["performance_16k"] is not None
            ):
                parts.append(
                    f'<line class="curve" x1="{x(first["tile"]):.2f}" y1="{y(first["performance_16k"]):.2f}" '
                    f'x2="{x(second["tile"]):.2f}" y2="{y(second["performance_16k"]):.2f}" stroke="{color}"/>'
                )
        for item in items:
            xx = x(item["tile"])
            if item["status"] == "valid" and item["performance_16k"] is not None:
                yy = y(item["performance_16k"])
                parts.append(
                    f'<circle class="marker" cx="{xx:.2f}" cy="{yy:.2f}" r="4.2" fill="white" stroke="{color}"/>'
                )
            elif item["status"] != "valid":
                jitter = (index - (len(workload_specs) - 1) / 2) * 1.5
                yy = y(0.025 * y_max) + jitter
                if item["status"] == "unsupported":
                    status_color = "#888"
                elif item["status"] in {"pending", "running"}:
                    status_color = "#E69F00"
                else:
                    status_color = "#D62728"
                parts.append(
                    f'<line x1="{xx - 4:.2f}" y1="{yy - 4:.2f}" x2="{xx + 4:.2f}" y2="{yy + 4:.2f}" stroke="{status_color}" stroke-width="2"/>'
                )
                parts.append(
                    f'<line x1="{xx - 4:.2f}" y1="{yy + 4:.2f}" x2="{xx + 4:.2f}" y2="{yy - 4:.2f}" stroke="{status_color}" stroke-width="2"/>'
                )

    legend_x = left + plot_width + 32
    parts.append(
        f'<text x="{legend_x}" y="{top + 5}" font-size="16" font-weight="bold">Workloads</text>'
    )
    for index, spec in enumerate(workload_specs):
        yy = top + 32 + index * 30
        color = COLORS[index % len(COLORS)]
        parts.append(
            f'<line x1="{legend_x}" y1="{yy}" x2="{legend_x + 27}" y2="{yy}" stroke="{color}" stroke-width="2.5"/>'
        )
        parts.append(
            f'<circle cx="{legend_x + 13.5}" cy="{yy}" r="4" fill="white" stroke="{color}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{legend_x + 37}" y="{yy + 5}" font-size="13">{html.escape(spec["label"])}</text>'
        )
    status_y = top + 32 + len(workload_specs) * 30 + 25
    parts.append(
        f'<text x="{legend_x}" y="{status_y}" font-size="14" font-weight="bold">Invalid-point rail near y=0</text>'
    )
    parts.append(
        f'<text x="{legend_x}" y="{status_y + 23}" font-size="12" fill="#D62728">red × failed/skipped</text>'
    )
    parts.append(
        f'<text x="{legend_x}" y="{status_y + 42}" font-size="12" fill="#E69F00">orange × pending/running</text>'
    )
    parts.append(
        f'<text x="{legend_x}" y="{status_y + 61}" font-size="12" fill="#777">gray × unsupported</text>'
    )
    parts.append("</svg>\n")
    atomic_text(path, "\n".join(parts))


def markdown_report(path, workload_specs, rows, counts, complete, issues, provenance):
    row_map = {(item["workload_id"], item["tile"]): item for item in rows}
    lines = [
        "# DX100 physical tile-size sweep",
        "",
        f"Status: **{'complete and validated' if complete else 'in progress or validation-failing'}**.",
        "",
        "The plotted metric is `simTicks(16K) / simTicks(tile)`, so higher is better and every valid 16K point is 1.0.",
        "",
        "| Workload | 1K | 2K | 4K | 8K | 16K | 32K | 64K |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for spec in workload_specs:
        values = []
        for tile in TILES:
            row = row_map[(spec["id"], tile)]
            if row["status"] == "valid" and row["performance_16k"] is not None:
                values.append(f"{row['performance_16k']:.3f}")
            else:
                values.append(row["status"].upper())
        lines.append(f"| {spec['label']} | " + " | ".join(values) + " |")
    lines.extend(["", "## Validation state", ""])
    for key in sorted(counts):
        lines.append(f"- {key}: {counts[key]}")
    if issues:
        lines.extend(["", "## Outstanding issues", ""])
        lines.extend(f"- {item}" for item in issues)
    lines.extend(["", "## Provenance", ""])
    lines.extend(f"- `{item}`" for item in provenance)
    lines.extend(
        [
            "",
            "Every fresh valid point was rechecked for a completed workflow task, wrapper rc=0, matching first-ROI `simTicks`, a clean `m5_exit`, and its benchmark-specific correctness marker. Exact cross-tile fingerprints were compared where the benchmark exposes them.",
            "",
        ]
    )
    atomic_text(path, "\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--prior-gapbs-results",
        type=Path,
        default=Path(
            "/data1/nier/DX100/experiments/campaigns/2026-07-08_gapbs_tile_smoke/results.tsv"
        ),
    )
    parser.add_argument(
        "--prior-hashjoin-results",
        type=Path,
        action="append",
        default=None,
    )
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    state_root = args.state_root.resolve()
    output_dir = (args.output_dir or run_root / "final").resolve()
    prior_hashjoin = args.prior_hashjoin_results or [
        Path(
            "/data1/nier/DX100/experiments/campaigns/2026-07-10_hashjoin_tile_smoke/results.tsv"
        ),
        Path(
            "/data1/nier/DX100/experiments/campaigns/2026-07-11_hashjoin_tile_smoke/results.tsv"
        ),
    ]
    prior_hashjoin = [path.resolve() for path in prior_hashjoin]
    prior_gapbs = args.prior_gapbs_results.resolve()
    original_state_path = state_root / "workflows/dx100-full-tile-sweep-20260720.json"
    normal_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-normal-20260721.json"
    )
    is_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-is-20260721.json"
    )
    is_gate_state_path = state_root / (
        "workflows/dx100-full-tile-sweep-recovery2-is-gate-20260721.json"
    )
    states = {
        "original": read_json(original_state_path),
        "recovery_normal": read_json(normal_state_path),
        "recovery_is_gate": read_json(is_gate_state_path),
        "recovery_is": read_json(is_state_path),
    }
    workload_specs = specs(run_root, prior_gapbs, prior_hashjoin)
    rows, issues = build_rows(workload_specs, states)
    legal_rows = [row for row in rows if row["status"] != "unsupported"]
    counts = dict(Counter(row["status"] for row in rows))
    parent_tasks_complete = all(
        task_state(states["original"], task).get("state") == "completed"
        for task in ("ume-gradzatp-t65536", "ume-gradzatz-t65536")
    )
    terminal = (
        workflow_terminal(states["recovery_normal"])
        and workflow_terminal(states["recovery_is_gate"])
        and workflow_terminal(states["recovery_is"])
        and parent_tasks_complete
    )
    complete = terminal and all(row["status"] == "valid" for row in legal_rows)
    if not terminal:
        issues.append(
            "recovery workflows are not terminal or parent-owned UME 64K evidence is incomplete"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    source_tsv = output_dir / "tile_sweep_source.tsv"
    figure = output_dir / "tile_sweep_performance_16k.svg"
    report = output_dir / "README.md"
    validation = output_dir / "validation.json"
    status = output_dir / "status.json"
    write_source_tsv(source_tsv, rows)
    svg_plot(figure, workload_specs, rows)
    provenance = [
        run_root / "manifest.json",
        run_root / "recovery2-manifest.json",
        original_state_path,
        normal_state_path,
        is_gate_state_path,
        is_state_path,
        prior_gapbs,
        *prior_hashjoin,
    ]
    markdown_report(
        report,
        workload_specs,
        rows,
        counts,
        complete,
        issues,
        [str(item) for item in provenance],
    )
    validation_document = {
        "schema_version": 1,
        "terminal": terminal,
        "complete": complete,
        "normalization": "simTicks(16384) / simTicks(tile)",
        "workflow_counts": {
            name: workflow_counts(state) for name, state in states.items()
        },
        "point_counts": counts,
        "issues": issues,
        "rows": rows,
    }
    atomic_json(validation, validation_document)
    artifacts = [source_tsv, figure, report, validation]
    status_document = {
        "terminal": terminal,
        "complete": complete,
        "issues": issues,
        "artifacts": {
            item.name: {"path": str(item), "sha256": sha256(item)} for item in artifacts
        },
    }
    atomic_json(status, status_document)
    print(json.dumps({"terminal": terminal, "complete": complete, "counts": counts}))
    if complete or args.allow_incomplete:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
