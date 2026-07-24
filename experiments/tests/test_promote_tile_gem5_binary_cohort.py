import csv
import hashlib
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finalize_full_tile_sweep as finalizer  # noqa: E402
import promote_tile_gem5_binary_cohort as promoter  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, document):
    path.write_text(json.dumps(document) + "\n")


def write_table(path, row):
    with path.open("w", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=list(row), delimiter="\t")
        writer.writeheader()
        writer.writerow(row)


def make_gate(outdir, snapshot, source, digest, tag, markers, ticks):
    outdir.mkdir(parents=True)
    (outdir / "run.log").write_text(
        f"command line: {snapshot} --outdir={outdir} config.py\n"
        + "\n".join(markers)
        + f"\nExiting @ tick {ticks} because m5_exit instruction encountered\n"
    )
    (outdir / "stats.txt").write_text(f"simTicks {ticks}\n")
    (outdir / "gem5_provenance.tsv").write_text(
        "schema_version\t2\n"
        "requested_gbin\tgem5.opt.ovl_base\n"
        f"resolved_path\t{source}\n"
        f"execution_snapshot\t{snapshot}\n"
        f"sha256\t{digest}\n"
        f"output_tag\t{tag}\n"
    )


def fixture(tmp_path):
    run_root = tmp_path / "run"
    run_root.mkdir()
    canonical = tmp_path / "canonical-gem5"
    canonical.write_bytes(b"canonical\n")
    source = tmp_path / "repair-gem5"
    source.write_bytes(b"repair\n")
    digest = sha(source)
    snapshot = tmp_path / "snapshot-gem5"
    snapshot.write_bytes(source.read_bytes())
    snapshot.chmod(0o555)
    write_json(
        run_root / "manifest.json",
        {
            "schema_version": 1,
            "gem5_binary": str(canonical),
            "gem5_sha256": sha(canonical),
        },
    )
    write_json(
        run_root / "repair5-gapbs-retry-manifest.json",
        {
            "schema_version": 1,
            "gem5_binary": str(snapshot),
            "gem5_sha256": digest,
            "task_ids": ["gapbs-bfs-t1024", "gapbs-bc-t1024"],
        },
    )
    tag = f"gem5.opt.ovl_base_sha256_{digest}"
    gapbs = run_root / "repair3-validation/gapbs"
    ume = run_root / "repair3-validation/ume"
    gapbs.mkdir(parents=True)
    ume.mkdir(parents=True)
    bfs_out = gapbs / "bfs"
    make_gate(
        bfs_out,
        snapshot,
        source,
        digest,
        tag,
        [f"BFS_FP fixture {finalizer.BFS_DEPTH_ORACLE}"],
        101,
    )
    ume_out = ume / "ume"
    make_gate(
        ume_out,
        snapshot,
        source,
        digest,
        tag,
        [
            "UME_OUTPUT_FP output_hash=9234467062988358067 nonfinite=0",
            "UME_REFERENCE_PASS fixture",
        ],
        202,
    )
    common = {
        "timestamp": "2026-07-23T00:00:00Z",
        "gem5_bin": "gem5.opt.ovl_base",
        "rc": "0",
        "maa_cycles_total": "1",
        "overlap_both_any": "0",
        "write_only_over_write": "0",
        "gem5_resolved_path": str(source),
        "gem5_sha256": digest,
        "gem5_output_tag": tag,
    }
    write_table(
        gapbs / "results_provenance_v2.tsv",
        {
            **common,
            "kernel": "bfs",
            "tile": "1024",
            "scale": "22",
            "iters": "1",
            "simTicks": "101",
            "outdir": str(bfs_out),
        },
    )
    write_table(
        ume / "results_provenance_v2.tsv",
        {
            **common,
            "kernel": "gradzatz",
            "tile": "16384",
            "n": "1000000",
            "simTicks": "202",
            "output_hash": "9234467062988358067",
            "outdir": str(ume_out),
        },
    )
    return run_root, digest


def test_promotion_waits_for_both_terminal_gates(tmp_path):
    run_root, _digest = fixture(tmp_path)
    (run_root / "repair3-validation/ume/results_provenance_v2.tsv").unlink()
    try:
        promoter.promote(run_root)
    except promoter.PromotionNotReady:
        pass
    else:
        raise AssertionError("promotion accepted a missing UME gate")
    assert not (run_root / "gem5-binary-cohort.json").exists()


def test_promotion_writes_loadable_idempotent_cohort(tmp_path):
    run_root, digest = fixture(tmp_path)
    result = promoter.promote(run_root)
    assert result["action"] == "promoted"
    policy = finalizer.load_binary_cohort(
        run_root / "manifest.json",
        run_root / "gem5-binary-cohort.json",
    )
    assert digest in policy["members"]
    again = promoter.promote(run_root)
    assert again["action"] == "already-promoted"


def test_promotion_rejects_wrong_ume_fingerprint(tmp_path):
    run_root, _digest = fixture(tmp_path)
    results = run_root / "repair3-validation/ume/results_provenance_v2.tsv"
    results.write_text(
        results.read_text().replace(
            "9234467062988358067", "9234467062988358066"
        )
    )
    try:
        promoter.promote(run_root)
    except promoter.PromotionError as error:
        assert "output_hash" in str(error)
    else:
        raise AssertionError("promotion accepted a wrong UME fingerprint")
    assert not (run_root / "gem5-binary-cohort.json").exists()
