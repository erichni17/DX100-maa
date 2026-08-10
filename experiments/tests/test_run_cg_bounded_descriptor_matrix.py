#!/usr/bin/env python3

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class CGBoundedDescriptorMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = (
            ROOT / "experiments/scripts/run_cg_bounded_descriptor_matrix.sh"
        ).read_text()

    def test_uses_one_binary_and_checkpoint(self):
        self.assertIn("comparison=one_binary_one_checkpoint", self.runner)
        self.assertEqual(self.runner.count("--max-checkpoints=1"), 1)

    def test_has_matched_controls_and_two_bounded_arms(self):
        for arm in (
            "matched16",
            "matched4",
            "bounded4_cached",
            "bounded4_bypass",
        ):
            self.assertIn(arm, self.runner)

    def test_winner_geometry_is_finite(self):
        self.assertIn("--maa_physical_tile_elements=\"$physical\"", self.runner)
        self.assertIn("--maa_virtual_descriptor_spool_read_credits=24", self.runner)
        self.assertIn("--maa_virtual_index_filter_words_per_cycle=64", self.runner)

    def test_validation_fails_closed(self):
        self.assertIn("x_q5=88c0975669c7062d", self.runner)
        self.assertIn("result=PASS", self.runner)
        self.assertIn("$scans -gt 0", self.runner)
        self.assertIn("$external -gt 0", self.runner)


if __name__ == "__main__":
    unittest.main()
