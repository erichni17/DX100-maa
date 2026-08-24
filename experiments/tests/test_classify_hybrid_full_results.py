"""Fixture tests for the one-shot, fail-closed hybrid full-result classifier."""
from __future__ import annotations

import hashlib
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
    @staticmethod
    def ledger(path: pathlib.Path, artifacts: list[pathlib.Path]) -> None:
        path.write_text(
            "".join(
                f"{hashlib.sha256(artifact.read_bytes()).hexdigest()}  {artifact}\n"
                for artifact in artifacts
            )
        )

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
        (root / "run/restore.exit").write_text("0\n")
        (root / "run/config.ini").write_text(
            "num_tile_elements=16384\n"
            "physical_tile_elements=4096\n"
            "num_offset_table_entries=16384\n"
            "num_offset_table_epoch_entries=16384\n"
            "num_initial_row_table_slices=32\n"
        )
        (root / "manifest.txt").write_text(
            "schema=dx100.cg.physical_page_product_soa_jit.v2\n"
            "arm=hybrid_only\nnative_reruns=0\nlogical_elements=16384\n"
            "physical_tile_elements=4096\nhidden_logical_payload_bytes=0\n"
            "host_payload_access=0\n"
        )
        self.ledger(
            root / "result_sha256.txt",
            [
                root / "result.txt",
                root / "run/restore.log",
                root / "run/stats.txt",
            ],
        )
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

    def test_running_marker_cannot_mask_fatal_evidence(self) -> None:
        root = pathlib.Path(self.tmp.name)
        (root / "run").mkdir()
        (root / "RUNNING.status").write_text("running\n")
        (root / "run/restore.log").write_text("fatal: broken\n")
        result = CLASSIFIER.classify_cg(root)
        self.assertEqual(result["status"], "correctness-failed")

    def test_malformed_stats_are_incomplete_and_hide_ticks(self) -> None:
        root = self.fixture()
        (root / "run/stats.txt").write_text("simTicks 123\n")
        self.ledger(
            root / "result_sha256.txt",
            [
                root / "result.txt",
                root / "run/restore.log",
                root / "run/stats.txt",
            ],
        )
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

    def test_valid_full_is_releases_ticks(self) -> None:
        root = pathlib.Path(self.tmp.name)
        (root / "run").mkdir()
        (root / "run/restore.log").write_text(
            "IS_SCALAR_SOA_JIT_SELECTION compiled=1 treatment=scalar_soa_jit "
            "legacy_default=0\n"
            "IS_SCALAR_SOA_JIT_TERMINAL generations=2048 result=PASS\n"
            "successfull: passed verification 6\nROI End!!!\n"
            "Exiting @ tick 99 because m5_exit instruction encountered\n"
        )
        (root / "run/stats.txt").write_text(
            f"{CLASSIFIER.STATS_BEGIN}\nsimTicks 456\n{CLASSIFIER.STATS_END}\n"
        )
        (root / "run/restore.exit").write_text("0\n")
        (root / "terminal.status").write_text("PASS\n")
        (root / "result.tsv").write_text(
            "action\tsimTicks\tinstructions\tterminals\tselected\trejected\n"
            "full\t456\t2048\t2048\t33554432\t0\t1\n"
        )
        artifacts = {}
        for name in ("source", "gem5", "guest", "input"):
            artifact = root / name
            artifact.write_text(name)
            artifacts[name] = artifact
        (root / "manifest.txt").write_text(
            "action=full\nlogical_elements=16384\n"
            "physical_tile_elements=4096\nnative_runs=0\n"
            + "".join(
                f"{name}_path={path}\n{name}_sha256="
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}\n"
                for name, path in artifacts.items()
            )
        )
        result = CLASSIFIER.classify_is(root)
        self.assertEqual(result["status"], "terminal-valid")
        self.assertEqual(result["first_roi_simTicks"], 456)

        expected = hashlib.sha256(b"gem5").hexdigest()
        artifacts["gem5"].write_text("replacement")
        archived = root / "archived-gem5"
        archived.write_text("gem5")
        (root / "runtime_gem5_recovery.manifest").write_text(
            "schema=dx100.runtime_executable_recovery.v1\n"
            "reason=lead_build_path_replaced_after_process_start\n"
            f"live_exe_sha256={expected}\n"
            f"archived_gem5_path={archived}\n"
            f"archived_gem5_sha256={expected}\n"
            "simulation_state_changed=false\n"
        )
        recovered = CLASSIFIER.classify_is(root)
        self.assertEqual(recovered["status"], "terminal-valid")

    def test_valid_partial_pro_is_recovered_from_raw_evidence(self) -> None:
        root = pathlib.Path(self.tmp.name)
        run = root / "PRO/run"
        run.mkdir(parents=True)
        (run / "run.log").write_text(
            "HASHJOIN_HYBRID_SOA_JIT enabled=1 first_eligible=1 "
            "first_routed=1 second_eligible=0 second_routed=0 eligible=1 "
            "routed=1 physical_spd_elements=4096 logical_reorder_elements=16384 "
            "row_table_slices=32 indirect_units=4 candidate_only=1\n"
            "HASHJOIN_HYBRID_RESULT result=2000000\n"
            "Exiting @ tick 99 because m5_exit instruction encountered\n"
        )
        values = {
            "IND_SoaJitInstructions": 1,
            "IND_SoaJitTerminalCompletions": 1,
            "IND_SoaJitSelected": 16384,
            "IND_SoaJitPredicateRejected": 0,
            "IND_SoaJitValueReadIssues": 0,
            "IND_SoaJitValueReadResponses": 0,
            "IND_SoaJitAliasesApplied": 16384,
            "IND_BoundedGlobalMergeFallbacks": 0,
            "IND_SoaJitAReadIssues": 16384,
            "IND_SoaJitAReadResponses": 16384,
            "IND_SoaJitAWriteIssues": 16384,
            "IND_SoaJitAWriteResponses": 16384,
        }
        (run / "stats.txt").write_text(
            CLASSIFIER.STATS_BEGIN
            + "\nsimTicks 789\n"
            + "".join(
                f"unit_{name} {value}\n" for name, value in values.items()
            )
            + CLASSIFIER.STATS_END
            + "\n"
        )
        (run / "config.ini").write_text(
            "num_tile_elements=16384\nphysical_tile_elements=4096\n"
            "num_offset_table_entries=16384\n"
            "num_offset_table_epoch_entries=16384\n"
            "num_initial_row_table_slices=32\n"
        )
        (root / "results.tsv").write_text(
            "kernel\tresult\trouted\tsoa_instructions\tsoa_terminals\tsimTicks\n"
        )
        result = CLASSIFIER.classify_hashjoin(root, "PRO")
        self.assertEqual(result["status"], "terminal-valid")
        self.assertEqual(result["first_roi_simTicks"], 789)


if __name__ == "__main__":
    unittest.main()
