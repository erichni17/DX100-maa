import unittest

from experiments.scripts.strict_two_phase import (
    run_cg_fixed_storage_combiner_sweep as runner,
)


class FixedStorageCombinerSweepTest(unittest.TestCase):
    def test_matrix_is_bounded_and_has_one_baseline(self) -> None:
        self.assertEqual(len(runner.ARMS), 10)
        self.assertEqual(
            sum(arm.name == "baseline_w4_b4_rr_c32" for arm in runner.ARMS),
            1,
        )
        for arm in runner.ARMS:
            runner.validate_arm_definition(arm)
            self.assertLessEqual(arm.write_credits, runner.BASELINE[3])

    def test_set_arg_replaces_or_appends_once(self) -> None:
        command = ["gem5", "--option=1"]
        runner.set_arg(command, "--option", 2)
        self.assertEqual(command, ["gem5", "--option=2"])
        runner.set_arg(command, "--new", 3)
        self.assertEqual(command[-1], "--new=3")
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            runner.set_arg(["--x=1", "--x=2"], "--x", 3)

    def test_invalid_geometry_and_credit_growth_fail_closed(self) -> None:
        with self.assertRaises(RuntimeError):
            runner.validate_arm_definition(
                runner.Arm("bad_geometry", 8, 3, 0, 32)
            )
        with self.assertRaisesRegex(RuntimeError, "credit storage grows"):
            runner.validate_arm_definition(
                runner.Arm("grows", 4, 4, 0, 33)
            )


if __name__ == "__main__":
    unittest.main()
