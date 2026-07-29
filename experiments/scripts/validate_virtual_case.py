#!/usr/bin/env python3
"""Independently validate one virtual-tile consumer run directory."""

from __future__ import annotations

import argparse
import configparser
import csv
import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

FATAL_RE = re.compile(
    r"panic|fatal|assert|abort|segmentation fault|error:", re.IGNORECASE
)
ROOT = Path(__file__).resolve().parents[2]


def read_key_values(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ValueError(f"invalid key/value line in {path}: {line!r}")
        values[key] = value
    return values


def read_result(path: Path) -> dict[str, str]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != 1:
        raise ValueError(
            f"expected one result row in {path}, found {len(rows)}"
        )
    if None in rows[0] or any(value is None for value in rows[0].values()):
        raise ValueError(f"malformed result row in {path}")
    return rows[0]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reconstructed_source_hash(
    relative: Path, source_commit: str, patch: Path
) -> str:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        base = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{source_commit}:{relative}"],
            capture_output=True,
            check=False,
        )
        if base.returncode == 0:
            target.write_bytes(base.stdout)
        apply = subprocess.run(
            ["git", "apply", f"--include={relative}", str(patch)],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if apply.returncode != 0:
            raise ValueError(
                f"cannot reconstruct {relative} from {source_commit} and {patch}: "
                f"{apply.stderr.decode(errors='replace').strip()}"
            )
        if not target.is_file():
            raise ValueError(
                f"reconstruction did not produce {relative} from {source_commit}"
            )
        return sha256(target)


def verify_hashes(
    path: Path, source_commit: str, source_patch: Path
) -> dict[str, str]:
    entries = []
    for line in path.read_text().splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise ValueError(f"invalid checksum line in {path}: {line!r}")
        artifact = Path(parts[1].lstrip("*"))
        if not artifact.is_absolute():
            artifact = ROOT / artifact
        entries.append((parts[0], artifact.resolve(strict=True)))
    if not entries:
        raise ValueError(f"empty checksum manifest {path}")

    source_patch = source_patch.resolve(strict=True)
    patch_entries = [item for item in entries if item[1] == source_patch]
    if len(patch_entries) != 1 or sha256(source_patch) != patch_entries[0][0]:
        raise ValueError(f"frozen source patch is not authenticated by {path}")

    values = {}
    for expected, artifact in entries:
        name = artifact.name
        if name in values:
            raise ValueError(f"duplicate artifact basename {name!r} in {path}")
        actual = sha256(artifact)
        if actual != expected:
            try:
                relative = artifact.relative_to(ROOT)
            except ValueError:
                relative = None
            if relative is not None:
                actual = reconstructed_source_hash(
                    relative, source_commit, source_patch
                )
            if actual != expected:
                raise ValueError(
                    f"checksum mismatch for {artifact}: expected {expected}, got {actual}"
                )
        values[name] = actual
    for required in ("source.diff", "source_status.txt"):
        if required not in values:
            raise ValueError(
                f"checksum manifest does not authenticate {required}"
            )
    return values


def read_stats(path: Path) -> dict[str, int]:
    values = {}
    in_first_section = False
    for line in path.read_text().splitlines():
        if line.startswith("---------- Begin Simulation Statistics"):
            if in_first_section:
                raise ValueError(f"nested statistics section in {path}")
            in_first_section = True
            continue
        if line.startswith("---------- End Simulation Statistics"):
            if not in_first_section:
                continue
            return values
        if not in_first_section:
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            value = int(fields[1])
        except ValueError:
            continue
        if fields[0] in values:
            raise ValueError(f"duplicate statistic {fields[0]!r} in {path}")
        values[fields[0]] = value
    raise ValueError(f"missing complete first statistics section in {path}")


def sum_suffix(
    stats: dict[str, int], suffix: str, required: bool = True
) -> int:
    matches = [value for key, value in stats.items() if key.endswith(suffix)]
    if required and not matches:
        raise ValueError(f"missing required statistic suffix {suffix!r}")
    return sum(matches)


def stat(stats: dict[str, int], key: str) -> int:
    if key not in stats:
        raise ValueError(f"missing required statistic {key!r}")
    return stats[key]


def parse_dram(log: str, name: str) -> int:
    matches = re.findall(
        rf"^\s*{re.escape(name)}:\s+([0-9]+)(?:\s+#.*)?$",
        log,
        re.MULTILINE,
    )
    if not matches:
        raise ValueError(f"missing {name} counter in restore.log")
    # Ramulator prints cumulative counters at several lifecycle boundaries;
    # the runner's END rule records the final value.
    return int(matches[-1])


def require_int(result: dict[str, str], key: str, actual: int) -> None:
    if key not in result:
        raise ValueError(f"result is missing {key!r}")
    try:
        expected = int(result[key])
    except ValueError as exc:
        raise ValueError(
            f"result {key!r} is not an integer: {result[key]!r}"
        ) from exc
    if expected != actual:
        raise ValueError(
            f"result {key}={expected} does not match raw evidence {actual}"
        )


def validate_stats(
    result: dict[str, str], stats: dict[str, int], log: str
) -> None:
    direct = {
        "simTicks": stat(stats, "simTicks"),
        "simInsts": stat(stats, "simInsts"),
        "index_line_reads": sum_suffix(stats, "IND_VirtIndexLineReads"),
        "index_words": sum_suffix(stats, "IND_VirtIndexWords"),
        "index_hwm": sum_suffix(stats, "IND_VirtIndexWordHighWater"),
        "write_issues": sum_suffix(stats, "IND_VirtWriteIssues"),
        "write_completions": sum_suffix(stats, "IND_VirtWriteCompletions"),
        "pages_ready": sum_suffix(stats, "IND_VirtPagesReady"),
        "row_table_cache_lines": sum_suffix(stats, "IND_NumCacheLineInserted"),
        "row_table_rows_inserted": sum_suffix(stats, "IND_NumRowsInserted"),
        "row_table_unique_cache_lines": sum_suffix(
            stats, "IND_NumUniqueCacheLineInserted"
        ),
        "row_table_unique_rows": sum_suffix(
            stats, "IND_NumUniqueRowsInserted"
        ),
        "row_table_full_events": sum_suffix(stats, "IND_NumRTFull"),
        "virtual_build_rounds": sum_suffix(stats, "IND_VirtBuildRounds"),
        "dram_reads": parse_dram(log, "CH0_num_RD_commands_T"),
        "dram_activates": parse_dram(log, "CH0_num_ACT_commands_T"),
        "dram_precharges": parse_dram(log, "CH0_num_PRE_commands_T"),
    }
    source_reads = (
        sum_suffix(stats, "IND_LoadsCacheHitResponding", required=False)
        + sum_suffix(stats, "IND_LoadsCacheHitAccessing", required=False)
        + sum_suffix(stats, "IND_LoadsMemAccessing")
        - direct["index_line_reads"]
    )
    direct["source_reads"] = source_reads
    for key, value in direct.items():
        require_int(result, key, value)


def validate_config(manifest: dict[str, str], path: Path) -> None:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    with path.open() as stream:
        parser.read_file(stream)
    sections = [
        section
        for section in parser.sections()
        if parser.has_option(section, "num_tile_elements")
        and parser.has_option(section, "virtual_index_partitions")
    ]
    if len(sections) != 1:
        raise ValueError(
            f"expected one resolved MAA section in {path}, found {sections}"
        )
    section = sections[0]
    integer_keys = {
        "logical_tile_elements": "num_tile_elements",
        "physical_tile_elements": "physical_tile_elements",
        "row_table_slices": "num_initial_row_table_slices",
        "row_table_rows_per_slice": "num_row_table_rows_per_slice",
        "row_table_entries_per_subslice_row": (
            "num_row_table_entries_per_subslice_row"
        ),
        "virtual_response_slots": "virtual_response_slots",
        "virtual_response_word_pool": "virtual_response_word_pool",
        "virtual_combine_slots": "virtual_combine_slots",
        "virtual_combine_words": "virtual_combine_words",
        "virtual_combine_ways": "virtual_combine_ways",
        "virtual_combine_victim_policy": "virtual_combine_victim_policy",
        "virtual_combine_banks": "virtual_combine_banks",
        "virtual_index_partitions": "virtual_index_partitions",
    }
    for manifest_key, config_key in integer_keys.items():
        if manifest_key not in manifest:
            raise ValueError(f"manifest is missing {manifest_key!r}")
        actual = parser.getint(section, config_key)
        if actual != int(manifest[manifest_key]):
            raise ValueError(
                f"resolved config {config_key}={actual} does not match manifest "
                f"{manifest_key}={manifest[manifest_key]}"
            )
    grow = parser.getboolean(section, "virtual_grow_order")
    if int(grow) != int(manifest["virtual_grow_order"]):
        raise ValueError("resolved virtual_grow_order does not match manifest")


def validate_pages(path: Path, result: dict[str, str]) -> None:
    pages = int(result["pages_ready"])
    readiness = path / "page_readiness.tsv"
    if pages == 0:
        if readiness.exists() and readiness.read_text().strip():
            raise ValueError(
                f"unexpected page-readiness evidence in {readiness}"
            )
        return
    with readiness.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    if len(rows) != pages:
        raise ValueError(
            f"page-readiness row count {len(rows)} does not match {pages}"
        )
    expected_pages = set(range(pages))
    actual_pages = {int(row["page"]) for row in rows}
    if actual_pages != expected_pages:
        raise ValueError(
            f"page-readiness pages {actual_pages} do not match {expected_pages}"
        )
    for sequence, row in enumerate(rows, 1):
        if (
            int(row["ready_count"]) != sequence
            or int(row["total_pages"]) != pages
        ):
            raise ValueError(f"invalid page-ready sequence in {readiness}")
        if row["issued_words"] != row["completed_words"]:
            raise ValueError(
                f"page became ready before writes completed in {readiness}"
            )


def validate_case(path: Path) -> dict:
    path = path.resolve(strict=True)
    required = (
        "virtual_tile_consumer_case.pass",
        "manifest.txt",
        "result.tsv",
        "artifact_sha256.txt",
        "source.diff",
        "source_status.txt",
        "checkpoint.exit",
        "checkpoint.log",
        "restore.exit",
        "restore.log",
        "run/config.ini",
        "run/stats.txt",
    )
    for relative in required:
        if not (path / relative).is_file():
            raise ValueError(
                f"missing required run evidence {path / relative}"
            )
    for name in ("checkpoint.exit", "restore.exit"):
        value = (path / name).read_text().strip()
        if value != "0":
            raise ValueError(f"{path.name}: {name} is {value!r}, expected '0'")

    manifest = read_key_values(path / "manifest.txt")
    result = read_result(path / "result.tsv")
    log = (path / "restore.log").read_text(errors="replace")
    mode = re.escape(manifest["mode"])
    page = re.escape(manifest["page_elements"])
    output_hash = re.escape(result["output_hash"])
    result_re = re.compile(
        rf"^VIRTUAL_TILE_CONSUMER_RESULT mode={mode} page_elements={page} "
        rf"hash={output_hash} errors=0$",
        re.MULTILINE,
    )
    if len(result_re.findall(log)) != 1:
        raise ValueError(
            f"{path.name}: missing unique exact-success result marker"
        )
    if len(re.findall(r"^ROI Ended$", log, re.MULTILINE)) != 1:
        raise ValueError(f"{path.name}: missing unique ROI completion marker")
    if FATAL_RE.search(log):
        raise ValueError(f"{path.name}: fatal marker found in restore.log")
    if "because m5_exit instruction encountered" not in log:
        raise ValueError(f"{path.name}: restore did not exit through m5_exit")
    checkpoint = (path / "checkpoint.log").read_text(errors="replace")
    layout = (
        f"VIRTUAL_TILE_CONSUMER_LAYOUT mode={manifest['mode']} "
        f"page_elements={manifest['page_elements']} "
        f"logical_elements={manifest['logical_tile_elements']} mem_size=2147483648"
    )
    if layout not in checkpoint:
        raise ValueError(
            f"{path.name}: checkpoint layout marker does not match manifest"
        )

    validate_config(manifest, path / "run/config.ini")
    validate_stats(result, read_stats(path / "run/stats.txt"), log)
    validate_pages(path, result)
    hashes = verify_hashes(
        path / "artifact_sha256.txt",
        manifest["source_commit"],
        path / "source.diff",
    )
    return {"manifest": manifest, "result": result, "hashes": hashes}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    evidence = validate_case(args.run)
    print(
        f"PASS {args.run}: simTicks={evidence['result']['simTicks']} "
        f"hash={evidence['result']['output_hash']}"
    )


if __name__ == "__main__":
    main()
