#!/usr/bin/env python3
"""Create an immutable successor cohort for exact legacy tile outputs.

This tool never changes a run directory or an existing cohort manifest.  It
only binds the command-line gem5 executable and its hash to an exact output
directory. Clean completions are required unless ``--allow-stopped-roi`` is
used; that exception supplies identity only, while a separate ROI policy and
same-binary correctness anchor remain mandatory.
"""

import argparse
import hashlib
import json
import sys
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

import finalize_full_tile_sweep as finalizer


class AttestationError(RuntimeError):
    pass


def artifact(path, *, sample_large=False):
    path = path.resolve()
    if path.is_symlink() or not path.is_file():
        raise AttestationError(f"artifact is not a regular file: {path}")
    record = {
        "path": str(path),
        "bytes": path.stat().st_size,
    }
    if sample_large and record["bytes"] > 64 * 1024 * 1024:
        sample_bytes = 1024 * 1024
        digest = hashlib.sha256()
        with path.open("rb") as source:
            digest.update(source.read(sample_bytes))
            source.seek(-sample_bytes, 2)
            digest.update(source.read(sample_bytes))
        record.update(
            hash_method="sha256(first-1MiB || last-1MiB)",
            sampled_sha256=digest.hexdigest(),
        )
    else:
        record.update(
            hash_method="sha256(full-file)", sha256=finalizer.sha256(path)
        )
    return record


def find_member(raw, digest):
    for member in raw.get("members", ()):
        if member.get("sha256") == digest:
            return member
    raise AttestationError(
        f"command binary {digest} is outside the base cohort"
    )


def attest_one(run_root, raw, outdir, output_tag, allow_stopped_roi=False):
    outdir = outdir.resolve()
    if outdir.is_symlink() or not outdir.is_dir():
        raise AttestationError(f"outdir is not a regular directory: {outdir}")
    run_log = outdir / "run.log"
    stats = outdir / "stats.txt"
    if (
        not run_log.is_file()
        or not stats.is_file()
        or stats.stat().st_size == 0
    ):
        raise AttestationError(
            f"terminal run artifacts are incomplete: {outdir}"
        )
    with run_log.open("rb") as source:
        source.seek(-min(run_log.stat().st_size, 1024 * 1024), 2)
        terminal_tail = source.read().decode(errors="replace")
    clean_exit = "m5_exit instruction encountered" in terminal_tail
    tail_failed = (
        "panic:" in terminal_tail.lower() or "fatal:" in terminal_tail.lower()
    )
    if tail_failed or (not clean_exit and not allow_stopped_roi):
        raise AttestationError(
            f"run is not a clean terminal completion: {outdir}"
        )
    command_binary = Path(finalizer._command_line_binary(run_log, str(outdir)))
    if command_binary.is_symlink() or not command_binary.is_file():
        raise AttestationError(
            f"command binary is not a regular non-symlink file: {command_binary}"
        )
    digest = finalizer.cached_binary_sha256(command_binary)
    member = find_member(raw, digest)
    resolved_path = str(command_binary.resolve())
    paths = member.setdefault("resolved_paths", [])
    if resolved_path not in paths:
        paths.append(resolved_path)
        paths.sort()
    tags = member.setdefault("output_tags", [])
    if output_tag not in tags:
        tags.append(output_tag)
        tags.sort()

    short = hashlib.sha256(str(outdir).encode()).hexdigest()[:16]
    evidence_dir = run_root / "evidence/legacy-run-identities"
    identity_path = evidence_dir / f"legacy-run-{short}.json"
    if identity_path.exists():
        identity = json.loads(identity_path.read_text())
        expected = {
            "outdir": str(outdir),
            "gem5_binary": resolved_path,
            "gem5_sha256": digest,
            "output_tag": output_tag,
        }
        if any(identity.get(key) != value for key, value in expected.items()):
            raise AttestationError(
                f"existing identity evidence conflicts: {identity_path}"
            )
        return {
            "outdir": str(outdir),
            "resolved_path": resolved_path,
            "sha256": digest,
            "output_tag": output_tag,
            "identity_evidence": [
                {
                    "kind": "json-binary-identity",
                    "path": str(identity_path.resolve()),
                    "sha256": finalizer.sha256(identity_path),
                    "path_field": "gem5_binary",
                    "sha256_field": "gem5_sha256",
                    "outdir_field": "outdir",
                }
            ],
        }
    identity = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "outdir": str(outdir),
        "gem5_binary": resolved_path,
        "gem5_sha256": digest,
        "output_tag": output_tag,
        "method": "single command line near run.log start plus current executable hash",
        "completion_scope": (
            "identity-only for separately governed stopped-after-ROI output"
            if allow_stopped_roi and not clean_exit
            else "clean m5_exit completion"
        ),
        "artifacts": [artifact(run_log, sample_large=True), artifact(stats)],
    }
    finalizer.atomic_json(identity_path, identity)
    return {
        "outdir": str(outdir),
        "resolved_path": resolved_path,
        "sha256": digest,
        "output_tag": output_tag,
        "identity_evidence": [
            {
                "kind": "json-binary-identity",
                "path": str(identity_path.resolve()),
                "sha256": finalizer.sha256(identity_path),
                "path_field": "gem5_binary",
                "sha256_field": "gem5_sha256",
                "outdir_field": "outdir",
            }
        ],
    }


def build_successor(
    run_root,
    base_path,
    output_path,
    outdirs,
    output_tag,
    allow_stopped_roi=False,
):
    run_root = run_root.resolve()
    base_path = base_path.resolve()
    output_path = output_path.resolve()
    if output_path.exists():
        raise AttestationError(
            f"refusing to overwrite successor cohort: {output_path}"
        )
    finalizer.load_binary_cohort(run_root / "manifest.json", base_path)
    raw = json.loads(base_path.read_text())
    raw["supersedes"] = {
        "path": str(base_path),
        "sha256": finalizer.sha256(base_path),
    }
    if not str(raw["cohort_id"]).endswith("+legacy-attested"):
        raw["cohort_id"] = str(raw["cohort_id"]) + "+legacy-attested"
    existing = {item.get("outdir") for item in raw.get("legacy_runs", ())}
    additions = []
    for outdir in outdirs:
        record = attest_one(
            run_root, raw, outdir, output_tag, allow_stopped_roi
        )
        if record["outdir"] in existing:
            raise AttestationError(
                f"duplicate legacy outdir: {record['outdir']}"
            )
        existing.add(record["outdir"])
        additions.append(record)
    raw.setdefault("legacy_runs", []).extend(additions)
    raw["legacy_runs"] = sorted(
        raw["legacy_runs"], key=lambda item: item["outdir"]
    )
    finalizer.atomic_json(output_path, raw)
    try:
        finalizer.load_binary_cohort(run_root / "manifest.json", output_path)
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return {
        "ok": True,
        "successor": str(output_path),
        "attested": [record["outdir"] for record in additions],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--base-cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, action="append", required=True)
    parser.add_argument("--output-tag", default="gem5.opt.ovl_base")
    parser.add_argument("--allow-stopped-roi", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_successor(
            args.run_root,
            args.base_cohort,
            args.output,
            args.outdir,
            args.output_tag,
            args.allow_stopped_roi,
        )
    except (
        AttestationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
