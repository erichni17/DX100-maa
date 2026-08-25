import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/classify_cg_page_product_native16.py"
SPEC = importlib.util.spec_from_file_location("native16_classifier", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Native16ClassifierAdversarialTest(unittest.TestCase):
    def test_wrong_roots_are_rejected_before_evidence_is_read(self):
        with self.assertRaises(MODULE.ClassificationError):
            MODULE.assert_root(
                pathlib.Path("/tmp/not-candidate"), MODULE.BASELINE_ROOT
            )
        with self.assertRaises(MODULE.ClassificationError):
            MODULE.assert_root(
                MODULE.CANDIDATE_ROOT, pathlib.Path("/tmp/not-baseline")
            )

    def test_wrong_hash_is_not_self_certifying(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            artifact = root / "artifact"
            artifact.write_text("forged\n")
            with self.assertRaises(MODULE.ClassificationError):
                MODULE.verify_hashes(root, {"artifact": "0" * 64})

    def test_fingerprint_requires_all_exact_native16_fields(self):
        baseline = MODULE.values(
            "CG_FINGERPRINT elements=150000 result=PASS nonfinite_x=0 nonfinite_z=0 x_q5=a x_q6=b z_q5=c z_q6=d x_sum=1 x_norm_sq=1 z_sum=1 z_norm_sq=1 rnorm=1 zeta=1"
        )
        candidate = dict(baseline)
        candidate["z_q6"] = "forged"
        with self.assertRaises(MODULE.ClassificationError):
            MODULE.require_exact_fingerprint(candidate, baseline)

    def test_wrong_mechanism_counter_is_rejected(self):
        numbers = {
            "full_windows": 10960,
            "staged_index_words": 10960 * 16384,
            "product_words": 10960 * 16384,
            "index_publish_pages": 10960 * 4,
            "product_publish_pages": 10960 * 4,
            "physical_page_product_windows": 10960,
            "q_spmv_eligible_windows": 8768,
            "q_spmv_routed_windows": 8768,
            "residual_spmv_eligible_windows": 2192,
            "residual_spmv_routed_windows": 2191,
        }
        with self.assertRaises(MODULE.ClassificationError):
            MODULE.require_terminal_counts(numbers)

    def test_live_service_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                MODULE, "live_process", return_value="pid=9"
            ):
                with self.assertRaises(MODULE.ClassificationError):
                    MODULE.require_dead(pathlib.Path(directory))

    def test_validate_rejects_forged_but_hash_consistent_seal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / MODULE.RESULT_NAME).write_text(
                json.dumps({"forged": True})
            )
            (root / MODULE.LEDGER_NAME).write_text("ignored\n")
            (root / MODULE.GATE_NAME).write_text("PASS_NATIVE16_ORACLE\n")
            with mock.patch.object(MODULE, "assert_root"), mock.patch.object(
                MODULE, "verify_result_ledger"
            ), mock.patch.object(
                MODULE,
                "classify_existing",
                return_value=({"forged": False}, {}),
            ):
                with self.assertRaises(MODULE.ClassificationError):
                    MODULE.validate_seal(root, pathlib.Path("/baseline"))

    def test_source_never_launches_or_reruns_gem5(self):
        source = SCRIPT.read_text()
        self.assertNotIn("subprocess", source)
        self.assertIn('"native_reruns": 0', source)
        self.assertIn("refusing to overwrite one-shot certificate", source)


if __name__ == "__main__":
    unittest.main()
