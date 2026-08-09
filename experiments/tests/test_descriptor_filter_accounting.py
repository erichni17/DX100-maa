import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DescriptorFilterAccountingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.maa_header = (ROOT / "src/mem/MAA/MAA.hh").read_text()
        cls.maa_source = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()
        cls.matrix = (
            ROOT / "experiments/scripts/run_true_4k_descriptor_spool_matrix.sh"
        ).read_text()

    def test_retry_and_final_flush_have_separate_counters(self) -> None:
        for counter in (
            "IND_DescriptorSpoolFilterRetryInspections",
            "IND_DescriptorSpoolFinalFlushStalls",
        ):
            self.assertIn(counter, self.maa_header)
            self.assertIn(counter, self.maa_source)
        self.assertEqual(
            self.indirect.count(".IND_DescriptorSpoolFilterRetryInspections["),
            2,
        )
        self.assertEqual(
            self.indirect.count(".IND_DescriptorSpoolFinalFlushStalls["),
            1,
        )
        for source in ("predicate_bucket", "grow_bucket"):
            self.assertIn(f"source={source}", self.indirect)
        self.assertIn(
            "event=descriptor_spool_final_flush_stall", self.indirect
        )
        self.assertIn("b_reinspection=0", self.indirect)
        self.assertIn("const bool direct_index_filtering", self.indirect)

    def test_failed_full_line_flush_is_the_only_retry_source(self) -> None:
        retry_counter = "descriptor_spool_filter_retry_inspections++"
        for source in ("predicate_bucket", "grow_bucket"):
            marker = f"source={source}"
            start = self.indirect.index(marker)
            preceding = self.indirect[max(0, start - 1500) : start]
            self.assertIn(
                "!flushDescriptorSpoolLine(bucket_pass, false)", preceding
            )
            self.assertIn(retry_counter, preceding)

    def test_terminal_source_ledger_is_exact(self) -> None:
        self.assertIn(
            "descriptor_spool_bucket_commits +\n"
            "                                 "
            "descriptor_spool_filter_retry_inspections",
            self.indirect,
        )
        self.assertIn("b_scans=2", self.indirect)
        self.assertIn("unique_inspections=%lu", self.indirect)
        self.assertIn("retry_inspections=%lu", self.indirect)
        self.assertIn("final_flush_stalls=%lu", self.indirect)

    def test_runner_closes_resident_first_accounting(self) -> None:
        for token in (
            "descriptor_filter_retry_inspections",
            "descriptor_final_flush_stalls",
            "descriptor_filter_predicate_retries",
            "descriptor_filter_grow_retries",
            "unattributed descriptor filter retries",
            "descriptor_filter_retry_inspections -le $descriptor_write_stalls",
            "descriptor_b_scans -eq 2",
            "descriptor_resident_populations -eq 1",
            "descriptor_resident_descriptors -eq 4096",
            "descriptor_external_descriptors -eq 12288",
            "descriptor_external_segments -eq 3",
            "descriptor_write_bytes -eq 73728",
            "expected_filter_words=$((expected_filter_words + \\",
        ):
            self.assertIn(token, self.runner)
        self.assertIn("canonical_ramulator_sha=", self.matrix)

    def test_complete_trace_regex_closes_exact_resident_line(self) -> None:
        expected = (
            "event=descriptor_spool_complete schema=2 "
            ".* b_scans=2 descriptors=16384 resident_pass=0 "
            "resident_descriptors=4096 external_descriptors=12288 "
            "external_segments=3 descriptor_bytes=6 payload_bytes=73728 "
            "write_lines=1152 write_acks=1152 read_lines=1152 "
            "read_responses=1152 .* prefetch_occupancy=0 .* "
            "wasted_lines=0 .* fallback=none$"
        )
        self.assertIn(expected, self.runner)
        trace_line = (
            "1: global: event=descriptor_spool_complete schema=2 unit=0 "
            "operation_tick=1 b_scans=2 descriptors=16384 resident_pass=0 "
            "resident_descriptors=4096 external_descriptors=12288 "
            "external_segments=3 descriptor_bytes=6 payload_bytes=73728 "
            "write_lines=1152 write_acks=1152 read_lines=1152 "
            "read_responses=1152 control_bytes=2000 backing_bytes=73728 "
            "staging_bytes=207 write_hwm=16 read_hwm=4 "
            "unique_inspections=16384 retry_inspections=100 "
            "final_flush_stalls=2 read_ahead=0 overlap_opportunities=0 "
            "next_pass_read_issues=0 next_pass_read_responses=0 "
            "useful_prefetched_lines=0 demand_waits_avoided=0 "
            "prefetch_occupancy=0 prefetch_occupancy_hwm=0 "
            "prefetch_occupancy_line_cycles=0 wasted_lines=0 "
            "boundary_wait_events=1 boundary_wait_cycles=10 "
            "within_pass_wait_events=2 within_pass_wait_cycles=20 "
            "active_limit=4096 "
            "identity_check=trace_side fallback=none"
        )
        self.assertIsNotNone(re.search(expected, trace_line))

    def test_matrix_shares_virtual_treatment_checkpoint(self) -> None:
        self.assertIn(
            "for arm in native16 native4 virtual_4k; do",
            self.matrix,
        )
        self.assertIn("virtual_4k virtual_4k", self.matrix)
        self.assertIn(
            "base_checkpoint_identity == $candidate_checkpoint_identity",
            self.matrix,
        )


if __name__ == "__main__":
    unittest.main()
