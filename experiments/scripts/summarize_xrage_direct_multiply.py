#!/usr/bin/env python3
"""Validate and summarize the four-arm XRAGE direct-destination scale test."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

LABELS = ("native_scale1", "direct_scale1", "native_scale3", "direct_scale3")
EXPECTED = {
    "native_scale1": ("native", "native16", 1),
    "direct_scale1": ("compact", "compact16", 1),
    "native_scale3": ("native", "native16x3", 3),
    "direct_scale3": ("compact", "compact16x3", 3),
}
DRAM_RE = re.compile(
    r"^\s*CH(?P<channel>[0-9]+)_num_(?P<command>RD|WR|ACT|PRE)_commands_T:"
    r"\s+(?P<value>[0-9]+)(?:\s|$)"
)
IGNORED_MANIFEST_KEYS = {"arm", "guest_arm", "result_scale", "created_utc"}


def fail(message: str) -> None:
    raise SystemExit(f"XRAGE direct-multiply validation failed: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_kv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def read_result(path: Path) -> dict[str, int]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        fail(f"{path} must contain exactly one row")
    try:
        return {key: int(value) for key, value in rows[0].items()}
    except (TypeError, ValueError) as error:
        fail(f"{path} contains a non-integer result: {error}")


def read_artifact_hashes(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            fail(f"malformed artifact hash line in {path}: {line}")
        digest, artifact = fields
        values[Path(artifact).name] = digest
    return values


def dram_totals(path: Path) -> dict[str, int]:
    samples: dict[tuple[int, str], int] = {}
    for line in path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        match = DRAM_RE.match(line)
        if match:
            samples[
                (int(match.group("channel")), match.group("command"))
            ] = int(match.group("value"))
    channels = {channel for channel, _ in samples}
    if channels != {0, 1}:
        fail(
            f"{path} does not contain final counters for DRAM channels 0 and 1"
        )
    totals: dict[str, int] = {}
    for command in ("RD", "WR", "ACT", "PRE"):
        try:
            totals[command] = sum(
                samples[(channel, command)] for channel in channels
            )
        except KeyError as error:
            fail(f"{path} lacks final {error.args[0]} DRAM count")
    return totals


@dataclass(frozen=True)
class Run:
    label: str
    path: Path
    manifest: dict[str, str]
    result: dict[str, int]
    artifacts: dict[str, str]
    dram: dict[str, int]


def load_run(label: str, path: Path) -> Run:
    required = (
        "manifest.txt",
        "result.tsv",
        "artifact_sha256.txt",
        "checkpoint.exit",
        "restore.exit",
        "restore.log",
        "run/stats.txt",
        "run/config.ini",
        "xrage_attribution_smoke.pass",
    )
    for relative in required:
        if not (path / relative).is_file():
            fail(f"{label} is missing {relative}")
    if (path / "checkpoint.exit").read_text().strip() != "0":
        fail(f"{label} checkpoint did not exit zero")
    if (path / "restore.exit").read_text().strip() != "0":
        fail(f"{label} restore did not exit zero")
    log = (path / "restore.log").read_text(encoding="utf-8", errors="replace")
    if len(re.findall(r"^MAA_GATHER_VERIFY_PASS ", log, re.MULTILINE)) != 1:
        fail(f"{label} does not have exactly one exact-output pass")
    if "m5_exit instruction encountered" not in log:
        fail(f"{label} lacks a terminal m5_exit")
    if re.search(
        r"panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL", log, re.I
    ):
        fail(f"{label} contains a fatal or verifier marker")
    return Run(
        label,
        path.resolve(),
        read_kv(path / "manifest.txt"),
        read_result(path / "result.tsv"),
        read_artifact_hashes(path / "artifact_sha256.txt"),
        dram_totals(path / "restore.log"),
    )


def validate(runs: dict[str, Run]) -> None:
    for label, run in runs.items():
        arm, guest_arm, scale = EXPECTED[label]
        if (run.manifest.get("arm"), run.manifest.get("guest_arm")) != (
            arm,
            guest_arm,
        ):
            fail(f"{label} has the wrong simulator/guest arm")
        if run.manifest.get("result_scale") != str(scale):
            fail(f"{label} has the wrong result scale")
        if run.result["stats_blocks"] != 2:
            fail(f"{label} must contain ROI and post-verifier stats blocks")
        if run.result["final_simTicks"] < run.result["roi_simTicks"]:
            fail(f"{label} final ticks precede ROI ticks")
        if (
            run.result["virtual_write_issues"]
            != run.result["virtual_write_completions"]
        ):
            fail(f"{label} has unbalanced virtual writes")
        if run.result["maa_scalar_alu_instructions"] != (
            2 if label == "native_scale3" else 0
        ):
            fail(f"{label} has an unexpected MAA scalar-ALU count")
        if label.startswith("direct_"):
            if run.result["maa_stream_write_instructions"] != 0:
                fail(f"{label} unexpectedly used an MAA stream store")
            if run.result["virtual_write_issues"] == 0:
                fail(f"{label} did not activate direct destination retirement")
        else:
            if run.result["virtual_write_issues"] != 0:
                fail(f"{label} unexpectedly activated virtual retirement")
            if run.result["maa_stream_write_instructions"] != 2:
                fail(f"{label} did not execute two MAA stream stores")

    reference = runs[LABELS[0]]
    reference_manifest = {
        key: value
        for key, value in reference.manifest.items()
        if key not in IGNORED_MANIFEST_KEYS
    }
    for label in LABELS[1:]:
        candidate = {
            key: value
            for key, value in runs[label].manifest.items()
            if key not in IGNORED_MANIFEST_KEYS
        }
        if candidate != reference_manifest:
            fail(f"{label} differs in a non-treatment manifest field")
        if runs[label].artifacts != reference.artifacts:
            fail(f"{label} does not use the same hashed artifacts")

    for scale in (1, 3):
        native = runs[f"native_scale{scale}"].result["output_hash"]
        direct = runs[f"direct_scale{scale}"].result["output_hash"]
        if native != direct:
            fail(f"scale {scale} native/direct exact hashes differ")
    if (
        runs["native_scale1"].result["output_hash"]
        == runs["native_scale3"].result["output_hash"]
    ):
        fail("scale 1 and scale 3 hashes unexpectedly match")

    for run in runs.values():
        for artifact_name, recorded in run.artifacts.items():
            matches = list(run.path.glob(f"**/{artifact_name}"))
            if matches and all(sha256(match) != recorded for match in matches):
                fail(
                    f"{run.label} local artifact hash mismatch for {artifact_name}"
                )


def write_outputs(runs: dict[str, Run], tsv: Path, markdown: Path) -> None:
    columns = (
        "label",
        "scale",
        "output_hash",
        "roi_simTicks",
        "maa_instructions",
        "maa_indirect_instructions",
        "maa_stream_read_instructions",
        "maa_stream_write_instructions",
        "maa_scalar_alu_instructions",
        "maa_scalar_alu_cycles",
        "cpu_committed_instructions",
        "cpu_data_reads",
        "cpu_data_writes",
        "virtual_write_issues",
        "dram_reads",
        "dram_writes",
        "dram_activates",
        "dram_precharges",
        "run",
    )
    rows: list[dict[str, str | int]] = []
    for label in LABELS:
        run = runs[label]
        row: dict[str, str | int] = {
            "label": label,
            "scale": EXPECTED[label][2],
            "dram_reads": run.dram["RD"],
            "dram_writes": run.dram["WR"],
            "dram_activates": run.dram["ACT"],
            "dram_precharges": run.dram["PRE"],
            "run": str(run.path),
        }
        for column in columns:
            if column in run.result:
                row[column] = run.result[column]
        rows.append(row)

    tsv.parent.mkdir(parents=True, exist_ok=True)
    with tsv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    def comparison(scale: int) -> tuple[float, float]:
        native = runs[f"native_scale{scale}"].result["roi_simTicks"]
        direct = runs[f"direct_scale{scale}"].result["roi_simTicks"]
        return native / direct, (native - direct) * 100.0 / native

    ratio1, reduction1 = comparison(1)
    ratio3, reduction3 = comparison(3)
    common = runs[LABELS[0]]
    artifact_values = list(common.artifacts.values())
    binary_hash = artifact_values[1] if len(artifact_values) > 1 else "unknown"
    input_hash = artifact_values[2] if len(artifact_values) > 2 else "unknown"
    markdown.write_text(
        "\n".join(
            [
                "# XRAGE Direct Destination With Post-Gather Multiply",
                "",
                "All four 20K runs used one binary and input with matched gem5/cache/DRAM/MAA configuration. Each checkpoint and restore exited zero, reached terminal `m5_exit`, produced two stats blocks, and passed exact output verification.",
                "",
                f"- source commit: `{common.manifest['source_commit']}`",
                f"- gem5 SHA-256: `{artifact_values[0] if artifact_values else 'unknown'}`",
                f"- Spatter SHA-256: `{binary_hash}`",
                f"- input SHA-256: `{input_hash}`",
                "",
                "| Path | Scale | ROI simTicks | MAA ALU | MAA stream store | CPU instructions | CPU reads/writes | Virtual writes | DRAM reads/writes | Hash |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
                *[
                    f"| {row['label']} | {row['scale']} | {row['roi_simTicks']} | {row['maa_scalar_alu_instructions']} | {row['maa_stream_write_instructions']} | {row['cpu_committed_instructions']} | {row['cpu_data_reads']}/{row['cpu_data_writes']} | {row['virtual_write_issues']} | {row['dram_reads']}/{row['dram_writes']} | `{row['output_hash']}` |"
                    for row in rows
                ],
                "",
                f"Gather-only direct versus native: `{ratio1:.6f}x`, `{reduction1:+.6f}%` native-tick reduction.",
                f"Multiply-by-three direct+CPU versus native+MAA-ALU: `{ratio3:.6f}x`, `{reduction3:+.6f}%` native-tick reduction.",
                "",
                "The direct opcode remains semantically final only for scale 1. Its destination tile is completion-only, so scale 3 cannot feed ordinary MAA ALU. The tested direct equivalent waits for acknowledged direct writes and then performs the multiply on the CPUs in place. Native scale 3 instead executes `INDIR_LD -> ALU_SCALAR(FP64 MUL) -> STREAM_ST` in MAA.",
                "",
                "These deterministic small runs answer this semantic/mechanism question only; they do not replace or generalize the historical full-XRAGE result.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    for label in LABELS:
        parser.add_argument(
            f"--{label.replace('_', '-')}", type=Path, required=True
        )
    parser.add_argument("--tsv", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    runs = {label: load_run(label, getattr(args, label)) for label in LABELS}
    validate(runs)
    write_outputs(runs, args.tsv, args.markdown)
    print(f"PASS XRAGE direct-multiply comparison: {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
