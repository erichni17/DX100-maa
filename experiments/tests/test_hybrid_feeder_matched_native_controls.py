#!/usr/bin/env python3
"""Contract tests for feeder-matched native equal-work controls."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.scripts import (
    run_hybrid_feeder_matched_native_controls as runner,
)


class FeederMatchedNativeControlsTest(unittest.TestCase):
    def test_exact_two_new_restores(self) -> None:
        self.assertEqual(
            [arm.name for arm in runner.NEW_ARMS],
            ["native16_f64", "native4_f64"],
        )
        self.assertTrue(all(arm.feeder_lines == 64 for arm in runner.NEW_ARMS))
        self.assertTrue(all(not arm.strict for arm in runner.NEW_ARMS))
        self.assertEqual(
            runner.ALL_ARM_NAMES,
            (
                "native16",
                "native4",
                "hybrid1",
                "hybrid64",
                "native16_f64",
                "native4_f64",
            ),
        )

    def test_each_command_changes_only_output_and_feeder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for arm in runner.NEW_ARMS:
                command = runner.command_for(arm, root / arm.name / "run")
                old_name = runner.PREDECESSOR_ARM[arm.name]
                prior = json.loads(
                    (
                        runner.PREDECESSOR / "arms" / old_name / "command.json"
                    ).read_text()
                )
                self.assertEqual(
                    runner.normalized_command(command),
                    runner.normalized_command(prior),
                )
                self.assertIn("--maa_virtual_index_buffer_lines=64", command)
                self.assertIn(
                    f"--checkpoint-dir={runner.PREDECESSOR / 'checkpoint'}",
                    command,
                )

    def test_wrapper_overlays_selector_without_predecessor_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            treatment = root / "treatment.txt"
            treatment.write_text("native_direct 16384\n")
            wrapper = runner.wrapped_command(root, treatment, ["gem5"])
        self.assertEqual(
            wrapper[:5],
            [str(runner.BWRAP), "--die-with-parent", "--ro-bind", "/", "/"],
        )
        self.assertIn("--ro-bind", wrapper)
        self.assertIn(str(runner.PREDECESSOR_SELECTOR), wrapper)
        self.assertNotIn("--bind", wrapper[wrapper.index(str(treatment)) :])

    def test_frozen_source_contract_includes_native_direct(self) -> None:
        proof = runner.verify_source_contract()
        self.assertEqual(proof["indirect_access_blob"], runner.SOURCE_BLOB)
        self.assertTrue(proof["native_direct_opcode_uses_feeder"])
        self.assertEqual(
            proof["activation_counters"],
            ["IND_VirtIndexLineHighWater", "IND_VirtIndexWordHighWater"],
        )

    def test_native_work_gate_requires_conservation_and_activation(
        self,
    ) -> None:
        arm = runner.NEW_ARMS[0]
        predecessor = {
            "output_hash": "x",
            "counters": {
                "simInsts": 10,
                "indirect_ops": 1,
                "stream_writes": 1,
                "scalar_ops": 1,
                "index_words": 16_384,
                "index_hwm": 16,
            },
        }
        candidate = {
            "output_hash": "x",
            "counters": {
                **predecessor["counters"],
                "index_hwm": 1_024,
                "index_line_hwm": 64,
            },
        }
        original = runner.old_index_line_hwm
        try:
            runner.old_index_line_hwm = lambda _name: 1
            activation = runner.validate_native_work(
                predecessor, candidate, arm
            )
            self.assertEqual(activation["feeder64_word_high_water"], 1_024)
            candidate["counters"]["index_words"] -= 1
            with self.assertRaisesRegex(runner.SuccessorError, "work changed"):
                runner.validate_native_work(predecessor, candidate, arm)
        finally:
            runner.old_index_line_hwm = original

    def test_fair_comparison_direction(self) -> None:
        arms = {
            "baseline": {"counters": {"simTicks": 120}},
            "candidate": {"counters": {"simTicks": 100}},
        }
        item = runner.comparison("baseline", "candidate", arms)
        self.assertAlmostEqual(item["speedup_reference_over_candidate"], 1.2)
        self.assertAlmostEqual(
            item["candidate_latency_change_fraction"], -1 / 6
        )

    def test_set_option_fails_closed(self) -> None:
        command = ["--x=1"]
        runner.set_option(command, "--x", 2)
        self.assertEqual(command, ["--x=2"])
        with self.assertRaisesRegex(runner.SuccessorError, "exactly one"):
            runner.set_option(["--x=1", "--x=2"], "--x", 3)


if __name__ == "__main__":
    unittest.main()
