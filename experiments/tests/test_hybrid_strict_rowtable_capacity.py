import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_hybrid_strict_rowtable_capacity.sh"


class HybridStrictRowTableCapacityContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = RUNNER.read_text(encoding="utf-8")

    def test_one_b_scan_uses_logical_words_not_physical_address_order(
        self,
    ) -> None:
        source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text(
            encoding="utf-8"
        )
        header = (ROOT / "src/mem/MAA/IndirectAccess.hh").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("strict_last_b_line", source)
        self.assertNotIn("strict_last_b_line", header)
        self.assertIn(
            "strict_b_words != static_cast<uint64_t>(my_max)", source
        )
        self.assertIn("attribution_row_insert_successes !=", source)
        self.assertIn("static_cast<uint64_t>(my_max)", source)
        self.assertIn("strict B fetch issued after admission closure", source)

    def test_reuses_same_commit_binary_checkpoint_and_row64_arm(self) -> None:
        for token in (
            "expected_api=963940eeaface13cb53f73b565a88b299",
            'campaign_commit=$(git -C "$root" rev-parse HEAD)',
            "gem5_source_commit=$(sed -n",
            '[[ $gem5_source_commit == "$campaign_commit" ]]',
            "base_current/result.tsv",
            "DX100_SHARED_CHECKPOINT_DIR=$checkpoint",
            "base_current/shared_checkpoint_identity.sha256",
            "base_current/source_snapshot/IndirectAccess.cc",
        ):
            self.assertIn(token, self.runner)
        self.assertNotIn("--max-checkpoints", self.runner)
        self.assertNotIn("expected_gem5=", self.runner)

    def test_expands_only_rows_and_keeps_forbidden_modes_off(self) -> None:
        for token in (
            '"MAA_ROW_TABLE_SLICES=16"',
            '"MAA_ROW_TABLE_ROWS_PER_SLICE=128"',
            '"MAA_ROW_TABLE_ENTRIES_PER_SUBSLICE_ROW=8"',
            '"MAA_OFFSET_TABLE_ENTRIES=16384"',
            '"MAA_OFFSET_TABLE_EPOCH_ENTRIES=16384"',
            '"MAA_VIRTUAL_INDEX_PARTITIONS=1"',
            '"MAA_VIRTUAL_INDEX_RANGE_PASSES=0"',
            '"MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=0"',
            '"MAA_VIRTUAL_DESCRIPTOR_SPOOL_READ_AHEAD=0"',
            '"MAA_VIRTUAL_BOUNDED_GLOBAL_MERGE=0"',
        ):
            self.assertIn(token, self.runner)
        self.assertIn("--maa_virtual_strict_two_phase", self.runner)

    def test_three_arm_analysis_separates_capacity_and_scheduling(
        self,
    ) -> None:
        for token in (
            '"current_row64"',
            '"current_row128"',
            '"strict_row128"',
            '"capacity_current_row64_to_row128"',
            '"scheduling_current_to_strict_at_row128"',
            "capacity_speedup = ticks64 / ticks_current",
            "scheduling_speedup = ticks_current / ticks_strict",
            "REJECT_CAPACITY_REGRESSION",
            "REJECT_STRICT_REGRESSION",
        ):
            self.assertIn(token, self.runner)

    def test_strict_gate_closes_required_ledgers_and_forbids_replay(
        self,
    ) -> None:
        for token in (
            '"a_first_issue_tick"',
            '"row_offset_last_insert_tick"',
            "A_FIRST_ISSUE < ROW_OFFSET_LAST_INSERT",
            '"b_words", "16384"',
            '"descriptor_inserts", "16384"',
            '"consumer_event", "hybrid_consumer_macro"',
            '"event=hybrid_consumer_macro "',
            "strict A issue/response ledger did not close",
            "strict backing issue/ACK ledger did not close",
            '"row_table_full_events"',
            '"offset_epoch_drains"',
            '"bounded_replay_line_reads"',
            '"descriptor_spool_b_scans"',
            '"bounded_global_descriptor_records"',
            '"source_issue_sha256"',
        ):
            self.assertIn(token, self.runner)

    def test_terminal_gate_has_no_native_rerun_or_timeout(self) -> None:
        for token in (
            "because m5_exit instruction encountered",
            "ROI Ended",
            "VIRTUAL_TILE_CONSUMER_RESULT",
            "virtual_tile_consumer_case.pass",
            "stats.stat().st_size == 0",
        ):
            self.assertIn(token, self.runner)
        self.assertNotIn("native_16k", self.runner)
        self.assertNotIn("timeout ", self.runner)

    def test_cost_ledger_distinguishes_active_and_unused_cpp_organizations(
        self,
    ) -> None:
        for token in (
            '"active_fixed_16_slice"',
            '"all_four_cpp_organizations_2_4_8_16"',
            "active_delta_slots = 8192",
            "allocated_delta_slots = 32768",
            "active_cpp_bytes = active_delta_slots * 18",
            "allocated_cpp_bytes =",
            '"packed_delta_bits"',
            '"semantic_cpp_delta_bits"',
        ):
            self.assertIn(token, self.runner)


if __name__ == "__main__":
    unittest.main()
