import unittest
from pathlib import Path

from experiments.scripts import run_cg_strict_feeder_full_pair as runner


class StrictFeederFullPairTest(unittest.TestCase):
    def test_command_delta_is_only_depth_and_output(self) -> None:
        first = runner.command_for(runner.Arm(1), Path("/tmp/one"))
        second = runner.command_for(runner.Arm(64), Path("/tmp/sixty-four"))
        self.assertEqual(
            runner.normalized_command(first), runner.normalized_command(second)
        )
        self.assertFalse(any("--debug" in token for token in first + second))
        self.assertIn("--maa_virtual_index_buffer_lines=1", first)
        self.assertIn("--maa_virtual_index_buffer_lines=64", second)

    def test_work_validation_rejects_drift(self) -> None:
        expected = {name: 0 for name in runner.WORK_STATS}
        expected.update({name: 0 for name in runner.full.FUSED_ZERO_STATS})
        values = dict(expected)
        for name in (
            "IND_StrictTwoPhaseBFetchCycles",
            "IND_StrictTwoPhaseRowOffsetCycles",
            "IND_StrictTwoPhaseAIssueCycles",
            "IND_StrictTwoPhaseBackingCycles",
            "IND_StrictTwoPhasePageCycles",
            "IND_StrictTwoPhaseConsumerCycles",
        ):
            values[name] = 1
        runner.validate_work(values, expected)
        values[runner.WORK_STATS[0]] = 1
        with self.assertRaisesRegex(runner.PairError, "conserved"):
            runner.validate_work(values, expected)

    def test_set_arg_fails_on_duplicates(self) -> None:
        command = ["--x=1"]
        runner.set_arg(command, "--x", 2)
        self.assertEqual(command, ["--x=2"])
        with self.assertRaisesRegex(runner.PairError, "duplicate"):
            runner.set_arg(["--x=1", "--x=2"], "--x", 3)


if __name__ == "__main__":
    unittest.main()
