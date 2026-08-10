import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DescriptorSpoolLiveContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spool = (
            ROOT / "src/mem/MAA/BoundedDescriptorSpool.hh"
        ).read_text()
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.header = (ROOT / "src/mem/MAA/IndirectAccess.hh").read_text()
        cls.range_tracker = (
            ROOT / "src/mem/MAA/BoundedRangePass.hh"
        ).read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()
        cls.matrix = (
            ROOT / "experiments/scripts/run_true_4k_descriptor_spool_matrix.sh"
        ).read_text()

    def test_treatment_is_opt_in_and_fails_closed(self) -> None:
        param = (ROOT / "src/mem/MAA/MAA.py").read_text()
        self.assertRegex(
            param,
            r"virtual_index_descriptor_spool\s*=\s*Param\.Bool\(\s*False,",
        )
        maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        self.assertRegex(
            maa,
            r"Descriptor spooling requires bounded translated-grow policy "
            r'"\s*"3',
        )
        self.assertIn("descriptor spool has no fallback", self.indirect)
        self.assertIn("fallback=none", self.indirect)

    def test_all_precise_control_capacities_are_finite(self) -> None:
        for token in (
            "MaxPasses = 4",
            "MaxExternalPasses = 3",
            "LineBytes = 64",
            "DescriptorBits = 46",
            "DescriptorBytes = 6",
            "MaxCarryBytes = 5",
            "MaxOutstandingWrites = 16",
            "DefaultOutstandingReadLines = 4",
            "MaxOutstandingReadLines = 32",
            "readCredits",
            "activeStagingBytes",
            "chargedControlBytes",
            "requiredBackingBytes",
            "reservedBackingBytes",
            "residentPass",
            "externalSegments",
        ):
            self.assertIn(token, self.spool)
        self.assertNotIn("std::vector", self.spool)
        self.assertIn("descriptor_spool_read_slots", self.header)
        self.assertIn("descriptor_spool_write_slots", self.header)
        self.assertNotIn("descriptor_spool_pending_lines", self.header)
        self.assertNotIn("descriptor_spool_write_paddr_to_vaddr", self.header)
        for token in (
            "read_scoreboard_bytes",
            "current_descriptor_bytes",
            "write_scoreboard_bytes",
        ):
            self.assertIn(token, self.indirect)

    def test_functional_mechanism_has_no_operation_sized_checker_state(
        self,
    ) -> None:
        # The legacy sets remain functional for native/non-spool operations,
        # while the resident-first treatment explicitly suppresses updates.
        self.assertIn("std::set<Addr> my_unique_WORD_addrs", self.header)
        self.assertIn("if (!descriptor_spool_operation)", self.indirect)
        self.assertIn("legacy_unique_sets=suppressed", self.indirect)
        self.assertNotIn("std::vector<uint64_t> admitted", self.range_tracker)
        self.assertNotIn("std::vector<uint64_t> retired", self.range_tracker)
        self.assertIn("identity_check=trace_side", self.indirect)

    def test_non_spool_retains_exact_legacy_unique_behavior(self) -> None:
        for token in (
            "my_unique_WORD_addrs.insert(vaddr)",
            "my_unique_CL_addrs.insert(block_paddr)",
            "my_unique_ROW_addrs.insert(",
            "my_unique_WORD_addrs.size() >",
            "my_words_per_cl * my_unique_CL_addrs.size()",
            "setRowTableConfig(my_base_addr, my_unique_CL_addrs.size()",
            "IND_NumUniqueWordsInserted",
            "IND_NumUniqueCacheLineInserted",
            "IND_NumUniqueRowsInserted",
        ):
            self.assertIn(token, self.indirect)
        self.assertNotIn(
            "setRowTableConfig(\n                my_base_addr, "
            "static_cast<int>(my_first_cache_lines)",
            self.indirect,
        )

    def test_descriptor_payload_uses_timed_backing_requests(self) -> None:
        for token in (
            "createDescriptorSpoolWritePacket",
            "createDescriptorSpoolReadPacket",
            "MemCmd::WriteReq",
            "MemCmd::ReadReq",
            "req->setRegion(my_backing_addr_range_id)",
            "IND_DescriptorSpoolWriteBytes",
            "IND_DescriptorSpoolReadBytes",
            "IND_DescriptorSpoolWriteAcks",
        ):
            self.assertIn(token, self.indirect)
        self.assertGreaterEqual(
            self.indirect.count("maa->getClockEdge(Cycles(0)), true"),
            2,
        )
        self.assertIn("descriptor_spool_read_response", self.indirect)

    def test_phases_are_two_b_scans_one_resident_and_three_replays(
        self,
    ) -> None:
        for token in (
            "direct_index_summary_active",
            "descriptor_spool_bucket_active",
            "descriptor_spool_replay_active",
            "IND_BoundedBucketWords",
            "recordSelectedInspection",
            "recordConsumption",
            "recordResidentClassification",
            "finishBucketing",
            "beginReplay",
            "finishReplay",
        ):
            self.assertIn(token, self.indirect)

    def test_descriptor_preserves_placement_and_logical_identity(self) -> None:
        self.assertRegex(
            self.spool,
            r"struct Descriptor\s*\{\s*uint16_t iteration[\s\S]*?"
            r"uint32_t value",
        )
        self.assertIn("descriptorIndexWordPaddr", self.indirect)
        self.assertIn("captureDescriptorIndexPage", self.indirect)
        self.assertIn("MaxDescriptorIndexPages = 17", self.header)
        self.assertIn("descriptor.iteration", self.indirect)
        self.assertNotIn("sourcePage", self.spool)
        self.assertIn("BoundedDescriptorSpool::unpack", self.indirect)
        self.assertIn("trackVirtualIteration(logical_itr", self.indirect)
        self.assertIn("predicate_key", self.indirect)
        self.assertIn("predicate descriptor stage", self.indirect)
        self.assertIn("predicate admission", self.indirect)
        self.assertIn("predicate retirement", self.indirect)

    def test_runner_requires_terminal_exact_and_bounded_evidence(self) -> None:
        self.assertIn(
            "baseline_commit=6e84c2c4a4c9b008f0efb78314c7ac1b7f828b55",
            self.runner,
        )
        for token in (
            "m5_exit instruction encountered",
            "physical_records -eq 16384",
            "range_complete_count -eq 1",
            "descriptor_complete_count -eq $expected_descriptor_complete_count",
            "descriptor_control_bytes -le 4096",
            "resolved_offset_entries -le 4096",
            "row_slices * row_rows * row_entries",
            "fill_sim_ticks",
            "request_sim_ticks",
            "dram_activates",
            "descriptor_spool_backing_bytes",
            "descriptor_b_scans -eq 2",
            "descriptor_resident_descriptors -eq 4096",
            "descriptor_external_segments -eq 3",
            "descriptor_write_bytes -eq 73728",
            "descriptor_spool_unclassified_write_stalls",
            "expected_descriptor_discards=16384",
        ):
            self.assertIn(token, self.runner)

    def test_matrix_is_matched_and_has_four_isolated_arms(self) -> None:
        for arm in (
            "native16",
            "native4",
            "ab_spool_reference_4k",
            "resident_first_4k",
        ):
            self.assertIn(arm, self.matrix)
        self.assertEqual(self.matrix.count('arm_pids+=("$!")'), 4)
        self.assertIn("MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=1", self.matrix)
        self.assertIn("matrix.complete", self.matrix)
        self.assertIn("physical_records", self.matrix)
        self.assertIn("descriptor_spool_write_bytes", self.matrix)
        self.assertIn("accepted_ab_commit=59ad3fbb", self.matrix)
        self.assertIn(
            "candidate_total_bytes -lt $reference_total_bytes", self.matrix
        )
        self.assertIn("provenance.tsv", self.matrix)
        self.assertIn("canonical_ab_gem5_sha=", self.matrix)
        self.assertIn('> "$out/traffic.tsv"', self.matrix)


if __name__ == "__main__":
    unittest.main()
