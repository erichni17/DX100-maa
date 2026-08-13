#!/usr/bin/env python3

import argparse
import csv
import hashlib
import json
import re
import shlex
from pathlib import Path

TREATMENT_KEYS = {"created_utc", "direct_retirement_line_handoff"}
TERMINAL_MARKER = re.compile(
    r"Exiting @ tick [0-9]+ because m5_exit instruction encountered"
)
VERIFIER_MARKER = re.compile(
    r"^MAA_GATHER_VERIFY_PASS length=([0-9]+) hash=([0-9]+)$", re.M
)
RAW_RESULT_STATS = {
    "direct_descriptors": "system.maa.direct_retirement_descriptors",
    "direct_page_acks": "system.maa.direct_retirement_producer_acks",
    "direct_line_acks": "system.maa.direct_retirement_producer_line_acks",
    "direct_page_fallback_lines": ("system.maa.direct_retirement_page_fallback_lines"),
    "direct_read_responses": "system.maa.direct_retirement_read_responses",
    "direct_alu_completions": "system.maa.direct_retirement_alu_completions",
    "direct_write_responses": "system.maa.direct_retirement_write_responses",
    "direct_fallbacks": "system.maa.direct_retirement_fallbacks",
}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def key_values(path):
    values = {}
    for line in path.read_text(encoding="ascii").splitlines():
        key, value = line.split("=", 1)
        values[key] = value
    return values


def artifact_records(path):
    records = []
    for line in path.read_text(encoding="ascii").splitlines():
        expected, raw_path = line.split(maxsplit=1)
        artifact = Path(raw_path)
        if not artifact.is_file() or digest(artifact) != expected:
            raise ValueError(f"artifact changed or is missing: {artifact}")
        records.append((expected, artifact))
    return records


def result_row(path):
    with path.open(newline="", encoding="ascii") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        raise ValueError(f"expected one result row in {path}")
    return rows[0]


def stats_blocks(path):
    blocks = []
    current = None
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if current is not None:
                raise ValueError(f"nested statistics blocks in {path}")
            current = {}
            continue
        if line.startswith("---------- End Simulation Statistics"):
            if current is None:
                raise ValueError(f"orphan statistics terminator in {path}")
            blocks.append(current)
            current = None
            continue
        if current is None:
            continue
        fields = line.split()
        if len(fields) >= 2:
            current[fields[0]] = fields[1]
    if current is not None or not blocks:
        raise ValueError(f"incomplete or missing statistics blocks in {path}")
    return blocks


def unique_config_value(path, key):
    prefix = f"{key}="
    values = [
        line[len(prefix) :]
        for line in path.read_text(encoding="ascii").splitlines()
        if line.startswith(prefix)
    ]
    if len(values) != 1:
        raise ValueError(f"expected one {key} in {path}")
    return values[0]


def verifier_record(log, path):
    matches = VERIFIER_MARKER.findall(log)
    if len(matches) != 1:
        raise ValueError(f"expected one exact verifier record in {path}")
    return tuple(int(value) for value in matches[0])


def command_has_line_handoff(path):
    arguments = shlex.split(path.read_text(encoding="ascii"))
    return arguments.count("--maa_direct_retirement_line_handoff") == 1


def exact_checkpoint(arm):
    checkpoints = [
        path
        for path in (arm / "checkpoint").glob("cpt.*")
        if path.is_dir() and (path / "m5.cpt").is_file()
    ]
    if len(checkpoints) != 1:
        raise ValueError(f"expected one checkpoint in {arm}")
    files = sorted(path for path in checkpoints[0].iterdir() if path.is_file())
    records = {}
    for path in files:
        if path.name == "m5.cpt":
            lines = path.read_bytes().splitlines(keepends=True)
            if not lines or not lines[0].startswith(b"## checkpoint generated: "):
                raise ValueError(f"{path} lacks the expected timestamp header")
            value = hashlib.sha256(b"".join(lines[1:])).hexdigest()
            records[path.name] = ("normalized_timestamp_header", value)
        else:
            records[path.name] = (path.stat().st_size, digest(path))
    return records


def validate_arm(arm, expected_line_handoff):
    for name in ("checkpoint.exit", "restore.exit"):
        if (arm / name).read_text(encoding="ascii").strip() != "0":
            raise ValueError(f"{arm} has a nonzero {name}")
    driver_exit = arm.parent / f"{arm.name}.driver.exit"
    if driver_exit.exists() and driver_exit.read_text(encoding="ascii").strip() != "0":
        raise ValueError(f"{arm} has a nonzero driver exit")
    if (arm / "source_status.txt").stat().st_size != 0:
        raise ValueError(f"{arm} used a dirty source tree")
    if (arm / "source.diff").stat().st_size != 0:
        raise ValueError(f"{arm} recorded an uncommitted source diff")

    log = (arm / "restore.log").read_text(encoding="ascii", errors="replace")
    if not TERMINAL_MARKER.search(log):
        raise ValueError(f"{arm} lacks terminal m5_exit")
    if re.search(r"panic|fatal|segmentation fault|MAA_GATHER_VERIFY_FAIL", log, re.I):
        raise ValueError(f"{arm} failed exact correctness or fatal-marker checks")
    raw_blocks = stats_blocks(arm / "run/stats.txt")

    manifest = key_values(arm / "manifest.txt")
    if manifest.get("guest_arm") != "direct4x3":
        raise ValueError(f"{arm} is not the direct4x3 arm")
    if int(manifest["direct_retirement_line_handoff"]) != expected_line_handoff:
        raise ValueError(f"{arm} has the wrong treatment bit")

    result = result_row(arm / "result.tsv")
    expected_bool = "true" if expected_line_handoff else "false"
    config = arm / "run/config.ini"
    resolved = {
        "direct_retirement_line_handoff": expected_bool,
        "transparent_spd_mode": "3",
        "num_tile_elements": "16384",
        "physical_tile_elements": "4096",
    }
    for key, expected in resolved.items():
        if unique_config_value(config, key) != expected:
            raise ValueError(f"{arm} has the wrong resolved {key}")
    if command_has_line_handoff(arm / "restore.command") != bool(expected_line_handoff):
        raise ValueError(f"{arm} restore command has the wrong treatment bit")
    if int(result["stats_blocks"]) != len(raw_blocks) or len(raw_blocks) != 2:
        raise ValueError(f"{arm} result has the wrong stats-block count")
    if int(result["roi_simTicks"]) != int(raw_blocks[0]["simTicks"]):
        raise ValueError(f"{arm} result does not match raw ROI simTicks")
    if int(result["final_simTicks"]) != int(raw_blocks[-1]["simTicks"]):
        raise ValueError(f"{arm} result does not match raw final simTicks")
    for field, stat in RAW_RESULT_STATS.items():
        if int(result[field]) != int(raw_blocks[0][stat]):
            raise ValueError(f"{arm} result does not match raw {stat}")

    descriptors = int(result["direct_descriptors"])
    lines = descriptors * 2048
    page_acks = descriptors * 4
    verified_length, verified_hash = verifier_record(log, arm / "restore.log")
    if verified_length != descriptors * 16384:
        raise ValueError(f"{arm} verifier length does not match descriptors")
    if str(verified_hash) != result["output_hash"]:
        raise ValueError(f"{arm} result does not match the exact verifier hash")
    common = {
        "direct_page_acks": page_acks,
        "direct_read_responses": lines,
        "direct_alu_completions": lines,
        "direct_write_responses": lines,
        "direct_fallbacks": 0,
    }
    for field, expected in common.items():
        if int(result[field]) != expected:
            raise ValueError(f"{arm} violates {field}={expected}")
    if expected_line_handoff:
        expected = (lines, 0)
    else:
        expected = (0, lines)
    observed = (
        int(result["direct_line_acks"]),
        int(result["direct_page_fallback_lines"]),
    )
    if observed != expected:
        raise ValueError(
            f"{arm} has line/fallback closure {observed}, expected {expected}"
        )
    return manifest, result, artifact_records(arm / "artifact_sha256.txt")


def find_artifact_hash(records, name):
    matches = [value for value, path in records if path.name == name]
    if len(matches) != 1:
        raise ValueError(f"expected one artifact named {name}")
    return matches[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("page_arm", type=Path)
    parser.add_argument("line_arm", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--simulator-provenance", required=True, type=Path)
    parser.add_argument("--guest-build-manifest", required=True, type=Path)
    parser.add_argument("--guest-build-artifacts", required=True, type=Path)
    args = parser.parse_args()

    page_manifest, page, page_artifacts = validate_arm(args.page_arm, 0)
    line_manifest, line, line_artifacts = validate_arm(args.line_arm, 1)
    page_comparable = {
        key: value for key, value in page_manifest.items() if key not in TREATMENT_KEYS
    }
    line_comparable = {
        key: value for key, value in line_manifest.items() if key not in TREATMENT_KEYS
    }
    if page_comparable != line_comparable:
        raise ValueError("non-treatment manifest fields differ")
    if [value for value, _ in page_artifacts] != [value for value, _ in line_artifacts]:
        raise ValueError("page and line artifacts differ")
    if exact_checkpoint(args.page_arm) != exact_checkpoint(args.line_arm):
        raise ValueError("page and line checkpoints are not byte-identical")
    if page["output_hash"] != line["output_hash"]:
        raise ValueError("page and line output hashes differ")

    simulator = key_values(args.simulator_provenance)
    if page_manifest["source_commit"] != simulator["source_commit"]:
        raise ValueError("declared simulator commit does not match provenance")
    gem5_hash = find_artifact_hash(page_artifacts, "gem5.opt")
    if gem5_hash != simulator["gem5_sha256"]:
        raise ValueError("gem5 hash does not match simulator provenance")

    guest_manifest = key_values(args.guest_build_manifest)
    guest_artifacts = artifact_records(args.guest_build_artifacts)
    guest_name = "spatter_maa_xrage_runtime_verify_16K"
    guest_hash = find_artifact_hash(page_artifacts, guest_name)
    if guest_hash != find_artifact_hash(guest_artifacts, guest_name):
        raise ValueError("guest hash does not match its build record")

    page_ticks = int(page["roi_simTicks"])
    line_ticks = int(line["roi_simTicks"])
    if page_ticks <= 0 or line_ticks <= 0:
        raise ValueError("ROI simTicks must be positive")
    summary = {
        "status": "pass",
        "page_arm": str(args.page_arm),
        "line_arm": str(args.line_arm),
        "simulator_source_commit": simulator["source_commit"],
        "simulator_sha256": gem5_hash,
        "guest_source_commit": guest_manifest["source_commit"],
        "guest_sha256": guest_hash,
        "checkpoint_sha256": exact_checkpoint(args.page_arm),
        "output_hash": page["output_hash"],
        "page_simTicks": page_ticks,
        "line_simTicks": line_ticks,
        "latency_reduction": (page_ticks - line_ticks) / page_ticks,
        "speedup": page_ticks / line_ticks,
        "direct_descriptors": int(page["direct_descriptors"]),
        "expected_result_lines": int(page["direct_descriptors"]) * 2048,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(
        "PASS XRAGE line handoff pair: "
        f"{page_ticks} -> {line_ticks} simTicks, "
        f"{summary['latency_reduction'] * 100:.6f}% lower latency, "
        f"{summary['speedup']:.6f}x speedup"
    )


if __name__ == "__main__":
    main()
