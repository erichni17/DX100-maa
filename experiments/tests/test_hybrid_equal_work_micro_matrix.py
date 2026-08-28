#!/usr/bin/env python3
"""Focused contract tests for the exact equal-work hybrid micro driver."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "experiments/scripts/run_hybrid_equal_work_micro_matrix.py"
)
SPEC = importlib.util.spec_from_file_location("hybrid_equal_work", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class EqualWorkContractTest(unittest.TestCase):
    def test_exact_four_arm_contract(self) -> None:
        arms = {arm.name: arm for arm in MODULE.ARMS}
        self.assertEqual(
            set(arms), {"native16", "native4", "hybrid1", "hybrid64"}
        )
        self.assertEqual(
            (
                arms["native16"].logical_elements,
                arms["native16"].physical_elements,
                arms["native16"].expected_indirect_ops,
            ),
            (16_384, 16_384, 1),
        )
        self.assertEqual(
            (
                arms["native4"].logical_elements,
                arms["native4"].physical_elements,
                arms["native4"].expected_indirect_ops,
            ),
            (16_384, 4_096, 4),
        )
        self.assertEqual(
            (
                arms["hybrid1"].logical_elements,
                arms["hybrid1"].physical_elements,
            ),
            (16_384, 4_096),
        )
        self.assertEqual(
            (arms["hybrid1"].feeder_lines, arms["hybrid64"].feeder_lines),
            (1, 64),
        )
        self.assertTrue(arms["hybrid1"].strict and arms["hybrid64"].strict)

    def test_one_binary_and_only_declared_command_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            commands = {
                arm.name: MODULE.common_restore_command(
                    base / "gem5.opt",
                    base / "workload",
                    base / "ramulator.yaml",
                    base / "checkpoint",
                    base / arm.name,
                    base / "treatment.txt",
                    arm,
                )
                for arm in MODULE.ARMS
            }
        self.assertEqual(
            {command[0] for command in commands.values()},
            {str(base / "gem5.opt")},
        )
        self.assertEqual(
            len(
                {
                    json.dumps(MODULE.normalized_command(command))
                    for command in commands.values()
                }
            ),
            1,
        )
        self.assertIn("--maa_virtual_strict_two_phase", commands["hybrid64"])
        self.assertNotIn(
            "--maa_virtual_strict_two_phase", commands["native16"]
        )
        self.assertTrue(
            all(
                "--maa_virtual_masked_writes" in command
                for command in commands.values()
            )
        )
        self.assertTrue(
            all("--mem-channels=2" in command for command in commands.values())
        )
        for option in (
            "--maa_virtual_combine_slots=16",
            "--maa_virtual_combine_words=0",
            "--maa_virtual_combine_ways=0",
            "--maa_virtual_response_slots=8",
            "--maa_virtual_response_word_pool=0",
            "--maa_virtual_words_per_cycle=1",
            "--maa_virtual_max_outstanding_writes=32",
        ):
            self.assertTrue(
                all(option in command for command in commands.values())
            )

    def test_legacy_attribution_binary_mismatch_is_source_grounded(
        self,
    ) -> None:
        matrix = (
            ROOT / "experiments/scripts/run_virtual_tile_attribution_matrix.sh"
        ).read_text()
        self.assertIn("test_virtual_tile_attribution_T16384", matrix)
        self.assertIn("if [[ $case_name == native_fused_4k ]]", matrix)
        self.assertIn("test_virtual_tile_attribution_T4096", matrix)

    def test_event_parser_requires_exact_single_event(self) -> None:
        line = (
            "123: system.maa: event=strict_two_phase_timing schema=2 "
            "logical=16384 order_ok=1 terminal=1"
        )
        parsed = MODULE.exactly_one_event([line], "strict_two_phase_timing")
        self.assertEqual(parsed["logical"], "16384")
        with self.assertRaisesRegex(MODULE.MatrixError, "expected one"):
            MODULE.exactly_one_event([line, line], "strict_two_phase_timing")

    def test_normalization_does_not_hide_semantic_knobs(self) -> None:
        left = ["gem5", "--outdir=a", "--maa_virtual_words_per_cycle=4"]
        right = ["gem5", "--outdir=b", "--maa_virtual_words_per_cycle=8"]
        self.assertNotEqual(
            MODULE.normalized_command(left), MODULE.normalized_command(right)
        )

    def test_selected_binary_and_simulator_commit_are_pinned(self) -> None:
        self.assertRegex(MODULE.EXPECTED_GEM5_SHA256, r"^[0-9a-f]{64}$")
        self.assertRegex(MODULE.EXPECTED_RAMULATOR_SHA256, r"^[0-9a-f]{64}$")
        self.assertRegex(MODULE.SIMULATOR_SOURCE_COMMIT, r"^[0-9a-f]{40}$")
        self.assertEqual(MODULE.EXPECTED_OUTPUT_HASH, "7228541527853630339")

    def test_masked_retirement_gate_fails_closed(self) -> None:
        good = {
            "write_issues": 10,
            "write_completions": 10,
            "full_writes": 3,
            "partial_writes": 7,
        }
        MODULE.validate_masked_retirement(good, "hybrid")
        bad = dict(good, partial_writes=0, full_writes=10)
        with self.assertRaisesRegex(
            MODULE.MatrixError, "masked retirement inactive"
        ):
            MODULE.validate_masked_retirement(bad, "hybrid")
        bad = dict(good, write_completions=9)
        with self.assertRaisesRegex(MODULE.MatrixError, "write closure"):
            MODULE.validate_masked_retirement(bad, "hybrid")


if __name__ == "__main__":
    unittest.main()
