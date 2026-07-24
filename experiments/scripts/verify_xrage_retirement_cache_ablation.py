#!/usr/bin/env python3
"""Independently verify an XRAGE retirement-cache ablation campaign."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

FATAL_RE = re.compile(
    r"panic|fatal|assert|abort|segmentation fault|error:|"
    r"MAA_GATHER_VERIFY_FAIL",
    re.IGNORECASE,
)
MARKER_RE = re.compile(
    r"^MAA_GATHER_VERIFY_PASS length=[1-9][0-9]* hash=[0-9]+$"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RESULT_FIELDS = (
    "phase",
    "candidate",
    "replica",
    "return_code",
    "sim_ticks",
    "maa_cycles",
    "write_issues",
    "write_completions",
    "cache_hits",
    "cache_misses",
    "cache_writebacks",
    "mshr_misses",
    "blocked_no_mshrs_cycles",
    "blocked_no_mshrs_events",
    "blocked_no_wb_cycles",
    "blocked_no_wb_events",
    "roi_count",
    "exit_count",
    "fatal_count",
    "marker",
    "parse_error",
    "wall_seconds",
    "valid",
)


def fail(message: str) -> None:
    raise SystemExit(f"ablation verification failed: {message}")


def regular_file(path: Path, *, empty: bool | None = None) -> Path:
    if path.is_symlink() or not path.is_file():
        fail(f"required regular file is missing or symlinked: {path}")
    if empty is not None and (path.stat().st_size == 0) != empty:
        fail(f"unexpected empty state: {path}")
    return path


def reject_symlink_components(path: Path) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            fail(f"path contains a symlink component: {path}")


def contained_regular_file(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        fail(f"unsafe relative path under {root}: {relative}")
    reject_symlink_components(root)
    target = root / relative
    reject_symlink_components(target)
    resolved_root = root.resolve(strict=True)
    resolved = target.resolve(strict=True)
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        fail(f"path escapes its root: root={root} path={relative}")
    return regular_file(resolved)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def correctness_campaign_fingerprint(campaign: Path) -> str:
    reject_symlink_components(campaign)
    campaign = campaign.resolve(strict=True)
    required = [
        campaign / "campaign.pass",
        campaign / "artifact_sha256.txt",
        campaign / "results.tsv",
        campaign / "source.txt",
    ]
    checkpoint_manifests = sorted(
        campaign.glob("checkpoints/*/checkpoint_sha256.txt")
    )
    if {path.parent.name for path in checkpoint_manifests} != {
        "native",
        "fused",
        "virtual",
    }:
        fail(f"correctness checkpoint-manifest closure differs: {campaign}")
    digest = hashlib.sha256()
    for path in sorted((*required, *checkpoint_manifests)):
        reject_symlink_components(path)
        regular_file(path)
        relative = str(path.relative_to(campaign)).encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(file_sha256(path)))
    return digest.hexdigest()


def expect_hash(path: Path, expected: str) -> None:
    regular_file(path)
    if not SHA256_RE.fullmatch(expected) or file_sha256(path) != expected:
        fail(f"SHA-256 mismatch: {path}")


def load_json(path: Path) -> dict[str, Any]:
    regular_file(path, empty=False)
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        fail(f"invalid JSON in {path}: {error}")
    if not isinstance(value, dict):
        fail(f"expected JSON object: {path}")
    return value


def read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    regular_file(path, empty=False)
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
    if reader.fieldnames is None or not rows:
        fail(f"empty TSV: {path}")
    if any(
        None in row or any(value is None for value in row.values())
        for row in rows
    ):
        fail(f"malformed TSV: {path}")
    return reader.fieldnames, rows


def verify_evidence(campaign: Path) -> None:
    manifest = regular_file(campaign / "evidence_sha256.txt", empty=False)
    covered: set[Path] = set()
    for line_number, line in enumerate(manifest.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            fail(f"malformed evidence line {line_number}")
        digest, raw = match.groups()
        relative = Path(raw)
        if relative.is_absolute() or ".." in relative.parts:
            fail(f"unsafe evidence path: {relative}")
        path = (campaign / relative).resolve(strict=True)
        try:
            path.relative_to(campaign)
        except ValueError:
            fail(f"evidence path escapes campaign: {path}")
        if path in covered:
            fail(f"duplicate evidence path: {path}")
        expect_hash(path, digest)
        covered.add(path)
    expected = {
        path.resolve()
        for path in campaign.rglob("*")
        if path.is_file()
        and not path.is_symlink()
        and "inputs" not in path.relative_to(campaign).parts
        and path.name
        not in {
            "campaign.pass",
            "campaign.fail",
            "execution.complete",
            "evidence_sha256.txt",
        }
    }
    if covered != expected:
        fail("evidence manifest does not exactly cover non-input evidence")


def verify_staged_inputs(campaign: Path) -> None:
    root = campaign / "inputs"
    manifest = load_json(campaign / "staged_input_sha256.json")
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual != set(manifest):
        fail("staged input closure differs")
    for relative, expected in manifest.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            fail("staged input manifest has invalid types")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            fail(f"unsafe staged path: {relative}")
        expect_hash(root / path, expected)


def verify_checkpoint(root: Path, manifest_name: str) -> None:
    reject_symlink_components(root)
    manifest = contained_regular_file(root, Path(manifest_name))
    if manifest.stat().st_size == 0:
        fail(f"empty checkpoint manifest: {manifest}")
    entries: dict[Path, str] = {}
    for line_number, line in enumerate(manifest.read_text().splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
        if match is None:
            fail(f"malformed checkpoint line {line_number}: {manifest}")
        digest, raw = match.groups()
        relative = Path(raw)
        path = contained_regular_file(root, relative)
        if path in entries:
            fail(f"duplicate checkpoint path: {relative}")
        entries[path] = digest
    checkpoint_dirs = {
        path.parent
        for path in root.glob("*/m5.cpt")
        if path.is_file() and not path.is_symlink()
    }
    if len(checkpoint_dirs) != 1:
        fail(f"expected one checkpoint directory: {root}")
    checkpoint_dir = checkpoint_dirs.pop()
    actual = {
        path
        for path in checkpoint_dir.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual != set(entries):
        fail(f"checkpoint closure differs: {root}")
    for path, digest in entries.items():
        expect_hash(path, digest)


def verify_source_correctness(
    campaign: Path, expected_fingerprint: str
) -> str:
    if correctness_campaign_fingerprint(campaign) != expected_fingerprint:
        fail(f"source correctness fingerprint differs: {campaign}")
    regular_file(campaign / "campaign.pass", empty=True)
    if (campaign / "campaign.fail").exists():
        fail(f"source correctness campaign has fail state: {campaign}")
    fields, rows = read_tsv(campaign / "results.tsv")
    if tuple(fields) != (
        "arm",
        "rc",
        "marker_count",
        "roi_count",
        "write_issues",
        "write_completions",
        "valid",
        "marker",
    ):
        fail(f"source correctness schema differs: {campaign}")
    virtual = [row for row in rows if row["arm"] == "virtual"]
    if len(virtual) != 1:
        fail(f"source correctness lacks one virtual row: {campaign}")
    row = virtual[0]
    if (
        row["rc"] != "0"
        or row["marker_count"] != "1"
        or row["roi_count"] != "1"
        or row["valid"] != "1"
        or not MARKER_RE.fullmatch(row["marker"])
        or not row["write_issues"].isdigit()
        or row["write_issues"] == "0"
        or row["write_issues"] != row["write_completions"]
    ):
        fail(f"source correctness virtual row is invalid: {campaign}")
    verify_checkpoint(
        campaign / "checkpoints/virtual", "checkpoint_sha256.txt"
    )
    return row["marker"]


def verified_reference_ticks(campaign: Path) -> int:
    fields, rows = read_tsv(campaign / "results.tsv")
    if not {"arm", "sim_ticks", "valid"}.issubset(fields):
        fail("reference campaign results lack required fields")
    virtual = [row for row in rows if row["arm"] == "virtual"]
    if len(virtual) != 3 or any(row["valid"] != "1" for row in virtual):
        fail("reference campaign lacks three valid virtual replicas")
    ticks = {row["sim_ticks"] for row in virtual}
    if len(ticks) != 1 or not next(iter(ticks)).isdigit():
        fail("reference virtual replicas are not deterministic")
    value = int(next(iter(ticks)))
    if value <= 0:
        fail("reference simTicks is not positive")
    return value


def first_stats(path: Path) -> dict[str, int]:
    sections: list[list[str]] = []
    current: list[str] | None = None
    for line in regular_file(path).read_text().splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if current is not None:
                fail(f"nested stats section: {path}")
            current = []
        elif line.startswith("---------- End Simulation Statistics"):
            if current is None:
                fail(f"unmatched stats terminator: {path}")
            sections.append(current)
            current = None
        elif current is not None:
            current.append(line)
    if current is not None or len(sections) != 2:
        fail(f"expected exactly two stats sections: {path}")

    values = {
        "sim_ticks": -1,
        "maa_cycles": -1,
        "write_issues": 0,
        "write_completions": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "cache_writebacks": 0,
        "mshr_misses": 0,
        "blocked_no_mshrs_cycles": 0,
        "blocked_no_mshrs_events": 0,
        "blocked_no_wb_cycles": 0,
        "blocked_no_wb_events": 0,
    }
    seen: dict[str, int] = {}
    patterns = (
        ("write_issues", r"system\.maa\.I[0-9]+_IND_VirtWriteIssues"),
        (
            "write_completions",
            r"system\.maa\.I[0-9]+_IND_VirtWriteCompletions",
        ),
        (
            "cache_hits",
            r"system\.maa_retirement_caches[0-9]+\.demandHits_8::maa",
        ),
        (
            "cache_misses",
            r"system\.maa_retirement_caches[0-9]+\.demandMisses_8::maa",
        ),
        (
            "cache_writebacks",
            r"system\.maa_retirement_caches[0-9]+"
            r"\.writebacks_8::writebacks",
        ),
        (
            "mshr_misses",
            r"system\.maa_retirement_caches[0-9]+"
            r"\.demandMshrMisses_8::maa",
        ),
        (
            "blocked_no_mshrs_cycles",
            r"system\.maa_retirement_caches[0-9]+"
            r"\.blockedCycles_T::no_mshrs",
        ),
        (
            "blocked_no_mshrs_events",
            r"system\.maa_retirement_caches[0-9]+"
            r"\.blockedCauses_T::no_mshrs",
        ),
        (
            "blocked_no_wb_cycles",
            r"system\.maa_retirement_caches[0-9]+" r"\.blockedCycles_T::no_wb",
        ),
        (
            "blocked_no_wb_events",
            r"system\.maa_retirement_caches[0-9]+" r"\.blockedCauses_T::no_wb",
        ),
    )
    for line in sections[0]:
        fields = line.split()
        if len(fields) < 2 or not re.fullmatch(r"-?[0-9]+", fields[1]):
            continue
        name, raw = fields[:2]
        value = int(raw)
        if name == "simTicks":
            key = "sim_ticks"
        elif name == "system.maa.cycles_TOTAL":
            key = "maa_cycles"
        else:
            key = next(
                (
                    candidate
                    for candidate, pattern in patterns
                    if re.fullmatch(pattern, name)
                ),
                "",
            )
        if not key:
            continue
        seen[key] = seen.get(key, 0) + 1
        if key in ("sim_ticks", "maa_cycles"):
            values[key] = value
        else:
            values[key] += value
    if seen.get("sim_ticks") != 1 or seen.get("maa_cycles") != 1:
        fail(f"missing unique timing metrics: {path}")
    if seen.get("write_issues", 0) <= 0 or seen.get(
        "write_issues"
    ) != seen.get("write_completions"):
        fail(f"unbalanced virtual-write stat keys: {path}")
    for key in ("cache_misses", "cache_writebacks", "mshr_misses"):
        if seen.get(key) != 4:
            fail(f"expected four values for {key}: {path}")
    if seen.get("cache_hits", 0) not in (0, 4):
        fail(f"expected zero or four cache-hit values: {path}")
    if (
        values["sim_ticks"] <= 0
        or values["maa_cycles"] <= 0
        or values["write_issues"] <= 0
        or values["write_issues"] != values["write_completions"]
        or values["cache_hits"] + values["cache_misses"]
        != values["write_issues"]
        or values["cache_writebacks"] > values["cache_misses"]
        or values["mshr_misses"] > values["cache_misses"]
    ):
        fail(f"inconsistent virtual-retirement accounting: {path}")
    return values


def candidate_map(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = source.get("candidates")
    if not isinstance(raw, list):
        fail("source candidate list is malformed")
    candidates: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            fail("source candidate is malformed")
        candidates[item["name"]] = item
    if tuple(candidates) != ("reference", "targets1", "compact"):
        fail("source candidate matrix or ordering changed")
    expected = {
        "reference": ("1kB", 4, 1, 16, 16, 16),
        "targets1": ("1kB", 4, 1, 16, 1, 16),
        "compact": ("256B", 4, 1, 16, 1, 16),
    }
    for name, values in expected.items():
        item = candidates[name]
        actual = (
            item.get("size"),
            item.get("assoc"),
            item.get("response_latency"),
            item.get("mshrs"),
            item.get("targets_per_mshr"),
            item.get("write_buffers"),
        )
        if actual != values:
            fail(f"candidate definition changed: {name}")
    return candidates


def command_difference(actual: list[str], expected: list[str]) -> str:
    for index in range(max(len(actual), len(expected))):
        actual_token = actual[index] if index < len(actual) else "<missing>"
        expected_token = (
            expected[index] if index < len(expected) else "<missing>"
        )
        if actual_token != expected_token:
            return (
                f"token={index} actual={actual_token!r} "
                f"expected={expected_token!r} "
                f"actual_length={len(actual)} expected_length={len(expected)}"
            )
    return "unknown difference"


def expected_command(
    campaign: Path,
    root: Path,
    phase: str,
    candidate: dict[str, Any],
) -> list[str]:
    inputs = campaign / "inputs"
    binary = (
        inputs / "benchmark/xrage_virtual"
        if phase == "full_performance"
        else inputs / "benchmark/xrage_virtual_verify"
    )
    data = (
        inputs / "benchmark/xrage_20k.json"
        if phase == "screen_correctness"
        else inputs / "benchmark/xrage_full.json"
    )
    return [
        str(inputs / "bin/gem5.opt"),
        "--listener-mode=off",
        f"--outdir={root}",
        str(inputs / "simulator/configs/deprecated/example/se.py"),
        "--cpu-type",
        "X86O3CPU",
        "-r",
        "1",
        "-n",
        "4",
        "--mem-size",
        "2GB",
        "--sys-clock",
        "3.2GHz",
        "--cpu-clock",
        "3.2GHz",
        "--caches",
        "--l1d_size=32kB",
        "--l1d_assoc=8",
        "--l1d-hwp-type=StridePrefetcher",
        "--l1d_mshrs=16",
        "--l1d_write_buffers=8",
        "--l1i_size=32kB",
        "--l1i_assoc=8",
        "--l1i-hwp-type=StridePrefetcher",
        "--l1i_mshrs=16",
        "--l1i_write_buffers=8",
        "--l2cache",
        "--l2_size=256kB",
        "--l2_assoc=4",
        "--l2-hwp-type=StridePrefetcher",
        "--l2_mshrs=32",
        "--l2_write_buffers=16",
        "--l3cache",
        "--l3_size=8MB",
        "--l3_assoc=16",
        "--l3_mshrs=256",
        "--l3_write_buffers=128",
        "--l3_ports=4",
        "--cacheline_size=64",
        "--mem-type",
        "Ramulator2",
        "--ramulator-config",
        str(inputs / "ramulator.yaml"),
        "--mem-channels=2",
        "--maa_ncbus_width=32",
        "--maa",
        "--maa_num_maas=1",
        "--maa_num_tile_elements=16384",
        "--maa_l2_uncacheable",
        "--maa_l3_uncacheable",
        "--maa_num_initial_row_table_slices=32",
        "--maa_virtual_combine_slots=384",
        "--maa_virtual_combine_words=4096",
        "--maa_virtual_combine_ways=4",
        "--maa_virtual_combine_banks=4",
        "--maa_virtual_response_slots=96",
        "--maa_virtual_response_word_pool=480",
        "--maa_virtual_words_per_cycle=4",
        "--maa_virtual_max_outstanding_writes=64",
        "--maa_virtual_masked_writes",
        f"--maa_retirement_cache_size={candidate['size']}",
        f"--maa_retirement_cache_assoc={candidate['assoc']}",
        (
            "--maa_retirement_cache_response_latency="
            f"{candidate['response_latency']}"
        ),
        f"--maa_retirement_cache_mshrs={candidate['mshrs']}",
        (
            "--maa_retirement_cache_targets_per_mshr="
            f"{candidate['targets_per_mshr']}"
        ),
        (
            "--maa_retirement_cache_write_buffers="
            f"{candidate['write_buffers']}"
        ),
        "--cmd",
        str(binary),
        "--options",
        f"-f {data}",
    ]


def verify_cache_config(path: Path, candidate: dict[str, Any]) -> None:
    sections: dict[str, dict[str, str]] = {}
    section: str | None = None
    for line in regular_file(path).read_text().splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            sections[section] = {}
        elif section is not None and "=" in line:
            key, value = line.split("=", 1)
            sections[section][key] = value
    banks = [
        values
        for name, values in sections.items()
        if re.fullmatch(r"system\.maa_retirement_caches[0-9]+", name)
    ]
    if len(banks) != 4:
        fail(f"expected four effective retirement caches: {path}")
    expected = {
        "size": str({"1kB": 1024, "256B": 256}[str(candidate["size"])]),
        "assoc": str(candidate["assoc"]),
        "response_latency": str(candidate["response_latency"]),
        "mshrs": str(candidate["mshrs"]),
        "tgts_per_mshr": str(candidate["targets_per_mshr"]),
        "write_buffers": str(candidate["write_buffers"]),
    }
    for bank in banks:
        for key, value in expected.items():
            if bank.get(key) != value:
                fail(
                    f"effective cache config differs for {key}: "
                    f"expected={value} actual={bank.get(key)!r} path={path}"
                )


def normalized_treatment_config(path: Path) -> str:
    normalized: list[str] = []
    section = ""
    for line in regular_file(path).read_text().splitlines():
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        if "=" in line:
            key, _ = line.split("=", 1)
            if (
                re.fullmatch(r"system\.maa_retirement_caches[0-9]+", section)
                and key in {"size", "tgts_per_mshr"}
            ) or (
                re.fullmatch(
                    r"system\.maa_retirement_caches[0-9]+"
                    r"\.tags(?:\.indexing_policy)?",
                    section,
                )
                and key == "size"
            ):
                line = f"{key}=<RETIREMENT_CACHE_TREATMENT>"
        normalized.append(line)
    return "\n".join(normalized) + "\n"


def verify_case(
    campaign: Path,
    source: dict[str, Any],
    candidates: dict[str, dict[str, Any]],
    row: dict[str, str],
) -> None:
    phase = row["phase"]
    candidate_name = row["candidate"]
    if candidate_name not in candidates:
        fail(f"unknown candidate in results: {candidate_name}")
    if not row["replica"].isdigit() or int(row["replica"]) <= 0:
        fail("invalid replica number")
    root = (
        campaign
        / "runs"
        / phase
        / candidate_name
        / f"replica_{row['replica']}"
    )
    result = load_json(root / "result.json")
    for field in RESULT_FIELDS:
        expected = str(result[field])
        if field == "wall_seconds":
            if not math.isclose(
                float(row[field]),
                float(result[field]),
                rel_tol=0,
                abs_tol=1e-6,
            ):
                fail(f"wall_seconds differs in {root}")
        elif row[field] != expected:
            fail(f"result.tsv differs from result.json for {field}: {root}")
    if row["valid"] != "1" or row["return_code"] != "0" or row["parse_error"]:
        fail(f"published campaign contains an invalid run: {root}")
    if (root / "restore.exit").read_text().strip() != "0":
        fail(f"restore exit file is nonzero: {root}")
    verify_checkpoint(root, "restore_checkpoint_sha256.txt")
    stats = first_stats(root / "stats.txt")
    for key, value in stats.items():
        if row[key] != str(value):
            fail(f"recomputed {key} differs: {root}")
    verify_cache_config(root / "config.ini", candidates[candidate_name])

    log = regular_file(root / "restore.log").read_text(errors="replace")
    lines = log.splitlines()
    if (
        sum(line == "ROI End!!!" for line in lines) != 1
        or log.count("because m5_exit instruction encountered") != 1
        or FATAL_RE.search(log) is not None
    ):
        fail(f"restore log terminal conditions are invalid: {root}")
    markers = [line for line in lines if MARKER_RE.fullmatch(line)]
    if phase == "screen_correctness":
        expected_marker = source["screen_expected_marker"]
    elif phase == "full_correctness":
        expected_marker = source["full_expected_marker"]
    elif phase == "full_performance":
        expected_marker = None
    else:
        fail(f"unknown phase: {phase}")
    if expected_marker is not None and markers != [expected_marker]:
        fail(f"exact output marker differs: {root}")

    item = candidates[candidate_name]
    command = shlex.split(regular_file(root / "restore.command").read_text())
    expected = expected_command(campaign, root, phase, item)
    if command != expected:
        fail(
            f"restore command differs: {root}: "
            f"{command_difference(command, expected)}"
        )


def verify_selection(
    campaign: Path,
    source: dict[str, Any],
    results: list[dict[str, str]],
    candidates: dict[str, dict[str, Any]],
) -> None:
    fields, rows = read_tsv(campaign / "selection.tsv")
    expected_fields = (
        "candidate",
        "screen_eligible",
        "full_correct",
        "full_deterministic",
        "reference_ticks",
        "candidate_ticks",
        "overhead_fraction",
        "promoted",
    )
    if tuple(fields) != expected_fields:
        fail("selection TSV schema differs")
    if not 1 <= len(rows) <= 2:
        fail("selection must evaluate one or two promotion candidates")
    order = ["compact", "targets1"]
    if [row["candidate"] for row in rows] != order[: len(rows)]:
        fail("selection candidate order differs")

    screen = {
        row["candidate"]: row
        for row in results
        if row["phase"] == "screen_correctness"
    }
    screen_rows = [
        row for row in results if row["phase"] == "screen_correctness"
    ]
    if (
        len(screen_rows) != 3
        or set(screen) != set(candidates)
        or any(row["replica"] != "1" for row in screen_rows)
    ):
        fail("screen matrix is incomplete")
    screen_reference_config = normalized_treatment_config(
        campaign / "runs/screen_correctness/reference/replica_1/config.ini"
    )
    for name in ("targets1", "compact"):
        candidate_config = normalized_treatment_config(
            campaign / f"runs/screen_correctness/{name}/replica_1/config.ini"
        )
        if candidate_config != screen_reference_config:
            fail(f"screen config differs beyond the treatment: {name}")
    reference_screen_ticks = int(screen["reference"]["sim_ticks"])
    screen_limit = float(source["screen_overhead_limit"])
    eligible_names = {
        name
        for name in ("compact", "targets1")
        if int(screen[name]["sim_ticks"])
        <= reference_screen_ticks * (1 + screen_limit)
    }
    reference_correctness = [
        row
        for row in results
        if row["phase"] == "full_correctness"
        and row["candidate"] == "reference"
    ]
    reference_performance = [
        row
        for row in results
        if row["phase"] == "full_performance"
        and row["candidate"] == "reference"
    ]
    reference_ticks: int | None = None
    reference_correctness_config: str | None = None
    reference_performance_config: str | None = None
    if eligible_names:
        if (
            len(reference_correctness) != 1
            or reference_correctness[0]["replica"] != "1"
            or reference_correctness[0]["valid"] != "1"
            or len(reference_performance) != 3
            or {row["replica"] for row in reference_performance}
            != {"1", "2", "3"}
            or any(row["valid"] != "1" for row in reference_performance)
        ):
            fail("fresh full reference evidence is incomplete")
        reference_tick_values = {
            row["sim_ticks"] for row in reference_performance
        }
        if len(reference_tick_values) != 1:
            fail("fresh full reference replicas are not deterministic")
        reference_ticks = int(next(iter(reference_tick_values)))
        reference_correctness_config = normalized_treatment_config(
            campaign / "runs/full_correctness/reference/replica_1/config.ini"
        )
        reference_performance_config = normalized_treatment_config(
            campaign / "runs/full_performance/reference/replica_1/config.ini"
        )
        reference_performance_by_replica = {
            row["replica"]: row for row in reference_performance
        }
        for replica in ("1", "2", "3"):
            if replica not in reference_performance_by_replica:
                fail(f"fresh reference is missing replica {replica}")
            replica_config = normalized_treatment_config(
                campaign
                / "runs/full_performance/reference"
                / f"replica_{replica}/config.ini"
            )
            if replica_config != reference_performance_config:
                fail(f"fresh reference replica config differs: {replica}")
    elif reference_correctness or reference_performance:
        fail("full reference ran despite every candidate failing the screen")

    promoted_rows = []
    for row in rows:
        name = row["candidate"]
        eligible = int(name in eligible_names)
        if row["screen_eligible"] != str(eligible):
            fail(f"screen eligibility was computed incorrectly: {name}")
        expected_reference_ticks = (
            str(reference_ticks) if reference_ticks is not None else "NA"
        )
        if row["reference_ticks"] != expected_reference_ticks:
            fail(f"selection uses the wrong reference ticks: {name}")
        full_correctness = [
            item
            for item in results
            if item["phase"] == "full_correctness"
            and item["candidate"] == name
        ]
        full_performance = [
            item
            for item in results
            if item["phase"] == "full_performance"
            and item["candidate"] == name
        ]
        if eligible == 0:
            if full_correctness or full_performance:
                fail(f"ineligible candidate received full runs: {name}")
            if (
                row["full_correct"] != "0"
                or row["full_deterministic"] != "0"
                or row["candidate_ticks"] != "NA"
                or row["overhead_fraction"] != "NA"
                or row["promoted"] != "0"
            ):
                fail(f"ineligible candidate selection fields differ: {name}")
            continue
        if len(full_correctness) != 1 or full_correctness[0]["valid"] != "1":
            fail(f"eligible candidate lacks exact full correctness: {name}")
        if row["full_correct"] != "1":
            fail(f"full correctness classification differs: {name}")
        if (
            full_correctness[0]["replica"] != "1"
            or len(full_performance) != 3
            or {row["replica"] for row in full_performance} != {"1", "2", "3"}
        ):
            fail(f"eligible full candidate lacks three replicas: {name}")
        if (
            reference_ticks is None
            or reference_correctness_config is None
            or reference_performance_config is None
        ):
            fail(f"eligible candidate has no fresh reference: {name}")
        candidate_correctness_config = normalized_treatment_config(
            campaign / f"runs/full_correctness/{name}/replica_1/config.ini"
        )
        if candidate_correctness_config != reference_correctness_config:
            fail(f"full correctness config differs beyond treatment: {name}")
        for item in full_performance:
            candidate_performance_config = normalized_treatment_config(
                campaign
                / f"runs/full_performance/{name}"
                / f"replica_{item['replica']}/config.ini"
            )
            if candidate_performance_config != reference_performance_config:
                fail(
                    f"full performance config differs beyond treatment: {name}"
                )
        ticks = {item["sim_ticks"] for item in full_performance}
        deterministic = int(len(ticks) == 1)
        if row["full_deterministic"] != str(deterministic):
            fail(f"full determinism classification differs: {name}")
        if not deterministic:
            if (
                row["candidate_ticks"] != "NA"
                or row["overhead_fraction"] != "NA"
                or row["promoted"] != "0"
            ):
                fail(
                    f"nondeterministic candidate selection fields differ: {name}"
                )
            continue
        candidate_ticks = int(next(iter(ticks)))
        overhead = candidate_ticks / reference_ticks - 1
        if row["candidate_ticks"] != str(candidate_ticks) or not math.isclose(
            float(row["overhead_fraction"]), overhead, abs_tol=5e-10
        ):
            fail(f"full overhead calculation differs: {name}")
        promoted = int(overhead <= float(source["full_overhead_limit"]))
        if row["promoted"] != str(promoted):
            fail(f"promotion decision differs: {name}")
        if promoted:
            promoted_rows.append(row)
    if len(promoted_rows) > 1:
        fail("multiple candidates were promoted")
    if len(rows) == 1 and not promoted_rows:
        fail("selection stopped before evaluating the fallback candidate")
    if promoted_rows and rows[-1] != promoted_rows[0]:
        fail("selection continued after promotion")
    selected_names = {row["candidate"] for row in rows}
    extras = [
        row
        for row in results
        if row["phase"] != "screen_correctness"
        and row["candidate"] != "reference"
        and row["candidate"] not in selected_names
    ]
    if extras:
        fail("full runs exist for a candidate outside the selection trace")

    summary = load_json(campaign / "summary.json")
    if promoted_rows:
        if reference_ticks is None:
            fail("promoted campaign has no fresh reference")
        if (campaign / "no_promotion").exists():
            fail("promoted campaign also has no_promotion")
        row = promoted_rows[0]
        name = row["candidate"]
        item = candidates[name]
        candidate_ticks = int(row["candidate_ticks"])
        if (
            summary.get("status") != "promoted"
            or summary.get("candidate") != item
            or summary.get("reference_sim_ticks") != reference_ticks
            or summary.get("candidate_sim_ticks") != candidate_ticks
            or not math.isclose(
                float(summary.get("speedup", 0)),
                reference_ticks / candidate_ticks,
                rel_tol=0,
                abs_tol=1e-12,
            )
            or not math.isclose(
                float(summary.get("elapsed_change_percent", 0)),
                (candidate_ticks / reference_ticks - 1) * 100,
                rel_tol=0,
                abs_tol=1e-12,
            )
        ):
            fail("promoted summary does not recompute")
        expected_bytes = {"1kB": 4096, "256B": 1024}[str(item["size"])]
        expected_targets = (
            int(item["mshrs"]) * int(item["targets_per_mshr"]) * 4
        )
        if (
            summary.get("retirement_cache_data_bytes_total") != expected_bytes
            or summary.get("retirement_cache_target_slots_total")
            != expected_targets
        ):
            fail("promoted cost counts are wrong")
    else:
        regular_file(campaign / "no_promotion", empty=True)
        if (
            summary.get("status") != "complete_without_promotion"
            or summary.get("reference_sim_ticks") != reference_ticks
        ):
            fail("no-promotion summary differs")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    parser.add_argument("--expected-sim-commit", required=True)
    parser.add_argument("--expected-runner-sha", required=True)
    parser.add_argument("--expected-reference-verifier-sha", required=True)
    parser.add_argument("--expected-reference-approval-sha", required=True)
    parser.add_argument("--expected-input-20k-sha", required=True)
    parser.add_argument("--expected-bfs-approval-sha", required=True)
    parser.add_argument("--expected-bfs-oracle-sha", required=True)
    parser.add_argument("--expected-source-20k-fingerprint", required=True)
    parser.add_argument("--expected-source-full-fingerprint", required=True)
    parser.add_argument("--expected-source-20k", type=Path, required=True)
    parser.add_argument("--expected-source-full", type=Path, required=True)
    parser.add_argument(
        "--expected-reference-campaign", type=Path, required=True
    )
    parser.add_argument("--expected-bfs-campaign", type=Path, required=True)
    parser.add_argument(
        "--expected-screen-overhead-limit", type=float, default=0.01
    )
    parser.add_argument(
        "--expected-full-overhead-limit", type=float, default=0.01
    )
    parser.add_argument(
        "--expected-minimum-available-kib",
        type=int,
        default=24 * 1024 * 1024,
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_sim_commit):
        fail("expected simulator commit is malformed")
    expected_hashes = (
        args.expected_runner_sha,
        args.expected_reference_verifier_sha,
        args.expected_reference_approval_sha,
        args.expected_input_20k_sha,
        args.expected_bfs_approval_sha,
        args.expected_bfs_oracle_sha,
        args.expected_source_20k_fingerprint,
        args.expected_source_full_fingerprint,
    )
    if any(SHA256_RE.fullmatch(value) is None for value in expected_hashes):
        fail("an expected authorization hash is malformed")
    expected_source_20k = args.expected_source_20k.resolve(strict=True)
    expected_source_full = args.expected_source_full.resolve(strict=True)
    expected_reference_campaign = args.expected_reference_campaign.resolve(
        strict=True
    )
    expected_bfs_campaign = args.expected_bfs_campaign.resolve(strict=True)
    if args.campaign.is_symlink():
        fail("campaign path is symlinked")
    campaign = args.campaign.resolve(strict=True)
    regular_file(campaign / "execution.complete", empty=True)
    if (campaign / "campaign.pass").exists():
        regular_file(campaign / "campaign.pass", empty=True)
    if (campaign / "campaign.fail").exists():
        fail("campaign has a fail state")
    for path in campaign.rglob("*"):
        if path.is_symlink():
            fail(f"campaign contains a symlink: {path}")
    verify_evidence(campaign)
    verify_staged_inputs(campaign)

    source = load_json(campaign / "source.json")
    if (
        source.get("schema_version") != 1
        or source.get("execution") != "serial"
        or source.get("wall_clock_timeout") != "none"
        or source.get("environment_policy") != "fixed-minimal-v1"
        or source.get("simulation_lock")
        != "/data1/nier/.dx100-virtual-simulation.lock"
        or source.get("simulator_commit") != args.expected_sim_commit
        or source.get("minimum_available_kib")
        != args.expected_minimum_available_kib
        or source.get("screen_overhead_limit")
        != args.expected_screen_overhead_limit
        or source.get("full_overhead_limit")
        != args.expected_full_overhead_limit
        or Path(source.get("source_20k_campaign", "")).resolve(strict=True)
        != expected_source_20k
        or Path(source.get("source_full_correctness_campaign", "")).resolve(
            strict=True
        )
        != expected_source_full
        or Path(source.get("reference_campaign", "")).resolve(strict=True)
        != expected_reference_campaign
        or Path(source.get("bfs_campaign", "")).resolve(strict=True)
        != expected_bfs_campaign
        or source.get("source_20k_fingerprint")
        != args.expected_source_20k_fingerprint
        or source.get("source_full_fingerprint")
        != args.expected_source_full_fingerprint
    ):
        fail("source policy or identity is invalid")
    candidates = candidate_map(source)
    source_artifacts = source.get("source_artifacts")
    if not isinstance(source_artifacts, dict) or not isinstance(
        source_artifacts.get("sha256"), dict
    ):
        fail("source artifact binding is malformed")
    staged_by_name = {
        "gem5": campaign / "inputs/bin/gem5.opt",
        "ramulator_yaml": campaign / "inputs/ramulator.yaml",
        "ramulator_lib": campaign / "inputs/lib/libramulator.so",
        "virtual_verify": campaign / "inputs/benchmark/xrage_virtual_verify",
        "virtual_perf": campaign / "inputs/benchmark/xrage_virtual",
        "input_20k": campaign / "inputs/benchmark/xrage_20k.json",
        "input_full": campaign / "inputs/benchmark/xrage_full.json",
        "runner": campaign / "inputs/runner.py",
        "reference_approval": (
            campaign / "inputs/manifests/reference_approval.json"
        ),
        "reference_verifier": campaign / "inputs/reference_verifier.py",
        "bfs_approval": campaign / "inputs/manifests/bfs_approval.json",
        "bfs_oracle": campaign / "inputs/manifests/bfs_oracle.json",
    }
    if set(source_artifacts["sha256"]) != set(staged_by_name):
        fail("source artifact name closure differs")
    trusted_staged_hashes = {
        "runner": args.expected_runner_sha,
        "reference_approval": args.expected_reference_approval_sha,
        "reference_verifier": args.expected_reference_verifier_sha,
        "input_20k": args.expected_input_20k_sha,
        "bfs_approval": args.expected_bfs_approval_sha,
        "bfs_oracle": args.expected_bfs_oracle_sha,
    }
    for name, expected in trusted_staged_hashes.items():
        if source_artifacts["sha256"].get(name) != expected:
            fail(f"source authorization hash differs: {name}")
    staged_manifest_sha = source_artifacts.get("staged_manifest_sha256")
    if (
        not isinstance(staged_manifest_sha, str)
        or SHA256_RE.fullmatch(staged_manifest_sha) is None
        or file_sha256(campaign / "staged_input_sha256.json")
        != staged_manifest_sha
    ):
        fail("recorded staged-input manifest hash differs")
    for name, path in staged_by_name.items():
        expect_hash(path, source_artifacts["sha256"][name])

    staged_verifier = campaign / "inputs/reference_verifier.py"
    staged_approval = campaign / "inputs/manifests/reference_approval.json"
    reference_campaign = expected_reference_campaign
    completed = subprocess.run(
        [
            str(staged_verifier),
            "xrage",
            str(reference_campaign),
            str(staged_approval),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        fail(
            "external reference verification no longer passes: "
            + completed.stdout.strip()
        )
    reference_ticks = verified_reference_ticks(reference_campaign)
    if source.get("upstream_reference_sim_ticks") != reference_ticks:
        fail("source upstream-reference simTicks differs")
    bfs_campaign = expected_bfs_campaign
    bfs_verification = subprocess.run(
        [
            str(staged_verifier),
            "bfs",
            str(bfs_campaign),
            str(campaign / "inputs/manifests/bfs_approval.json"),
            "--oracle",
            str(campaign / "inputs/manifests/bfs_oracle.json"),
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if bfs_verification.returncode != 0:
        fail(
            "external BFS dependency verification no longer passes: "
            + bfs_verification.stdout.strip()
        )
    screen_marker = verify_source_correctness(
        expected_source_20k, args.expected_source_20k_fingerprint
    )
    full_marker = verify_source_correctness(
        expected_source_full, args.expected_source_full_fingerprint
    )
    if (
        source.get("screen_expected_marker") != screen_marker
        or source.get("full_expected_marker") != full_marker
    ):
        fail("source exact-output marker binding differs")

    fields, rows = read_tsv(campaign / "results.tsv")
    if tuple(fields) != RESULT_FIELDS:
        fail("results TSV schema differs")
    identities = {
        (row["phase"], row["candidate"], row["replica"]) for row in rows
    }
    if len(identities) != len(rows):
        fail("results contain duplicate run identities")
    for row in rows:
        verify_case(campaign, source, candidates, row)
    verify_selection(campaign, source, rows, candidates)
    print(f"XRAGE retirement-cache ablation verified: {campaign}")


if __name__ == "__main__":
    main()
