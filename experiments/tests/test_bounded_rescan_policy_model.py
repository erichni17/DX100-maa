#!/usr/bin/env python3
"""Adversarial tests for the standalone bounded-rescan trace model."""

import importlib.util
import inspect
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "experiments/analysis/bounded_rescan_policy_model.py"
SPEC = importlib.util.spec_from_file_location(
    "bounded_rescan_policy_model", MODEL_PATH
)
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)


def index_for_row(row, line_offset=0):
    """Return an A index for slice zero in one decoded row."""

    line = row * 4096 + line_offset
    return line * (MODEL.CACHE_LINE_BYTES // MODEL.A_ELEMENT_BYTES)


class AdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = MODEL.load_frozen_corpus()

    def test_all_frozen_sources_are_admitted_once(self):
        self.assertEqual(len(self.sources), 15)
        self.assertEqual(
            [source.source_id for source in self.sources[1:]],
            list(MODEL.FLAG_EXPECTED_IDS),
        )
        self.assertEqual(self.sources[0].source_count, 20_000)
        self.assertEqual(
            sum(source.source_count for source in self.sources), 658_460
        )
        for source in self.sources:
            self.assertTrue(source.path.is_absolute())
            self.assertEqual(MODEL.sha256_file(source.path), source.sha256)
            self.assertEqual(len(source.pattern), source.source_count)

    def test_incomplete_and_duplicate_source_sets_are_rejected(self):
        records = [
            (source.source_id, source.path, source.sha256, source.source_count)
            for source in self.sources[1:]
        ]
        with self.assertRaisesRegex(MODEL.AdmissionError, "expected 14"):
            MODEL.validate_source_records(records[:-1], 14)
        duplicate = records[:-1] + [records[0]]
        with self.assertRaisesRegex(MODEL.AdmissionError, "duplicate"):
            MODEL.validate_source_records(duplicate, 14)

    def test_xrage_base_is_grounded_by_exact_frozen_address_trace(self):
        self.assertEqual(MODEL.XRAGE_BASE_LINE, 65_025)
        self.assertEqual(
            MODEL.sha256_file(MODEL.XRAGE_GROUND_TRACE_PATH),
            MODEL.XRAGE_GROUND_TRACE_SHA256,
        )
        MODEL._validate_xrage_address_grounding(self.sources[0].pattern)


class CandidateCorrectnessTests(unittest.TestCase):
    def test_single_partition_skew_drains_without_loss(self):
        pattern = [index_for_row(0)] * MODEL.MAX_LOGICAL_ELEMENTS
        result = MODEL.CandidatePolicy().run(pattern)
        self.assertEqual(result.partition_counts, (16_384, 0, 0, 0))
        self.assertEqual(result.drains, 4)
        self.assertEqual(result.capacity_drains, 3)
        self.assertEqual(result.a_requests, 4)
        self.assertEqual(result.destinations, len(pattern))
        self.assertLessEqual(
            result.max_active_descriptors, MODEL.ACTIVE_DESCRIPTOR_CAPACITY
        )

    def test_duplicates_map_every_destination_exactly_once(self):
        pattern = [9, 9, 17, 9, 17, 33, 33, 33, 9]
        result = MODEL.CandidatePolicy(operation_generation=3).run(pattern)
        self.assertEqual(result.destinations, len(pattern))
        self.assertEqual(result.unique_lines, 3)
        self.assertEqual(result.a_requests, 3)
        observer = MODEL.StructuralObserver(pattern, "mapping_probe")
        with self.assertRaisesRegex(MODEL.ProtocolError, "wrong destination"):
            observer.retire(0, 10)

    def test_empty_and_full_partitions(self):
        pattern = []
        for partition in range(4):
            pattern.extend(
                [index_for_row(partition)] * MODEL.ACTIVE_DESCRIPTOR_CAPACITY
            )
        result = MODEL.CandidatePolicy().run(pattern)
        self.assertEqual(result.partition_counts, (4096, 4096, 4096, 4096))
        self.assertEqual(result.drains, 4)

        empty = MODEL.CandidatePolicy().run([])
        self.assertEqual(empty.partition_counts, (0, 0, 0, 0))
        self.assertEqual(empty.drains, 0)
        self.assertEqual(empty.b_scan_words, 0)

    def test_nondivisible_n_has_four_complete_scans(self):
        pattern = [index_for_row(0)] * 5001
        result = MODEL.CandidatePolicy().run(pattern)
        self.assertEqual(result.destinations, 5001)
        self.assertEqual(result.drains, 2)
        self.assertEqual(result.b_scan_words, 4 * 5001)
        self.assertEqual(result.b_scan_bytes, 4 * 5001 * MODEL.B_WORD_BYTES)

    def test_row_table_skew_causes_early_drain(self):
        # All rows use slice zero and partition zero; the 65th distinct row
        # cannot enter a 64-row slice until the first subepoch drains.
        pattern = [index_for_row(4 * row) for row in range(65)]
        result = MODEL.CandidatePolicy().run(pattern)
        self.assertEqual(result.row_table_drains, 1)
        self.assertEqual(result.drains, 2)
        self.assertEqual(result.max_rows_in_slice, MODEL.ROWS_PER_SLICE)

    def test_finite_response_and_ack_ownership_rejects_stale_tokens(self):
        table = MODEL.FiniteOwnerTable(capacity=2, operation_generation=7)
        first = table.allocate(11, "response-a")
        second = table.allocate(12, "response-b")
        with self.assertRaisesRegex(MODEL.ProtocolError, "full"):
            table.allocate(13, "overflow")
        self.assertEqual(table.complete(first), "response-a")
        with self.assertRaisesRegex(MODEL.ProtocolError, "unowned"):
            table.complete(first)
        with self.assertRaisesRegex(MODEL.ProtocolError, "live owners"):
            table.advance_subepoch()
        self.assertEqual(table.complete(second), "response-b")
        table.advance_subepoch()
        with self.assertRaisesRegex(MODEL.ProtocolError, "stale"):
            table.complete(first)
        forged = replace(first, operation_generation=8)
        with self.assertRaisesRegex(MODEL.ProtocolError, "stale"):
            table.complete(forged)

    def test_candidate_has_no_hidden_n_sized_policy_state(self):
        policy = MODEL.CandidatePolicy()
        result = policy.run([index_for_row(0)] * MODEL.MAX_LOGICAL_ELEMENTS)
        sizes = policy.bounded_container_sizes()
        self.assertEqual(sizes["active"], 0)
        self.assertEqual(sizes["active_lines"], 0)
        self.assertEqual(sizes["row_sets"], 0)
        self.assertEqual(sizes["row_set_count"], MODEL.ROW_TABLE_SLICES)
        self.assertEqual(sizes["response_entries"], MODEL.RESPONSE_OWNER_SLOTS)
        self.assertEqual(sizes["ack_entries"], MODEL.ACK_OWNER_SLOTS)
        self.assertLessEqual(
            result.max_active_descriptors, MODEL.ACTIVE_DESCRIPTOR_CAPACITY
        )
        source = inspect.getsource(MODEL.CandidatePolicy)
        for forbidden in (
            "completion_bitmap",
            "oracle_issue_serial",
            "sorted(pattern)",
            "list(pattern)",
        ):
            self.assertNotIn(forbidden, source)

    def test_deterministic_duplicate_replay(self):
        pattern = [index_for_row((i * 7) % 20, i % 8) for i in range(5003)]
        first = MODEL.CandidatePolicy(9, base_line=64).run(pattern)
        second = MODEL.CandidatePolicy(9, base_line=64).run(pattern)
        self.assertEqual(first, second)


class ReferenceAndAccountingTests(unittest.TestCase):
    def test_reference_schedulers_do_not_call_candidate(self):
        original = MODEL.CandidatePolicy.run

        def forbidden(*_args, **_kwargs):
            raise AssertionError("reference called candidate")

        MODEL.CandidatePolicy.run = forbidden
        try:
            pattern = [3, 11, 3, 19, 27]
            native16 = MODEL.run_native16(pattern)
            native4 = MODEL.run_native4k_x4(pattern)
        finally:
            MODEL.CandidatePolicy.run = original
        self.assertEqual(native16.destinations, len(pattern))
        self.assertEqual(native4.destinations, len(pattern))

    def test_native16_and_native4k_have_expected_epoch_boundary(self):
        # One repeated line is coalesced once by native16 and once in each 4K
        # epoch by native4K x4.
        pattern = [8] * MODEL.MAX_LOGICAL_ELEMENTS
        native16 = MODEL.run_native16(pattern)
        native4 = MODEL.run_native4k_x4(pattern)
        self.assertEqual(native16.a_requests, 1)
        self.assertEqual(native16.drains, 1)
        self.assertEqual(native4.a_requests, 4)
        self.assertEqual(native4.drains, 4)

    def test_metadata_ledger_accounts_every_required_state_family(self):
        ledger = MODEL.metadata_ledger()
        self.assertEqual(ledger["active_offset_descriptors"]["count"], 4096)
        self.assertEqual(ledger["row_table_entries"]["count"], 2048)
        self.assertEqual(ledger["response_identity_entries"]["count"], 128)
        self.assertEqual(ledger["ack_identity_entries"]["count"], 64)
        self.assertIn("b_word_latch", ledger)
        self.assertIn("finite_control", ledger)
        component_bits = sum(
            row["bits"] for name, row in ledger.items() if name != "total"
        )
        self.assertEqual(ledger["total"]["bits"], component_bits)
        self.assertEqual(ledger["total"]["packed_bytes"], 51_693)

    def test_model_is_standalone_from_rejected_professor_model(self):
        source = MODEL_PATH.read_text(encoding="utf-8")
        self.assertNotIn("professor_bounded_reorder_policy_model", source)
        self.assertNotIn("simTicks", source)
        self.assertNotIn("speedup", source)


if __name__ == "__main__":
    unittest.main()
