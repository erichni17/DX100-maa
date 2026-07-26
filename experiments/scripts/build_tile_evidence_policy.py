#!/usr/bin/env python3
"""Build the fail-closed successor policy for saved post-ROI tile results."""

import argparse
import json
import sys
from datetime import (
    datetime,
    timezone,
)
from pathlib import Path

import finalize_full_tile_sweep as finalizer


class PolicyError(RuntimeError):
    pass


RECORDS = (
    {
        "workload_id": "gapbs-bc-s22",
        "tile": 32768,
        "anchor_tile": 16384,
        "table": "repair3-validation/gapbs/results_provenance_v2.tsv",
        "filters": {"kernel": "bc", "scale": "22", "iters": "1"},
    },
    {
        "workload_id": "gapbs-bc-s22",
        "tile": 65536,
        "anchor_tile": 16384,
        "table": "gapbs_recovery2/results_provenance_v2.tsv",
        "filters": {"kernel": "bc", "scale": "22", "iters": "1"},
    },
    {
        "workload_id": "gapbs-sssp-s22",
        "tile": 65536,
        "anchor_tile": 8192,
        "table": "gapbs_recovery2/results.tsv",
        "filters": {"kernel": "sssp", "scale": "22", "iters": "1"},
    },
)


def build(run_root, output):
    run_root = run_root.resolve()
    output = output.resolve()
    if output.exists():
        raise PolicyError(f"refusing to overwrite frozen policy: {output}")
    records = []
    for spec in RECORDS:
        table = run_root / spec["table"]
        row = finalizer.select_latest(
            finalizer.read_tsv(table), spec["filters"], spec["tile"]
        )
        if row is None:
            raise PolicyError(
                "saved ROI row is absent: "
                f"{spec['workload_id']}:{spec['tile']}"
            )
        outdir = Path(row.get("outdir", "")).resolve()
        stats = outdir / "stats.txt"
        run_log = outdir / "run.log"
        ticks = finalizer.parse_positive_int(row.get("simTicks"))
        recorded = finalizer.stats_ticks(stats)
        if (
            row.get("rc") in {None, "", "0"}
            or ticks is None
            or not recorded
            or recorded[0] != ticks
            or not run_log.is_file()
        ):
            raise PolicyError(f"saved ROI evidence is incomplete: {outdir}")
        records.append(
            {
                "workload_id": spec["workload_id"],
                "tile": spec["tile"],
                "anchor_tile": spec["anchor_tile"],
                "outdir": str(outdir),
                "wrapper_rc": row["rc"],
                "simTicks": ticks,
                "stats_sha256": finalizer.sha256(stats),
                "run_log_tail_sha256": finalizer.tail_sha256(run_log),
                "result_source": str(table.resolve()),
                "reason": (
                    "ROI statistics completed before the expensive validator; "
                    "semantic acceptance requires the named same-binary anchor"
                ),
            }
        )
    document = {
        "schema_version": 1,
        "policy_id": "full-tile-roi-anchor-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "only the three exact stopped output directories listed below",
        "records": records,
    }
    finalizer.atomic_json(output, document)
    try:
        finalizer.load_roi_evidence_policy(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return {"ok": True, "path": str(output), "records": len(records)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build(args.run_root, args.output)
    except (PolicyError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
