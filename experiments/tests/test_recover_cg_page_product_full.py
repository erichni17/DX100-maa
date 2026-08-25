import importlib.util
import pathlib
import tempfile
import unittest

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

    def test_incomplete_root_fails_without_creating_recovery_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            with self.assertRaises(MODULE.RecoveryError):
                MODULE.recover(root, ROOT)
            self.assertFalse((root / "RECOVERED_GATE.complete").exists())


if __name__ == "__main__":
    unittest.main()
