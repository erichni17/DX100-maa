#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("analyze_gzz_authoritative.py")
SPEC = importlib.util.spec_from_file_location("gzz_authoritative", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AnalyzeGzzAuthoritativeTest(unittest.TestCase):
    def populate(self, root: Path) -> None:
        for tile in MODULE.TILES:
            point = root / f"t{tile}"
            outdir = point / f"gradzatz_n1000000_t{tile}_m2GB_test"
            outdir.mkdir(parents=True)
            benchmark_sha = f"{tile:064x}"
            gem5_sha = "a" * 64
            (point / "treatment.txt").write_text(
                "source_commit=test-commit\n"
                f"benchmark_sha256={benchmark_sha}\n"
            )
            (outdir / "run.log").write_text(
                "UME_OUTPUT_FP output_hash=9234467062988358067 nonfinite=0\n"
                "UME_REFERENCE_PASS volume_errors=0 gradient_errors=0 elements=1180000\n"
                "Exiting @ tick 123 because m5_exit instruction encountered\n"
            )
            (outdir / "stats.txt").write_text(
                "---------- Begin Simulation Statistics ----------\n"
                f"simTicks {tile * 1000}\n"
                f"simInsts {tile * 10}\n"
                "---------- End Simulation Statistics   ----------\n"
            )
            checkpoint = point / "checkpoints" / f"gzz_binsha_{benchmark_sha}"
            (outdir / "benchmark_provenance.tsv").write_text(
                "schema_version\t1\n"
                "path\t/test/gzz\n"
                f"sha256\t{benchmark_sha}\n"
                f"checkpoint\t{checkpoint}\n"
            )
            (outdir / "gem5_provenance.tsv").write_text(
                "schema_version\t2\n" f"sha256\t{gem5_sha}\n"
            )
            (point / "results_provenance_v2.tsv").write_text(
                "rc\toutput_hash\toutdir\tgem5_sha256\n"
                f"0\t9234467062988358067\t{outdir}\t{gem5_sha}\n"
            )

    def test_complete_matched_cohort(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.populate(root)
            rows = MODULE.collect(root)
            self.assertEqual([], MODULE.validate_cohort(rows))
            self.assertTrue(all(row["status"] == "valid" for row in rows))
            base = next(row for row in rows if row["tile"] == 16384)
            self.assertEqual(1.0, base["performance_16k"])

    def test_checkpoint_must_bind_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.populate(root)
            outdir = Path(MODULE.latest_result(root / "t1024")["outdir"])
            provenance = outdir / "benchmark_provenance.tsv"
            provenance.write_text(
                provenance.read_text().replace("_binsha_", "_unbound_")
            )
            rows = MODULE.collect(root)
            self.assertEqual("invalid", rows[0]["status"])
            self.assertIn("checkpoint is not keyed", rows[0]["notes"])


if __name__ == "__main__":
    unittest.main()
