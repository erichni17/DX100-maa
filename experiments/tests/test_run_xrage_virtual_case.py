#!/usr/bin/env python3
"""Contract tests for the representative XRAGE virtualization runner."""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_xrage_virtual_case.sh"


class XrageVirtualCaseContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = RUNNER.read_text(encoding="utf-8")

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(RUNNER)], check=True)

    def test_freezes_all_external_artifacts_and_resolves_ramulator(self):
        for name in (
            "gem5.opt",
            "xrage_verify",
            "xrage.json",
            "libramulator.so",
        ):
            self.assertIn(f'"$out/input/{name}"', self.source)
        self.assertIn('LD_LIBRARY_PATH="$library_path" ldd "$gem5"', self.source)
        self.assertIn('artifact_sha256.txt', self.source)
        self.assertIn('checkpoint_identity.sha256', self.source)
        self.assertIn('evidence_sha256.txt', self.source)

    def test_first_arm_is_exact_full_metadata_smoke(self):
        self.assertIn("run_xrage_direct_index_smoke.sh", self.source)
        self.assertIn("XRAGE_GUEST_ARM=direct4", self.source)
        self.assertIn("MAA_PHYSICAL_TILE_ELEMENTS=4096", self.source)
        self.assertIn("MAA_ROW_TABLE_ROWS_PER_SLICE=64", self.source)
        self.assertIn("MAA_NUM_OFFSET_TABLE_ENTRIES=16384", self.source)
        self.assertIn("MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=16384", self.source)

    def test_bounded_arm_reuses_neutral_checkpoint(self):
        self.assertIn("recover_xrage_checkpoint.sh", self.source)
        self.assertIn("XRAGE_ALLOW_PRE_MAA_RETARGET=1", self.source)
        self.assertIn("MAA_ROW_TABLE_ROWS_PER_SLICE=32", self.source)
        self.assertIn("MAA_NUM_OFFSET_TABLE_ENTRIES=4096", self.source)
        self.assertIn("MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=4096", self.source)
        self.assertIn("MAA_VIRTUAL_INDEX_PARTITIONS=4", self.source)
        self.assertIn("MAA_VIRTUAL_INDEX_FILTER_WORDS_PER_CYCLE=16", self.source)
        self.assertIn("checkpoint_retargeted=1", self.source)

    def test_comparison_requires_exact_hash_and_simticks(self):
        self.assertIn('bounded_hash == "$full_hash"', self.source)
        self.assertIn("roi_simTicks", self.source)
        self.assertIn("delta_vs_full_pct", self.source)
        self.assertNotIn("hostSeconds", self.source)


if __name__ == "__main__":
    unittest.main()
