#!/usr/bin/env python3
"""Promote the reviewed GAPBS repair binary after two completed gates.

The command is fail-closed and idempotent.  It writes the successor cohort
manifest only after the repaired binary passes the BFS retry-contract
regression and the independently verified NAS CG 64K compatibility sentinel
with complete wrapper, stats, m5-exit, correctness, and immutable-binary
evidence.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

import finalize_full_tile_sweep as finalizer


class PromotionNotReady(RuntimeError):
    """Required terminal gate evidence is not available yet."""


class PromotionError(RuntimeError):
    """Gate evidence or binary identity is invalid."""


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pinned(path, kind, **fields):
    return {
        "kind": kind,
        "path": str(path.resolve()),
        "sha256": file_sha256(path),
        **fields,
    }


def artifact(path):
    path = path.resolve()
    if path.is_symlink() or not path.is_file():
        raise PromotionError(f"gate artifact is not a regular file: {path}")
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
    }


def load_json(path, label):
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PromotionError(f"cannot read {label}: {path}") from error
    if not isinstance(document, dict):
        raise PromotionError(f"{label} is not a JSON object: {path}")
    return document


def select_gate_row(path, filters, tile):
    if not path.is_file():
        raise PromotionNotReady(f"gate result table is absent: {path}")
    rows = finalizer.read_tsv(path)
    row = finalizer.select_latest(rows, filters, tile)
    if row is None:
        raise PromotionNotReady(
            f"terminal gate row is not available in {path}"
        )
    return row


def verify_gate(
    *,
    name,
    results_path,
    row,
    oracle,
    expected_hash,
    candidate_sha,
):
    valid, ticks, oracle_id, notes = finalizer.validate_row(
        row,
        oracle,
        expected_hash=expected_hash,
    )
    if not valid:
        raise PromotionError(f"{name} gate invalid: " + "; ".join(notes))
    if row.get("gem5_sha256") != candidate_sha:
        raise PromotionError(f"{name} gate used a different binary hash")
    resolved_path = finalizer.require_absolute_path(
        row.get("gem5_resolved_path", ""),
        f"{name} resolved binary",
    )
    expected_tag = f"gem5.opt.ovl_base_sha256_{candidate_sha}"
    if row.get("gem5_output_tag") != expected_tag:
        raise PromotionError(f"{name} gate has an unexpected output tag")

    outdir = Path(row["outdir"]).resolve()
    sidecar_path = outdir / "gem5_provenance.tsv"
    sidecar = finalizer.read_kv_tsv(sidecar_path)
    if sidecar.get("schema_version") != "2":
        raise PromotionError(f"{name} gate lacks schema-v2 provenance")
    execution_snapshot = finalizer.require_absolute_path(
        sidecar.get("execution_snapshot", ""),
        f"{name} execution snapshot",
    )
    snapshot_path = Path(execution_snapshot)
    if (
        snapshot_path.is_symlink()
        or not snapshot_path.is_file()
        or snapshot_path.stat().st_mode & 0o222
        or file_sha256(snapshot_path) != candidate_sha
    ):
        raise PromotionError(
            f"{name} gate snapshot is not immutable candidate evidence"
        )
    expected_sidecar = {
        "resolved_path": resolved_path,
        "execution_snapshot": execution_snapshot,
        "sha256": candidate_sha,
        "output_tag": expected_tag,
    }
    for field, expected in expected_sidecar.items():
        if sidecar.get(field) != expected:
            raise PromotionError(f"{name} gate sidecar {field} does not match")
    command_binary = finalizer._command_line_binary(
        outdir / "run.log", str(outdir)
    )
    if command_binary != execution_snapshot:
        raise PromotionError(
            f"{name} gate command did not execute the frozen snapshot"
        )
    return (
        {
            "name": name,
            "result_row": dict(sorted(row.items())),
            "simTicks": ticks,
            "oracle": oracle_id,
            "artifacts": [
                artifact(results_path),
                artifact(sidecar_path),
                artifact(outdir / "run.log"),
                artifact(outdir / "stats.txt"),
            ],
        },
        resolved_path,
        expected_tag,
        execution_snapshot,
    )


def promote(run_root):
    run_root = run_root.resolve()
    campaign_path = run_root / "manifest.json"
    repair_path = run_root / "repair5-gapbs-retry-manifest.json"
    cohort_path = run_root / "gem5-binary-cohort.json"
    campaign = load_json(campaign_path, "campaign manifest")
    repair = load_json(repair_path, "repair manifest")
    canonical_sha = finalizer.require_sha256(
        campaign.get("gem5_sha256", ""), "canonical binary hash"
    )
    canonical_path = finalizer.require_absolute_path(
        campaign.get("gem5_binary", ""), "canonical binary path"
    )
    candidate_sha = finalizer.require_sha256(
        repair.get("gem5_sha256", ""), "repair binary hash"
    )
    candidate_snapshot = finalizer.require_absolute_path(
        repair.get("gem5_binary", ""), "repair snapshot path"
    )
    task_ids = repair.get("task_ids")
    if not isinstance(task_ids, list) or not task_ids:
        raise PromotionError("repair manifest lacks an exact task scope")

    if cohort_path.is_file():
        policy = finalizer.load_binary_cohort(campaign_path, cohort_path)
        if candidate_sha not in policy["members"]:
            raise PromotionError(
                "existing cohort manifest omits the repair binary"
            )
        return {
            "ok": True,
            "action": "already-promoted",
            "cohort": str(cohort_path),
            "cohort_id": policy["cohort_id"],
            "candidate_sha256": candidate_sha,
        }

    gapbs_results = (
        run_root / "repair3-validation/gapbs/results_provenance_v2.tsv"
    )
    cg_results = run_root / "cg_recovery2/results_provenance_v2.tsv"
    bfs_row = select_gate_row(
        gapbs_results,
        {"kernel": "bfs", "scale": "22", "iters": "1"},
        1024,
    )
    cg_row = select_gate_row(cg_results, {}, 65536)
    snapshot_path = Path(candidate_snapshot)
    if (
        snapshot_path.is_symlink()
        or not snapshot_path.is_file()
        or snapshot_path.stat().st_mode & 0o222
    ):
        raise PromotionError(
            "repair snapshot must be a read-only regular non-symlink file"
        )
    if file_sha256(snapshot_path) != candidate_sha:
        raise PromotionError("repair snapshot hash does not match manifest")
    bfs, bfs_resolved_path, output_tag, bfs_snapshot = verify_gate(
        name="gapbs-bfs-t1024",
        results_path=gapbs_results,
        row=bfs_row,
        oracle="bfs",
        expected_hash=None,
        candidate_sha=candidate_sha,
    )
    cg, cg_resolved_path, cg_output_tag, cg_snapshot = verify_gate(
        name="nas-cg-t65536",
        results_path=cg_results,
        row=cg_row,
        oracle="cg",
        expected_hash=None,
        candidate_sha=candidate_sha,
    )
    if cg_output_tag != output_tag:
        raise PromotionError("gate output tags differ")
    source_binary = Path(candidate_snapshot)
    if source_binary.is_symlink() or not source_binary.is_file():
        raise PromotionError("repair source binary is not a regular file")
    if file_sha256(source_binary) != candidate_sha:
        raise PromotionError(
            "repair source binary changed before compatibility promotion"
        )

    stamp = datetime.now(timezone.utc).isoformat()
    identity_path = run_root / f"gem5-binary-identity-{candidate_sha}.json"
    compatibility_path = (
        run_root / f"gem5-binary-compatibility-{candidate_sha}.json"
    )
    identity = {
        "schema_version": 1,
        "created_at": stamp,
        "binary": {
            "path": candidate_snapshot,
            "sha256": candidate_sha,
            "execution_snapshot": candidate_snapshot,
            "execution_snapshot_sha256": candidate_sha,
        },
        "repair_manifest": artifact(repair_path),
    }
    compatibility = {
        "schema_version": 1,
        "created_at": stamp,
        "canonical_sha256": canonical_sha,
        "candidate_sha256": candidate_sha,
        "decision": "compatible",
        "scope": {
            "description": "reviewed CPU-side retry repair for failed GAPBS cells",
            "task_ids": sorted(task_ids),
        },
        "method": (
            "clean exact BFS retry-contract regression plus independent NAS "
            "CG 64K output-fingerprint sentinel; source review confirms the "
            "simulator delta is limited to dropping translated uncacheable "
            "hardware prefetches"
        ),
        "gates": [bfs, cg],
    }
    finalizer.atomic_json(identity_path, identity)
    finalizer.atomic_json(compatibility_path, compatibility)

    campaign_tag = Path(canonical_path).name
    cohort = {
        "schema_version": 1,
        "cohort_id": f"full-tile-repair-{candidate_sha[:12]}",
        "canonical_sha256": canonical_sha,
        "members": [
            {
                "sha256": canonical_sha,
                "resolved_paths": [canonical_path],
                "output_tags": [
                    campaign_tag,
                    f"{campaign_tag}_sha256_{canonical_sha}",
                ],
                "identity_evidence": [
                    pinned(
                        campaign_path,
                        "json-binary-identity",
                        path_field="gem5_binary",
                        sha256_field="gem5_sha256",
                    )
                ],
            },
            {
                "sha256": candidate_sha,
                "resolved_paths": sorted(
                    {
                        candidate_snapshot,
                        bfs_resolved_path,
                        cg_resolved_path,
                        bfs_snapshot,
                        cg_snapshot,
                    }
                ),
                "output_tags": [output_tag],
                "identity_evidence": [
                    pinned(
                        identity_path,
                        "json-binary-identity",
                        path_field="binary.path",
                        sha256_field="binary.sha256",
                    )
                ],
                "compatibility_evidence": [
                    pinned(
                        compatibility_path,
                        "json-binary-compatibility",
                    )
                ],
            },
        ],
        "legacy_runs": [],
    }
    temporary = cohort_path.with_name(f".{cohort_path.name}.{os.getpid()}")
    try:
        finalizer.atomic_json(temporary, cohort)
        policy = finalizer.load_binary_cohort(campaign_path, temporary)
        os.replace(temporary, cohort_path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "ok": True,
        "action": "promoted",
        "cohort": str(cohort_path),
        "cohort_id": policy["cohort_id"],
        "candidate_sha256": candidate_sha,
        "gates": [bfs["name"], cg["name"]],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = promote(args.run_root)
    except PromotionNotReady as error:
        print(json.dumps({"ok": False, "ready": False, "error": str(error)}))
        return 3
    except (
        PromotionError,
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
