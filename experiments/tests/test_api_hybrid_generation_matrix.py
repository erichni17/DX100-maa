#!/usr/bin/env python3
"""Focused contracts for the API hybrid generation matrix."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/scripts/run_api_hybrid_generation_matrix.py"
SPEC = importlib.util.spec_from_file_location("api_hybrid_matrix", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class ApiHybridGenerationMatrixTest(unittest.TestCase):
    def test_existing_labels_are_preserved_and_original_is_new(self) -> None:
        self.assertEqual(
            runner.ALL_ARM_NAMES,
            (
                "native16",
                "native4",
                "hybrid1",
                "hybrid64",
                "native16_f64",
                "native4_f64",
                "original_hybrid64",
            ),
        )
        self.assertEqual(
            runner.SELECTED_ARM_NAMES,
            (
                "native16_f64",
                "native4_f64",
                "original_hybrid64",
                "hybrid64",
            ),
        )
        self.assertFalse(runner.ORIGINAL_ARM.strict)
        self.assertTrue(
            next(
                arm for arm in runner.base.ARMS if arm.name == "hybrid64"
            ).strict
        )

    def test_original_command_has_only_declared_mechanism_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = runner.command_for(Path(directory) / "run")
        prior = json.loads(
            (
                runner.matched.PREDECESSOR / "arms/hybrid64/command.json"
            ).read_text()
        )
        self.assertNotIn(runner.STRICT_OPTION, command)
        self.assertIn(runner.STRICT_OPTION, prior)
        self.assertIn("--maa_virtual_index_buffer_lines=64", command)
        self.assertIn(
            f"--maa_num_initial_row_table_slices="
            f"{runner.ORIGINAL_ROW_SLICES}",
            command,
        )
        self.assertEqual(command[0], prior[0])
        self.assertEqual(
            runner.normalized_treatment_command(command),
            runner.normalized_treatment_command(prior),
        )
        self.assertTrue(
            any(token.startswith("--checkpoint-dir=") for token in command)
        )
        self.assertIn(
            str(runner.matched.PREDECESSOR / "input/workload"), command
        )

    def test_historical_original_is_source_grounded_strict_off(self) -> None:
        proof = runner.verify_historical_original()
        self.assertEqual(proof["case_label"], "transparent_4k")
        self.assertEqual(proof["treatment"], "transparent 4096")
        self.assertFalse(proof["strict_option_present"])
        self.assertFalse(proof["virtual_strict_two_phase"])
        self.assertTrue(proof["terminal"] and proof["exact_output"])
        self.assertFalse(proof["performance_comparable_to_frozen_matrix"])

    def test_frozen_source_proves_default_off_and_distinct_paths(self) -> None:
        proof = runner.verify_source_contract()
        self.assertFalse(proof["strict_cli_default"])
        self.assertIn("fence A issue", proof["strict_semantics"])
        self.assertIn("pressure drains", proof["strict_off_semantics"])
        self.assertEqual(
            proof["source_blobs"],
            runner.SOURCE_BLOBS,
        )

    def test_original_geometry_is_bounded_below_logical_work(self) -> None:
        self.assertEqual(runner.ORIGINAL_ROW_CAPACITY, 8_192)
        self.assertLess(
            runner.ORIGINAL_ROW_CAPACITY, runner.base.TOTAL_ELEMENTS
        )
        self.assertEqual(runner.ORIGINAL_ARM.feeder_lines, 64)
        self.assertEqual(runner.ORIGINAL_ARM.logical_elements, 16_384)
        self.assertEqual(runner.ORIGINAL_ARM.physical_elements, 4_096)

    def test_original_order_signature_requires_positive_overlap(self) -> None:
        event = {
            "b_first_issue_tick": "10",
            "b_last_issue_tick": "90",
            "b_last_response_tick": "100",
            "row_offset_first_insert_tick": "20",
            "row_offset_last_insert_tick": "95",
            "a_first_issue_tick": "50",
            "a_last_issue_tick": "120",
            "a_last_response_tick": "130",
            "backing_first_issue_tick": "60",
            "backing_last_issue_tick": "125",
            "backing_last_ack_tick": "140",
            "page_first_ready_tick": "110",
            "page_last_ready_tick": "140",
            "complete_tick": "141",
        }
        runner.validate_original_macro_order(event, "original_hybrid64")
        event["a_first_issue_tick"] = "101"
        event["backing_first_issue_tick"] = "110"
        with self.assertRaisesRegex(runner.MatrixError, "did not overlap"):
            runner.validate_original_macro_order(event, "original_hybrid64")

    def test_normalization_does_not_hide_unrelated_hardware(self) -> None:
        left = ["gem5", "--outdir=a", "--maa_virtual_words_per_cycle=1"]
        right = ["gem5", "--outdir=b", "--maa_virtual_words_per_cycle=2"]
        self.assertNotEqual(
            runner.normalized_treatment_command(left),
            runner.normalized_treatment_command(right),
        )

    def test_selected_matrix_writer_handles_predecessor_counter_schema(
        self,
    ) -> None:
        counters = {
            "simTicks": 10,
            "simInsts": 9,
            "indirect_ops": 1,
            "stream_writes": 4,
            "scalar_ops": 4,
            "index_words": 16_384,
            "index_hwm": 1_024,
            "write_issues": 8,
            "write_completions": 8,
            "strict_operations": 0,
            "offset_epoch_drains": 0,
        }
        arms = {
            name: {
                "classification": "ACCEPT",
                "output_hash": runner.base.EXPECTED_OUTPUT_HASH,
                "spec": {"strict": name == "hybrid64"},
                "counters": dict(counters),
            }
            for name in runner.SELECTED_ARM_NAMES
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner.write_matrix(root, {"arms": arms})
            rows = (root / "matrix.tsv").read_text().splitlines()
        self.assertEqual(len(rows), 5)
        self.assertIn("row_table_full_events", rows[0])


if __name__ == "__main__":
    unittest.main()
