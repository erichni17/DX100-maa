"""Fixture tests for the one-shot, fail-closed hybrid full-result classifier."""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/classify_hybrid_full_results.py"
SPEC = importlib.util.spec_from_file_location("classifier", SCRIPT)
assert SPEC and SPEC.loader
CLASSIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLASSIFIER)


class ClassifierFixtureTest(unittest.TestCase):
    def fixture(self) -> pathlib.Path:
        root = pathlib.Path(self.tmp.name)
        (root / "run").mkdir()
        (root / "run/restore.log").write_text(
            "CG_FINGERPRINT mode=MAA result=PASS\n"
            "CG_LOGICAL16_RMW_TERMINAL treatment=x result=PASS\n"
            "ROI End!!!\nExiting @ tick 99 because m5_exit instruction encountered\n"
        )
        (root / "run/stats.txt").write_text(
            "---------- Begin Simulation Statistics ----------\nsimTicks 123\n"
            "---------- End Simulation Statistics ----------\n"
        )
        (root / "result.txt").write_text("terminal=true\ncorrect=true\n")
        (root / "gate.complete").touch()
        return root

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_valid_cg_releases_only_first_roi_ticks(self) -> None:
        result = CLASSIFIER.classify_cg(self.fixture())
        self.assertEqual(result["status"], "terminal-valid")
        self.assertEqual(result["first_roi_simTicks"], 123)

    def test_missing_evidence_is_incomplete_not_success_from_absence(
        self,
    ) -> None:
        root = pathlib.Path(self.tmp.name)
        result = CLASSIFIER.classify_cg(root)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["first_roi_simTicks"])

    def test_explicit_running_marker_is_the_only_running_signal(self) -> None:
        root = pathlib.Path(self.tmp.name)
        (root / "RUNNING.status").write_text("running\n")
        result = CLASSIFIER.classify_cg(root)
        self.assertEqual(result["status"], "running")

    def test_malformed_stats_are_incomplete_and_hide_ticks(self) -> None:
        root = self.fixture()
        (root / "run/stats.txt").write_text("simTicks 123\n")
        result = CLASSIFIER.classify_cg(root)
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["first_roi_simTicks"])
        self.assertIn("malformed first statistics window", result["reasons"])

    def test_wrong_output_is_correctness_failed(self) -> None:
        root = self.fixture()
        (root / "run/restore.log").write_text(
            "CG_FINGERPRINT mode=MAA result=FAIL\n"
            "CG_LOGICAL16_RMW_TERMINAL treatment=x result=PASS\n"
            "ROI End!!!\nExiting @ tick 99 because m5_exit instruction encountered\n"
        )
        result = CLASSIFIER.classify_cg(root)
        self.assertEqual(result["status"], "correctness-failed")
        self.assertIsNone(result["first_roi_simTicks"])

    def test_sssp_requires_its_second_stats_window_and_wrapper_evidence(
        self,
    ) -> None:
        root = self.fixture()
        (root / "run/restore.log").write_text(
            "SSSP_FINGERPRINT x=1 result=PASS\n"
            "SSSP_OLD_RESULT_HYBRID_TERMINAL counts_close=1\n"
            "ROI End!!!\nExiting @ tick 99 because m5_exit instruction encountered\n"
        )
        (root / "checkpoint.exit").write_text("0\n")
        (root / "run/restore.exit").write_text("0\n")
        (root / "result.txt").write_text("validation=PASS\n")
        result = CLASSIFIER.classify_sssp(root)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn(
            "SSSP requires exactly two complete statistics windows",
            result["reasons"],
        )


if __name__ == "__main__":
    unittest.main()
