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
    cg = run_root / "cg_recovery2"
    gapbs.mkdir(parents=True)
    cg.mkdir(parents=True)
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
    cg_out = cg / "cg"
    make_gate(
        cg_out,
        snapshot,
        source,
        digest,
        tag,
        [
            "CG_FINGERPRINT mode=MAA elements=150000 x_raw=abc z_raw=def "
            "x_q5=1 x_q6=2 z_q5=3 z_q6=4 x_sum=-385.9 "
            "x_norm_sq=0.99999999995 z_sum=-1793 z_norm_sq=21.58 "
            "rnorm=0.00109749 zeta=109.99944 nonfinite_x=0 "
            "nonfinite_z=0 unquantizable_x=0 unquantizable_z=0 result=PASS",
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
        cg / "results_provenance_v2.tsv",
        {
            **common,
            "tile": "65536",
            "simTicks": "202",
            "outdir": str(cg_out),
        },
    )
    return run_root, digest


def test_promotion_waits_for_both_terminal_gates(tmp_path):
    run_root, _digest = fixture(tmp_path)
    (run_root / "cg_recovery2/results_provenance_v2.tsv").unlink()
    try:
        promoter.promote(run_root)
    except promoter.PromotionNotReady:
        pass
    else:
        raise AssertionError("promotion accepted a missing CG gate")
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


def test_promotion_rejects_wrong_cg_fingerprint(tmp_path):
    run_root, _digest = fixture(tmp_path)
    run_log = run_root / "cg_recovery2/cg/run.log"
    run_log.write_text(
        run_log.read_text().replace("result=PASS", "result=FAIL")
    )
    try:
        promoter.promote(run_root)
    except promoter.PromotionError as error:
        assert "passing CG fingerprint" in str(error)
    else:
        raise AssertionError("promotion accepted a wrong CG fingerprint")
    assert not (run_root / "gem5-binary-cohort.json").exists()
