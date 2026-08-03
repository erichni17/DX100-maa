#!/usr/bin/env python3
"""Fail-closed path/hash/completion audit of frozen gem5 controls."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")


def resolve_beneath(base: Path, relative: str) -> Path:
    require(
        not Path(relative).is_absolute(),
        f"artifact path is absolute: {relative}",
    )
    path = (base / relative).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError:
        require(False, f"artifact escapes evidence directory: {relative}")
    return path


def verify_artifact(path: Path, expected_hash: str, role: str) -> None:
    require(path.is_file(), f"missing {role}: {path}")
    require(
        sha256(path) == expected_hash,
        f"{role} hash changed at exact path {path}",
    )


def parse_key_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        require(key not in values, f"duplicate manifest key {key} in {path}")
        values[key] = value
    return values


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=here / "gem5_control_evidence.json"
    )
    parser.add_argument("--evidence-root", type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.manifest.read_text())
    require(evidence["schema"] == 2, "unsupported evidence schema")
    declared_root = Path(evidence["evidence_root"]).resolve()
    root = (args.evidence_root or declared_root).resolve()
    repo_root = here.parents[1]
    if args.evidence_root is None:
        require(root == declared_root, "evidence root path changed")
    require(root.is_dir(), f"missing evidence root {root}")

    roles: set[str] = set()
    for artifact in evidence["shared_artifacts"]:
        role = artifact["role"]
        require(role not in roles, f"duplicate shared artifact role {role}")
        roles.add(role)
        if artifact["base"] == "evidence_root":
            path = resolve_beneath(root, artifact["path"])
        elif artifact["base"] == "repo_root":
            path = resolve_beneath(repo_root, artifact["path"])
        elif artifact["base"] == "absolute":
            path = Path(artifact["path"])
            require(path.is_absolute(), f"{role} path is not absolute")
            require(
                str(path.resolve()) == artifact["path"],
                f"{role} exact path changed",
            )
        else:
            require(False, f"invalid base for {role}")
        verify_artifact(path, artifact["sha256"], role)

    for control in evidence["controls"]:
        directory = resolve_beneath(root, control["directory"])
        require(directory.is_dir(), f"missing control directory {directory}")
        artifacts: dict[str, Path] = {}
        for role, artifact in control["artifacts"].items():
            path = resolve_beneath(directory, artifact["path"])
            verify_artifact(
                path, artifact["sha256"], f"{control['name']} {role}"
            )
            artifacts[role] = path

        require(
            int(artifacts["restore_exit"].read_text().strip())
            == control["wrapper_exit"],
            f"{control['name']} wrapper status changed",
        )
        log = artifacts["restore_log"].read_text(errors="replace")
        require(
            control["exact_output"] in log,
            f"{control['name']} exact output marker absent",
        )
        require(
            control["terminal_marker"] in log,
            f"{control['name']} terminal marker absent",
        )
        require(
            not any(marker in log.lower() for marker in ("panic:", "fatal:")),
            f"{control['name']} contains panic/fatal",
        )
        require(
            artifacts["stats"].stat().st_size > 0,
            f"{control['name']} final stats are empty",
        )

        with artifacts["result_tsv"].open(newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        require(
            len(rows) == 1, f"{control['name']} result.tsv row count changed"
        )
        row = rows[0]
        for field, expected in control["result_fields"].items():
            require(
                field in row, f"{control['name']} lacks result field {field}"
            )
            require(
                int(row[field]) == expected,
                f"{control['name']} {field}: {row[field]} != {expected}",
            )

        manifest_values = parse_key_values(artifacts["run_manifest"])
        for field, expected in control["run_manifest_fields"].items():
            require(
                manifest_values.get(field) == str(expected),
                f"{control['name']} manifest {field} changed",
            )

        trace = artifacts["virtual_trace"].read_text(errors="replace")
        require(
            "BOUNDED_ROW_META" not in trace,
            f"{control['name']} unexpectedly claims a physical trace",
        )

    print(
        "PASS frozen gem5 controls: every consumed artifact path/hash, exact "
        "output, terminal status, final stats, config identity, result.tsv, "
        "and observed 1025/1028 B-line counters match. Physical bounded-row "
        "records remain absent; a new owner-run trace is required."
    )


if __name__ == "__main__":
    main()
