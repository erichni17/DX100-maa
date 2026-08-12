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
        self.assertIn("analyze_cg_bounded_descriptor_matrix.py", self.runner)
        self.assertIn('"$out" --write', self.runner)
        self.assertIn(
            '"$out/input/analyze_cg_bounded_descriptor_matrix.py"',
            self.runner,
        )
        self.assertIn("! -e $out/$completion_marker", self.runner)
        self.assertIn("trap - EXIT", self.runner)
        self.assertIn('exit "$rc"', self.runner)
        self.assertIn("trap 'exit 143' TERM", self.runner)

    def test_bounded_precheck_crosses_prior_failure_without_controls(self):
        self.assertIn("bounded-precheck", self.runner)
        self.assertIn("checkpoint_tick + 2000000000", self.runner)
        self.assertIn('--abs-max-tick "$max_tick"', self.runner)
        self.assertIn("simulate\\(\\) limit reached", self.runner)
        self.assertIn("precheck.complete", self.runner)
        self.assertIn("descriptor_scans", self.runner)

    def test_bounded_precheck_can_reuse_a_fingerprinted_checkpoint(self):
        for token in (
            "CG_PRECHECK_CHECKPOINT_SOURCE",
            "precheck-checkpoint-source.txt",
            "precheck-checkpoint-source.files.sha256",
            "precheck-checkpoint-source.identity.sha256",
            'checkpoint_dirs=("$checkpoint"/cpt.[0-9]*)',
            'run_arm bounded4_cached "$bounded" "$checkpoint"',
        ):
            self.assertIn(token, self.runner)
        self.assertIn(
            "CG_PRECHECK_CHECKPOINT_SOURCE requires bounded-precheck mode",
            self.runner,
        )


if __name__ == "__main__":
    unittest.main()
