import csv
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import analyze_gzz_tile_attribution as analyzer  # noqa: E402


def make_run(root: Path, name: str, ticks: int, idle: int, insts: int) -> None:
    cohort = root / name
    outdir = cohort / "gradzatz_n1000000_t16384_m2GB_test"
    outdir.mkdir(parents=True)
    (outdir / "stats.txt").write_text(
        "---------- Begin Simulation Statistics ----------\n"
        f"simTicks {ticks}\n"
        f"simInsts {insts}\n"
        "system.maa.cycles_BUSY 100\n"
        f"system.maa.cycles_IDLE {idle}\n"
        f"system.maa.cycles_TOTAL {100 + idle}\n"
        "system.maa.numInst 50\n"
        "---------- End Simulation Statistics   ----------\n"
    )
    (outdir / "run.log").write_text(
        "UME_OUTPUT_FP output_hash=9234467062988358067 nonfinite=0\n"
        "UME_REFERENCE_PASS volume_errors=0 gradient_errors=0 elements=1180000\n"
        "Exiting @ tick 1 because m5_exit instruction encountered\n"
    )
    (outdir / "maa_controller.trace").write_text(
        "10: system.maa: recvTimingReq: INSTR[one] received!\n"
        "20: system.maa: dispatchInstruction: INSTR[one] failed to dipatch!\n"
        "30: system.maa: recvTimingReq: INSTR[two] received!\n"
    )
    with (cohort / "results.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle, delimiter="\t", fieldnames=("rc", "outdir", "output_hash")
        )
        writer.writeheader()
        writer.writerow(
            {"rc": 0, "outdir": outdir, "output_hash": analyzer.EXPECTED_HASH}
        )


class AnalyzeGzzTileAttributionTest(unittest.TestCase):
    def test_complete_cohort_attributes_feed_granularity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = {
                "native_p16384": (1000, 10, 100),
                "native_p32768": (1200, 110, 200),
                "native_p65536": (1300, 160, 210),
                "logical_p16384_l16384": (1010, 11, 110),
                "logical_p32768_l16384": (1050, 31, 115),
                "logical_p65536_l16384": (1070, 41, 117),
            }
            for name, metrics in values.items():
                make_run(root, name, *metrics)
            rows = analyzer.collect(root)
            summary = analyzer.add_comparisons(rows)
            self.assertTrue(all(row["status"] == "valid" for row in rows))
            self.assertTrue(summary["complete"])
            self.assertEqual(
                summary["metrics"]["simTicks"]["32768"]["fraction_recovered"],
                0.8,
            )
            self.assertEqual(rows[0]["trace_requests"], 2)
            self.assertEqual(rows[0]["trace_dispatch_failures"], 1)
            self.assertEqual(rows[0]["trace_interarrival_max_ticks"], 20)
            self.assertIn("explains most", summary["verdict"])

    def test_missing_run_remains_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rows = analyzer.collect(Path(temporary))
            self.assertEqual(rows[0]["status"], "pending")
            self.assertFalse(analyzer.add_comparisons(rows)["complete"])


if __name__ == "__main__":
    unittest.main()
