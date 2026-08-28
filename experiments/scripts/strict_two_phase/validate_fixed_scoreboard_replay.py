#!/usr/bin/env python3
"""Revalidate the sealed strict line-combined fixed-scoreboard replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from experiments.scripts.strict_two_phase import (
    run_cg_strict_line_combined as runner,
)


class ValidationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_hashes(root: Path, artifacts: dict[str, str], label: str) -> None:
    for relative, expected in artifacts.items():
        path = Path(relative)
        require(
            not path.is_absolute() and ".." not in path.parts,
            f"{label} contains unsafe artifact path: {relative}",
        )
        artifact = root / path
        require(artifact.is_file(), f"{label} artifact is missing: {artifact}")
        require(
            re.fullmatch(r"[0-9a-f]{64}", expected) is not None,
            f"{label} has invalid SHA-256 for {relative}",
        )
        require(
            sha256_file(artifact) == expected,
            f"{label} artifact changed: {artifact}",
        )


def semantic_lines(path: Path) -> list[str]:
    prefixes = (
        "CG_FINGERPRINT ",
        "CG_LOGICAL16_RMW_TERMINAL ",
        "CG_REDUCTION_EVIDENCE ",
        "CG_OUTER_REDUCTION_EVIDENCE ",
    )
    return [
        line
        for line in path.read_text(errors="replace").splitlines()
        if line.startswith(prefixes)
    ]


def validate(manifest_path: Path) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text())
    require(
        manifest.get("schema") == "dx100.strict_linecombined_seal.v1",
        "invalid fixed-scoreboard manifest schema",
    )
    root = Path(manifest["root"])
    matched = Path(manifest["matched_root"])
    require(
        root.is_dir() and matched.is_dir(),
        "sealed evidence root is missing",
    )
    verify_hashes(root, manifest["artifacts"], "candidate")
    verify_hashes(matched, manifest["matched_artifacts"], "matched")

    matched_result = runner.verify_matched_root(matched)
    result = json.loads((root / "result.json").read_text())
    for field, expected in manifest["expected_result"].items():
        require(
            result.get(field) == expected,
            f"result field {field} changed: "
            f"{result.get(field)!r} != {expected!r}",
        )
    require(
        result.get("matched_root") == str(matched),
        "result does not name the sealed matched root",
    )
    require(
        result.get("matched_strict_simTicks")
        == matched_result.get("strict_reference_simTicks"),
        "candidate and matched result disagree on reference simTicks",
    )

    require(
        (root / "restore.log.exit").read_text() == "0\n",
        "candidate wrapper exit is not zero",
    )
    require(
        (root / "gate.complete").read_text()
        == "COMPLETE_CG_STRICT_LINE_COMBINED\n"
        "decision=VALID_LINE_COMBINED_ATTRIBUTION\n"
        "correctness=EXACT_MATCH\n",
        "candidate terminal gate changed",
    )
    log_lines = (root / "restore.log").read_text(errors="replace").splitlines()
    require(
        sum(
            re.fullmatch(
                r"Exiting @ tick [0-9]+ because m5_exit instruction "
                r"encountered",
                line,
            )
            is not None
            for line in log_lines
        )
        == 1,
        "candidate does not have exactly one m5_exit",
    )
    require(
        not any(runner.gate.base.FATAL_RE.search(line) for line in log_lines),
        "candidate log contains fatal text",
    )
    require(
        semantic_lines(root / "restore.log")
        == semantic_lines(matched / "strict/restore.log"),
        "candidate CG semantics differ from the matched strict arm",
    )

    config = (root / "config.ini").read_text().splitlines()
    require(
        "virtual_strict_two_phase=true" in config
        and "virtual_masked_writes=true" in config,
        "candidate treatment flags did not resolve",
    )
    command = json.loads((root / "command.json").read_text())
    require(
        command.count("--maa_virtual_strict_two_phase") == 1
        and command.count("--maa_virtual_masked_writes") == 1,
        "candidate command treatment is not exact",
    )
    return {
        "schema": "dx100.strict_linecombined_validation.v1",
        "terminal": True,
        "decision": "VALID_FIXED_SCOREBOARD_REPLAY",
        "root": str(root),
        "source_commit": result["source_commit"],
        "gem5_sha256": result["gem5_sha256"],
        "simTicks": result["line_combined_simTicks"],
        "whole_windows": result["whole_windows"],
        "p_backing_write_issues": result["p_backing_write_issues"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = validate(args.manifest)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
