import hashlib
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import attest_legacy_tile_runs as attester  # noqa: E402
import finalize_full_tile_sweep as finalizer  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, *, clean=True):
    run_root = tmp_path / "run"
    run_root.mkdir()
    binary = tmp_path / "gem5"
    binary.write_bytes(b"binary\n")
    binary.chmod(0o555)
    digest = sha(binary)
    campaign = run_root / "manifest.json"
    campaign.write_text(
        json.dumps({"gem5_binary": str(binary), "gem5_sha256": digest}) + "\n"
    )
    cohort = run_root / "cohort.json"
    cohort.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cohort_id": "fixture",
                "canonical_sha256": digest,
                "members": [
                    {
                        "sha256": digest,
                        "resolved_paths": [str(binary)],
                        "output_tags": ["gem5"],
                        "identity_evidence": [
                            {
                                "kind": "json-binary-identity",
                                "path": str(campaign),
                                "sha256": sha(campaign),
                                "path_field": "gem5_binary",
                                "sha256_field": "gem5_sha256",
                            }
                        ],
                    }
                ],
                "legacy_runs": [],
            }
        )
        + "\n"
    )
    outdir = run_root / "output"
    outdir.mkdir()
    (outdir / "stats.txt").write_text("simTicks 123\n")
    suffix = (
        "Exiting @ tick 456 because m5_exit instruction encountered\n"
        if clean
        else "panic: broken\n"
    )
    (outdir / "run.log").write_text(
        f"command line: {binary} --outdir={outdir} config.py\n" + suffix
    )
    return run_root, cohort, outdir


def test_successor_attests_exact_legacy_outdir(tmp_path):
    run_root, cohort, outdir = fixture(tmp_path)
    output = run_root / "cohort-v2.json"
    result = attester.build_successor(
        run_root, cohort, output, [outdir], "gem5"
    )
    assert result["ok"]
    policy = finalizer.load_binary_cohort(run_root / "manifest.json", output)
    assert str(outdir) in policy["legacy_runs"]


def test_successor_rejects_nonterminal_output(tmp_path):
    run_root, cohort, outdir = fixture(tmp_path, clean=False)
    try:
        attester.build_successor(
            run_root, cohort, run_root / "cohort-v2.json", [outdir], "gem5"
        )
    except attester.AttestationError as error:
        assert "clean terminal" in str(error)
    else:
        raise AssertionError("accepted nonterminal legacy output")
