import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).parents[1] / "analysis" / "summarize_is_force_cache_pair.py"
)
SPEC = importlib.util.spec_from_file_location("is_force_summary", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ISForceCacheSummaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "manifest.tsv").write_text(
            "source_commit\tabc\n"
            "gem5_sha256\tg\n"
            "workload_sha256\tw\n"
            "frozen_input_sha256\ti\n"
            "checkpoint_tick\tcpt.1\n"
        )
        self.write_arm("control", False, 1000, 100, 20, 200, 100)
        self.write_arm("treatment", True, 900, 150, 5, 80, 60)

    def tearDown(self):
        self.temp.cleanup()

    def write_arm(self, name, force, ticks, hits, misses, writes, unique):
        arm = self.root / name
        arm.mkdir()
        (arm / "wrapper.exit").write_text("0\n")
        (arm / "terminal.status").write_text("PASS\n")
        (arm / "config.ini").write_text(
            "[system.maa]\n"
            f"force_cache_access={'true' if force else 'false'}\n"
            "num_tile_elements=16384\n"
        )
        (arm / "run.log").write_text(
            "ROI End!!!\n"
            "successfull: passed verification 6\n"
            f"WRITE_ADDR_AUDIT writes={writes} unique_cl={unique} "
            "unique_rows=4 transitions=7\n"
            "Exiting because m5_exit instruction encountered\n"
        )
        (arm / "stats.txt").write_text(
            f"{MODULE.BEGIN_STATS}\n"
            f"simTicks {ticks}\n"
            f"system.l3.demandHits_6::maa {hits}\n"
            f"system.l3.demandMisses_6::maa {misses}\n"
            f"{MODULE.END_STATS}\n"
            f"{MODULE.BEGIN_STATS}\nsimTicks {ticks + 100}\n{MODULE.END_STATS}\n"
        )

    def test_valid_pair_uses_first_roi_dump_and_stays_conservative(self):
        result = MODULE.summarize(self.root)
        self.assertEqual(result["control"]["sim_ticks"], 1000)
        self.assertEqual(result["treatment"]["sim_ticks"], 900)
        self.assertAlmostEqual(
            result["comparison"]["treatment_latency_change_pct"], -10.0
        )
        self.assertEqual(
            result["classification"]["histogram_target_residency"],
            "UNRESOLVED",
        )

    def test_rejects_extra_config_difference(self):
        config = self.root / "treatment" / "config.ini"
        config.write_text(config.read_text().replace("16384", "4096"))
        with self.assertRaisesRegex(ValueError, "configs differ"):
            MODULE.summarize(self.root)

    def test_rejects_missing_correctness_marker(self):
        log = self.root / "treatment" / "run.log"
        log.write_text(log.read_text().replace("successfull", "failed"))
        with self.assertRaisesRegex(ValueError, "passed verification"):
            MODULE.summarize(self.root)

    def test_rejects_wrong_treatment_polarity(self):
        config = self.root / "treatment" / "config.ini"
        config.write_text(config.read_text().replace("true", "false"))
        with self.assertRaisesRegex(ValueError, "polarity"):
            MODULE.summarize(self.root)


if __name__ == "__main__":
    unittest.main()
