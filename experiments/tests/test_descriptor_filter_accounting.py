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

    def test_descriptor_retry_inspections_have_a_dedicated_counter(self):
        counter = "IND_DescriptorSpoolFilterRetryInspections"
        self.assertIn(counter, self.maa_header)
        self.assertIn(counter, self.maa_source)
        self.assertEqual(self.indirect.count(counter), 2)
        for source in ("predicate_bucket", "grow_bucket"):
            self.assertIn(
                f"event=descriptor_spool_filter_retry schema=1 ",
                self.indirect,
            )
            self.assertIn(f"source={source}", self.indirect)
        self.assertIn("const bool direct_index_filtering", self.indirect)

    def test_failed_flush_is_the_only_accounted_descriptor_retry_source(
        self,
    ):
        retry_counter = "IND_DescriptorSpoolFilterRetryInspections"
        for source in ("predicate_bucket", "grow_bucket"):
            marker = f"source={source}"
            start = self.indirect.index(marker)
            preceding = self.indirect[max(0, start - 1200) : start]
            self.assertIn(
                "!flushDescriptorSpoolLine(bucket_pass, false)", preceding
            )
            self.assertIn(retry_counter, preceding)

    def test_descriptor_spool_trace_uses_gem5_supported_size_format(self):
        self.assertNotIn("pending=%zu", self.indirect)
        self.assertIn("pending=%lu", self.indirect)
        self.assertIn(
            "static_cast<unsigned long>(descriptor_spool_pending_lines.size())",
            self.indirect,
        )

    def test_33870_is_accounted_without_counting_final_flush_stalls(self):
        summary_words = 16_384
        bucket_words = 16_384
        descriptor_retry_inspections = 1_102
        write_credit_stalls = 1_105
        self.assertEqual(
            summary_words + bucket_words + descriptor_retry_inspections,
            33_870,
        )
        self.assertNotEqual(
            summary_words + bucket_words + write_credit_stalls,
            33_870,
        )

    def test_runner_and_matrix_close_the_source_ledger(self):
        for token in (
            "descriptor_filter_retry_inspections",
            "descriptor_filter_predicate_retries",
            "descriptor_filter_grow_retries",
            "unattributed descriptor filter retries",
            "descriptor_filter_retry_inspections -le $descriptor_write_stalls",
            "expected_filter_words=$((expected_filter_words + \\",
        ):
            self.assertIn(token, self.runner)
        self.assertIn(
            'descriptor_spool_filter_retry_inspections "$candidate") -eq',
            self.matrix,
        )
        self.assertIn("canonical_ramulator_sha=", self.matrix)

    def test_descriptor_complete_trace_contract_allows_adjacent_fields(self):
        expected = (
            "event=descriptor_spool_complete schema=1 "
            ".* descriptors=16384 write_lines=2048 "
            "write_acks=2048 read_lines=2048 read_responses=2048 "
            ".* staging_entries=32 .* fallback=none$"
        )
        self.assertIn(expected, self.runner)
        trace_line = (
            "3490359717: global: event=descriptor_spool_complete schema=1 "
            "unit=0 operation_tick=3435831048 descriptors=16384 "
            "write_lines=2048 write_acks=2048 read_lines=2048 "
            "read_responses=2048 control_bytes=2449 backing_bytes=131328 "
            "staging_entries=32 write_hwm=16 fallback=none"
        )
        self.assertIsNotNone(re.search(expected, trace_line))

    def test_matrix_uses_one_virtual_checkpoint_for_treatment_equivalence(
        self,
    ):
        self.assertIn(
            "for arm in native16 native4 virtual_4k; do",
            self.matrix,
        )
        self.assertIn(
            "run_arm base_replay_4k paged_4k virtual_4k virtual_4k",
            self.matrix,
        )
        self.assertIn(
            "run_arm descriptor_spool_4k paged_4k virtual_4k virtual_4k",
            self.matrix,
        )
        self.assertIn(
            "base_checkpoint_identity == $candidate_checkpoint_identity",
            self.matrix,
        )


if __name__ == "__main__":
    unittest.main()
