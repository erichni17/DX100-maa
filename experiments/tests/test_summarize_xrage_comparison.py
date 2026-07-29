import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "summarize_xrage_comparison",
    SCRIPT_DIR / "summarize_xrage_comparison.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class SummarizeXrageComparisonTest(unittest.TestCase):
    def test_dirty_source_requires_matching_frozen_simulator(self):
        trusted = ("a" * 40, "b" * 64)
        MODULE.verify_source_provenance(
            "candidate", True, trusted[0], trusted[1], trusted
        )

        with self.assertRaisesRegex(SystemExit, "dirty source worktree"):
            MODULE.verify_source_provenance(
                "candidate", True, trusted[0], trusted[1], None
            )
        with self.assertRaisesRegex(SystemExit, "gem5 hash differs"):
            MODULE.verify_source_provenance(
                "candidate", True, trusted[0], "c" * 64, trusted
            )

    def test_sum_stats_uses_only_first_stats_block(self):
        stats = """\
---------- Begin Simulation Statistics ----------
system.maa.S0_STR_CyclesRequest 11 # first stream
system.maa.S2_STR_CyclesRequest 13 # second stream
system.maa.I0_IND_CyclesFill 23 # first indirect unit
system.maa.I1_IND_CyclesFill 29 # second indirect unit
system.mem_ctrls0.numReads::maa 17 # channel zero
system.mem_ctrls1.numReads::maa 19 # channel one
---------- End Simulation Statistics   ----------
---------- Begin Simulation Statistics ----------
system.maa.S0_STR_CyclesRequest 101 # post-ROI
system.mem_ctrls0.numReads::maa 103 # post-ROI
---------- End Simulation Statistics   ----------
"""
        self.assertEqual(
            MODULE.sum_first_block_stats(
                stats, MODULE.SUM_STAT_FIELDS["stream_request_cycles"]
            ),
            24,
        )
        self.assertEqual(
            MODULE.sum_first_block_stats(
                stats, MODULE.SUM_STAT_FIELDS["memory_controller_reads"]
            ),
            36,
        )
        self.assertEqual(
            MODULE.sum_first_block_stats(
                stats, MODULE.SUM_STAT_FIELDS["fill_cycles"]
            ),
            52,
        )

    def test_sum_stats_returns_zero_when_absent(self):
        self.assertEqual(
            MODULE.sum_first_block_stats(
                "---------- End Simulation Statistics ----------\n",
                MODULE.SUM_STAT_FIELDS["stream_instructions"],
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
