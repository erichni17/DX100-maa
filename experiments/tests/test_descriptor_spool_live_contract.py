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
        self.assertIn(
            "Descriptor spooling requires bounded translated-grow policy 3",
            maa,
        )
        self.assertIn("descriptor spool has no fallback", self.indirect)
        self.assertIn("fallback=none", self.indirect)

    def test_all_precise_control_capacities_are_finite(self) -> None:
        for token in (
            "MaxPasses = 4",
            "LineBytes = 64",
            "DescriptorBytes = 8",
            "DescriptorsPerLine",
            "MaxOutstandingWrites = 16",
            "MaxOutstandingReadLines = 4",
            "activeStagingDescriptorCapacity",
            "chargedControlBytes",
            "requiredBackingBytes",
            "reservedBackingBytes",
        ):
            self.assertIn(token, self.spool)
        self.assertNotIn("std::vector", self.spool)
        self.assertIn(
            "descriptor_spool_pending_lines.size() +\n"
            "               direct_index_ready_lines.size() <\n"
            "           BoundedDescriptorSpool::MaxOutstandingReadLines",
            self.indirect,
        )
        self.assertIn(
            "descriptor write map exceeded finite capacity", self.indirect
        )
        for token in (
            "read_scoreboard_bytes",
            "ready_line_bytes",
            "decoded_descriptor_bytes",
            "write_address_map_bytes",
        ):
            self.assertIn(token, self.indirect)

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
        self.assertIn("descriptor_spool_write_paddr_to_vaddr", self.header)
        self.assertIn("descriptor_spool_read_response", self.indirect)

    def test_phases_are_one_summary_one_bucket_one_descriptor_replay(
        self,
    ) -> None:
        for token in (
            "direct_index_summary_active",
            "descriptor_spool_bucket_active",
            "descriptor_spool_replay_active",
            "IND_BoundedBucketWords",
            "recordSelectedInspection",
            "recordConsumption",
            "finishBucketing",
            "beginReplay",
            "finishReplay",
        ):
            self.assertIn(token, self.indirect)
        self.assertIn("bounded_replay_words -eq 0", self.runner)
        self.assertIn("bounded_bucket_words -eq 16384", self.runner)
        self.assertIn("descriptor_write_bytes -eq 131072", self.runner)
        self.assertIn(
            "descriptor_read_bytes -eq $descriptor_write_bytes", self.runner
        )

    def test_descriptor_preserves_placement_and_logical_identity(self) -> None:
        self.assertRegex(
            self.spool,
            r"struct Descriptor\s*\{\s*uint16_t iteration[\s\S]*?"
            r"uint16_t sourcePage[\s\S]*?uint32_t value",
        )
        self.assertIn("descriptorIndexWordPaddr", self.indirect)
        self.assertIn("captureDescriptorIndexPage", self.indirect)
        self.assertIn("MaxDescriptorIndexPages = 17", self.header)
        self.assertIn("descriptor.iteration", self.indirect)
        self.assertIn(
            "const int logical_itr = descriptor_spool_replay_active",
            self.indirect,
        )
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
        ):
            self.assertIn(token, self.runner)

    def test_matrix_is_matched_and_has_four_isolated_arms(self) -> None:
        for arm in (
            "native16",
            "native4",
            "base_replay_4k",
            "descriptor_spool_4k",
        ):
            self.assertIn(arm, self.matrix)
        self.assertEqual(self.matrix.count('arm_pids+=("$!")'), 4)
        self.assertIn("MAA_VIRTUAL_INDEX_DESCRIPTOR_SPOOL=1", self.matrix)
        self.assertIn("matrix.complete", self.matrix)
        self.assertIn("physical_records", self.matrix)
        self.assertIn("descriptor_spool_write_bytes", self.matrix)
        self.assertIn("naive_16byte_total_bytes", self.matrix)
        self.assertIn('> "$out/traffic.tsv"', self.matrix)


if __name__ == "__main__":
    unittest.main()
