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

    def test_uses_native_and_bounded_binaries_with_matching_checkpoints(self):
        self.assertIn(
            "comparison=three-binaries-three-checkpoints", self.runner
        )
        self.assertIn("make_checkpoint native16", self.runner)
        self.assertIn("make_checkpoint native4", self.runner)
        self.assertIn("make_checkpoint bounded", self.runner)
        self.assertIn("--max-checkpoints=1", self.runner)

    def test_requires_and_freezes_precomputed_input(self):
        self.assertIn("CG_DATA_HEADER", self.runner)
        self.assertIn('cp --reflink=auto "$data_header_source"', self.runner)
        self.assertIn("input_mode=precomputed-cg-data-header", self.runner)
        self.assertIn("maa_mem_size_bytes=2147483648", self.runner)
        self.assertIn("Using data from file!", self.runner)
        self.assertIn("makea started!", self.runner)
        self.assertIn("maa_mem_size=2147483648", self.runner)

    def test_has_native_controls_and_two_bounded_arms(self):
        for arm in (
            "native16",
            "native4",
            "bounded4_cached",
            "bounded4_bypass",
        ):
            self.assertIn(arm, self.runner)

    def test_winner_geometry_is_finite(self):
        self.assertIn('--maa_physical_tile_elements="$physical"', self.runner)
        self.assertIn('--maa_num_tile_elements="$logical"', self.runner)
        self.assertIn(
            "--maa_virtual_descriptor_spool_read_credits=24", self.runner
        )
        self.assertIn(
            "--maa_virtual_index_filter_words_per_cycle=64", self.runner
        )
        self.assertIn("16384 16384 64 16384 0 0", self.runner)
        self.assertIn("4096 4096 16 4096 0 0", self.runner)
        self.assertGreaterEqual(self.runner.count("16384 4096 16 4096 1"), 2)

    def test_validation_fails_closed(self):
        self.assertIn("x_q5=88c0975669c7062d", self.runner)
        self.assertIn("result=PASS", self.runner)
        self.assertIn("$scans -gt 0", self.runner)
        self.assertIn("$external -gt 0", self.runner)
        self.assertIn("! -e $out/matrix.complete", self.runner)
        self.assertIn("trap 'exit 143' TERM", self.runner)


if __name__ == "__main__":
    unittest.main()
