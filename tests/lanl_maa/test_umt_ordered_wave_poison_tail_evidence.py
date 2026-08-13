#!/usr/bin/env python3
"""Static evidence checks for the issue-two poison-tail live gate."""

import importlib.util
import pathlib
import unittest

DRIVER_PATH = pathlib.Path(__file__).with_name(
    "run_umt_ordered_wave_poison_tail_smoke.py"
)
SPEC = importlib.util.spec_from_file_location(
    "umt_poison_tail_driver", DRIVER_PATH
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class IssueTwoPoisonTailEvidenceTest(unittest.TestCase):
    def test_cold_d32_line_read_oracle(self):
        self.assertEqual(
            DRIVER.ISSUE2_D32_INPUT_LINE_READS,
            {1: 16, 7: 16, 8: 16, 9: 32, 31: 91, 32: 88},
        )
        self.assertEqual(
            set(DRIVER.D32_GROUP_COUNTS),
            set(DRIVER.ISSUE2_D32_INPUT_LINE_READS),
        )


if __name__ == "__main__":
    unittest.main()
