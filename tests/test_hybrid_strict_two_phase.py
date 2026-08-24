import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class HybridStrictTwoPhaseContract(unittest.TestCase):
    def test_default_off_cli_is_forwarded(self) -> None:
        simobject = read("src/mem/MAA/MAA.py")
        options = read("configs/common/Options.py")
        config = read("configs/common/MAAConfig.py")
        self.assertIn(
            "virtual_strict_two_phase = Param.Bool(\n        False,", simobject
        )
        self.assertIn('"--maa_virtual_strict_two_phase"', options)
        self.assertIn('opts["virtual_strict_two_phase"]', config)

    def test_mode_rejects_replay_and_insufficient_geometry(self) -> None:
        source = read("src/mem/MAA/MAA.cc")
        for token in (
            "num_tile_elements != 16384",
            "physical_tile_elements != 4096",
            "num_offset_table_entries < num_tile_elements",
            "num_offset_table_epoch_entries < num_tile_elements",
            "virtual_index_partitions != 1",
            "virtual_index_range_passes",
            "virtual_index_descriptor_spool",
            "virtual_bounded_global_merge",
        ):
            self.assertIn(token, source)
        self.assertIn(
            "descriptor replay, range passes, and global merge are", source
        )

    def test_fill_closes_before_a_and_fails_closed_on_pressure(self) -> None:
        source = read("src/mem/MAA/IndirectAccess.cc")
        self.assertIn("event=strict_two_phase_admission_closed", source)
        self.assertIn("a_issues=0", source)
        self.assertIn("strict A issue", source)
        self.assertIn("state == Status::Fill", source)
        self.assertIn("strict A build opened before global Row/Offset", source)
        self.assertIn("strict two-phase physical RowTable", source)
        self.assertIn("strict two-phase cannot retain all", source)

    def test_terminal_invariants_and_coarse_ledger_exist(self) -> None:
        source = read("src/mem/MAA/IndirectAccess.cc")
        header = read("src/mem/MAA/MAA.hh")
        self.assertIn("A_FIRST_ISSUE=%lu", source)
        self.assertIn("ROW_OFFSET_LAST_INSERT=%lu", source)
        self.assertIn("event=strict_two_phase_summary", source)
        self.assertIn("replay=0 descriptor_backing=none", source)
        for field in (
            "b_fetch_lines=",
            "descriptor_inserts=",
            "a_issues=",
            "a_responses=",
            "backing_issues=",
            "backing_acks=",
            "pages_ready=",
            "consumer_event=",
            "exposed_stalls=",
            "fill_sim_ticks=",
            "issue_sim_ticks=",
            "retire_sim_ticks=",
        ):
            self.assertIn(field, source)
        for counter in (
            "IND_StrictTwoPhaseBFetchLines",
            "IND_StrictTwoPhaseDescriptors",
            "IND_StrictTwoPhaseAIssues",
            "IND_StrictTwoPhaseAResponses",
            "IND_StrictTwoPhaseBackingIssues",
            "IND_StrictTwoPhaseBackingAcks",
            "IND_StrictTwoPhasePagesReady",
            "IND_StrictTwoPhaseExposedStalls",
            "IND_StrictTwoPhaseFillCycles",
            "IND_StrictTwoPhaseIssueCycles",
            "IND_StrictTwoPhaseRetireCycles",
        ):
            self.assertIn(counter, header)

    def test_runner_is_one_binary_checkpoint_pair_and_rejects_regression(
        self,
    ) -> None:
        runner = read("experiments/scripts/run_hybrid_strict_two_phase_ab.sh")
        self.assertIn("--maa_virtual_strict_two_phase", runner)
        self.assertIn("DX100_SHARED_CHECKPOINT_DIR", runner)
        self.assertIn("strict_ticks > current_ticks", runner)
        self.assertIn("descriptor_spool_b_scans", runner)
        self.assertNotIn("native_16k", runner)
        self.assertNotIn("timeout ", runner)


if __name__ == "__main__":
    unittest.main()
