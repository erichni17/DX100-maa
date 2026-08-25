import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/recover_cg_page_product_full.py"
SPEC = importlib.util.spec_from_file_location("recover_cg", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RecoverCgPageProductFullTest(unittest.TestCase):
    def test_marker_values_keep_exact_tokens(self):
        values = MODULE.marker_values(
            "CG_FINGERPRINT elements=150000 x_q5=abc result=PASS"
        )
        self.assertEqual(
            values,
            {"elements": "150000", "x_q5": "abc", "result": "PASS"},
        )

    def test_first_window_stats_are_required_and_summed(self):
        stats = """---------- Begin Simulation Statistics ----------
simTicks 123
system.maa.I0_IND_SoaJitInstructions 2
system.maa.I1_IND_SoaJitInstructions 3
---------- End Simulation Statistics   ----------
---------- Begin Simulation Statistics ----------
simTicks 999
---------- End Simulation Statistics   ----------
"""
        section = MODULE.first_stats_section(stats)
        self.assertEqual(MODULE.first_stat(section, "simTicks"), 123)
        self.assertEqual(
            MODULE.stat_sum(section, "IND_SoaJitInstructions"), 5
        )
        with self.assertRaises(MODULE.RecoveryError):
            MODULE.stat_sum(section, "IND_Missing")

    def test_relative_delta_handles_signed_and_zero_reference(self):
        self.assertAlmostEqual(MODULE.relative_delta("-9", "-10"), 0.1)
        self.assertGreater(MODULE.relative_delta("1", "0"), 1.0e299)

    def test_branch_status_allows_only_ahead_count_change(self):
        branch, ahead = MODULE.branch_ahead(
            "## topic...origin/topic [ahead 250]"
        )
        self.assertEqual(branch, "## topic...origin/topic")
        self.assertEqual(ahead, 250)
        with self.assertRaises(MODULE.RecoveryError):
            MODULE.branch_ahead(" M benchmarks/NAS/cg/cg.cpp")

    def test_hash_ledger_resolves_relative_paths_from_explicit_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            artifact = root / "artifact.txt"
            artifact.write_text("payload\n")
            ledger = root / "ledger.txt"
            ledger.write_text(f"{MODULE.sha256(artifact)}  artifact.txt\n")
            MODULE.verify_ledger(ledger, root)
            artifact.write_text("changed\n")
            with self.assertRaises(MODULE.RecoveryError):
                MODULE.verify_ledger(ledger, root)

    def test_self_consistent_arbitrary_artifact_ledger_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = pathlib.Path(directory)
            root = parent / MODULE.EXPECTED_ROOT_NAME
            repo = parent / "repo"
            (root / "input").mkdir(parents=True)
            repo.mkdir()
            arbitrary = root / "arbitrary"
            arbitrary.write_text("attacker-selected\n")
            (root / "input/artifact_sha256.before").write_text(
                f"{MODULE.sha256(arbitrary)}  {arbitrary}\n"
            )
            with self.assertRaises(MODULE.RecoveryError):
                MODULE.verify_expected_artifact_ledger(root, repo)

    def test_recovery_source_enforces_liveness_trace_and_atomic_seal(self):
        source = SCRIPT.read_text()
        for required in (
            "RUNNING.status still reports running",
            "active_root_process(root)",
            "disabled-full run unexpectedly contains a logical-page trace",
            "EXPECTED_CHECKPOINT_LEDGER_SHA256",
            "artifact ledger is not the pinned full-CG set",
            "verify_snapshot(initial_snapshot)",
            "write_temporary(result_path",
            "os.replace(result_temporary, result_path)",
            "publish_gate_after_validation(root, repo)",
            "validate_seal(root, repo, require_gate=False)",
            "gate.unlink()",
        ):
            self.assertIn(required, source)

    def test_validate_reopens_raw_contract_and_rejects_forged_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            result = {
                "schema": "dx100.cg.physical_page_product_soa_jit.recovered.v1",
                "validation": "PASS",
                "performance_status": "correctness_only_unpromoted",
                "native_reruns": 0,
                "simTicks": 1,
            }
            result_path = root / "recovered_result.json"
            result_path.write_text(json.dumps(result) + "\n")
            ledger = root / "recovered_result_sha256.txt"
            ledger.write_text(f"{MODULE.sha256(result_path)}  {result_path}\n")
            (root / "RECOVERED_GATE.complete").write_text("PASS\n")
            regenerated = dict(result)
            regenerated["simTicks"] = 2
            with mock.patch.object(
                MODULE,
                "recover",
                return_value=(regenerated, {result_path: MODULE.sha256(result_path)}),
            ) as recover:
                with self.assertRaises(MODULE.RecoveryError):
                    MODULE.validate_seal(root, ROOT)
            recover.assert_called_once_with(
                root, ROOT, allow_existing_seal=True
            )

    def test_failed_post_publication_validation_removes_pass_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            with mock.patch.object(
                MODULE,
                "validate_seal",
                side_effect=[{}, MODULE.RecoveryError("injected post-gate failure")],
            ) as validate:
                with self.assertRaises(MODULE.RecoveryError):
                    MODULE.publish_gate_after_validation(root, ROOT)
            self.assertFalse((root / "RECOVERED_GATE.complete").exists())
            self.assertEqual(
                validate.call_args_list,
                [
                    mock.call(root, ROOT, require_gate=False),
                    mock.call(root, ROOT, require_gate=True),
                ],
            )

    def test_incomplete_root_fails_without_creating_recovery_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            with self.assertRaises(MODULE.RecoveryError):
                MODULE.recover(root, ROOT)
            self.assertFalse((root / "RECOVERED_GATE.complete").exists())


if __name__ == "__main__":
    unittest.main()
