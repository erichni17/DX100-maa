#!/usr/bin/env python3

import ast
import inspect
import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from bounded_row_model import (
    ACTIVE_ELEMENTS,
    LINE_SLOTS,
    NUM_BANKS,
    NUM_SLICES,
    RESPONSE_SLOTS,
    RESPONSE_WORD_POOL,
    ROW_SLOTS,
    ApertureGeometry,
    FiniteTables,
    Model,
    PhysicalRecord,
    model_report,
    storage_ledger,
)
from extract_grounded_trace import extract

GEOMETRY = ApertureGeometry.synthetic_full_ddr4()


def record(
    itr: int,
    *,
    index: int | None = None,
    slice_id: int = 0,
    grow: int = 0,
    line: int | None = None,
    wid: int = 0,
    b_base: int = 0x100004,
) -> PhysicalRecord:
    bankgroup, bank = divmod(slice_id, NUM_BANKS)
    if line is None:
        line = itr
    return PhysicalRecord(
        itr=itr,
        index=itr if index is None else index,
        b_paddr=b_base + itr * 4,
        a_line_paddr=0x400000 + line * 64,
        channel=0,
        rank=0,
        bankgroup=bankgroup,
        bank=bank,
        row=grow,
        column=line % 1024,
        wid=wid,
    )


def exact_capacity_records(
    count: int, grow_base: int = 0
) -> list[PhysicalRecord]:
    records = []
    for itr in range(count):
        line_id = itr // 16
        records.append(
            record(
                itr,
                slice_id=line_id % NUM_SLICES,
                grow=grow_base + line_id // NUM_SLICES,
                line=line_id,
                wid=itr % 8,
            )
        )
    return records


def full_line_capacity_records() -> list[PhysicalRecord]:
    """Fill every fixed Offset, row, and line slot exactly once."""
    records = []
    for itr in range(ACTIVE_ELEMENTS):
        slice_id = itr % NUM_SLICES
        local_line = itr // NUM_SLICES
        records.append(
            record(
                itr,
                slice_id=slice_id,
                grow=local_line // 8,
                line=itr,
                wid=itr % 8,
            )
        )
    return records


class FiniteGeometryTest(unittest.TestCase):
    def test_exact_4096_offset_boundary_and_one_past(self) -> None:
        exact = Model(logical_elements=4096, source_elements=8192).run(
            exact_capacity_records(4096), GEOMETRY
        )
        self.assertEqual(exact.peak_offsets, ACTIVE_ELEMENTS)
        self.assertEqual(exact.capacity_drains, 0)
        self.assertEqual(exact.epochs, 1)
        self.assertTrue(exact.geometry_bound_respected)

        one_past = Model(logical_elements=4097, source_elements=8192).run(
            exact_capacity_records(4097), GEOMETRY
        )
        self.assertEqual(one_past.peak_offsets, ACTIVE_ELEMENTS)
        self.assertEqual(one_past.drain_reasons["offset_limit"], 1)
        self.assertEqual(one_past.epochs, 2)
        self.assertEqual(one_past.placements, 4097)

    def test_4096_distinct_rows_drain_at_512_row_slots(self) -> None:
        records = [
            record(
                itr,
                slice_id=itr % NUM_SLICES,
                grow=itr // NUM_SLICES,
                line=itr,
            )
            for itr in range(4096)
        ]
        result = Model(logical_elements=4096, source_elements=8192).run(
            records, GEOMETRY
        )
        self.assertEqual(result.peak_row_slots, ROW_SLOTS)
        self.assertEqual(result.peak_line_slots, ROW_SLOTS)
        self.assertEqual(result.drain_reasons["row_slot_limit"], 7)
        self.assertEqual(result.epochs, 8)
        self.assertTrue(result.geometry_bound_respected)

    def test_more_than_eight_lines_rolls_to_bounded_row_slot(self) -> None:
        records = [record(itr, grow=11, line=itr) for itr in range(9)]
        result = Model(logical_elements=9, source_elements=32).run(
            records, GEOMETRY
        )
        self.assertEqual(result.peak_row_slots, 2)
        self.assertEqual(result.peak_line_slots, 9)
        self.assertEqual(result.line_slot_rollovers, 1)
        self.assertEqual(result.capacity_drains, 0)

    def test_one_slice_row_slot_exhaustion_drains(self) -> None:
        records = [record(itr, grow=19, line=itr) for itr in range(257)]
        result = Model(logical_elements=257, source_elements=512).run(
            records, GEOMETRY
        )
        self.assertEqual(result.peak_row_slots, 32)
        self.assertEqual(result.peak_line_slots, 256)
        self.assertEqual(result.drain_reasons["row_slot_limit"], 1)
        self.assertEqual(result.epochs, 2)

    def test_one_line_fanout_drains_at_response_word_descriptor(self) -> None:
        records = [
            record(itr, index=13, grow=23, line=7) for itr in range(4096)
        ]
        result = Model(logical_elements=4096, source_elements=8192).run(
            records, GEOMETRY
        )
        self.assertEqual(result.peak_reserved_words, RESPONSE_WORD_POOL)
        self.assertEqual(result.drain_reasons["line_word_limit"], 8)
        self.assertEqual(result.epochs, 9)
        self.assertEqual(result.a_line_requests, 9)
        self.assertEqual(result.placements, 4096)
        self.assertEqual(result.duplicate_placements, 0)

    def test_partition_skew_is_bounded_and_charged(self) -> None:
        records = exact_capacity_records(4096, grow_base=50_000)
        result = Model(logical_elements=4096, source_elements=8192).run(
            records, GEOMETRY
        )
        self.assertEqual(result.selector_words, 4096 * 4)
        self.assertEqual(result.selector_cycles_lower_bound, 1024)
        self.assertEqual(result.peak_offsets, 4096)
        self.assertEqual(result.capacity_drains, 0)

    def test_policy_arrays_have_exact_fixed_lengths(self) -> None:
        tables = FiniteTables(ACTIVE_ELEMENTS)
        self.assertEqual(len(tables.offset_valid), ACTIVE_ELEMENTS)
        self.assertEqual(len(tables.row_valid), ROW_SLOTS)
        self.assertEqual(len(tables.line_valid), LINE_SLOTS)

    def test_full_line_occupancy_has_no_issue_order_policy_vector(
        self,
    ) -> None:
        records = full_line_capacity_records()
        models = [
            Model(logical_elements=ACTIVE_ELEMENTS, source_elements=8192)
            for _ in range(2)
        ]
        results = [model.run(records, GEOMETRY) for model in models]
        for result in results:
            self.assertEqual(result.peak_offsets, ACTIVE_ELEMENTS)
            self.assertEqual(result.peak_row_slots, ROW_SLOTS)
            self.assertEqual(result.peak_line_slots, LINE_SLOTS)
            self.assertEqual(result.a_line_requests, LINE_SLOTS)
            self.assertEqual(result.materialized_issue_order_entries, 0)
            self.assertEqual(result.capacity_drains, 0)
            self.assertEqual(
                result.issue_order_sha256,
                "b0e2fa19cb09eabd737219a18c65402e2a141da875fede28838f694898c538ff",
            )
        self.assertEqual(results[0].summary(), results[1].summary())

        tables = models[0].tables
        self.assertIsNotNone(tables)
        sequence_lengths = {
            name: len(value)
            for name, value in vars(tables).items()
            if isinstance(value, (list, tuple))
        }
        self.assertEqual(
            sequence_lengths,
            {
                "offset_valid": ACTIVE_ELEMENTS,
                "offset_itr": ACTIVE_ELEMENTS,
                "offset_wid": ACTIVE_ELEMENTS,
                "offset_next": ACTIVE_ELEMENTS,
                "row_valid": ROW_SLOTS,
                "row_grow": ROW_SLOTS,
                "row_sent": ROW_SLOTS,
                "line_valid": LINE_SLOTS,
                "line_addr": LINE_SLOTS,
                "line_head": LINE_SLOTS,
                "line_tail": LINE_SLOTS,
                "line_words": LINE_SLOTS,
                "issue_row_cursor": NUM_SLICES,
                "issue_grow_row_cursor": NUM_SLICES,
                "issue_line_cursor": NUM_SLICES,
                "issue_active_grow": NUM_SLICES,
                "issue_active_grow_valid": NUM_SLICES,
            },
        )

        self.assertFalse(hasattr(FiniteTables, "_native_slice_lines"))
        for method_name in ("_next_native_line", "begin_issue", "issue_next"):
            tree = ast.parse(
                textwrap.dedent(
                    inspect.getsource(getattr(FiniteTables, method_name))
                )
            )
            self.assertFalse(
                any(
                    isinstance(
                        node,
                        (
                            ast.List,
                            ast.ListComp,
                            ast.Set,
                            ast.SetComp,
                            ast.Dict,
                            ast.DictComp,
                            ast.GeneratorExp,
                        ),
                    )
                    for node in ast.walk(tree)
                ),
                method_name,
            )
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(
                        node.func.id, {"list", "tuple", "set", "dict"}
                    )
                if isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, {"append", "extend"})


class ValidationAndTraversalTest(unittest.TestCase):
    def test_out_of_range_index_rejected_before_policy_construction(
        self,
    ) -> None:
        records = exact_capacity_records(4)
        records[3] = record(3, index=32)
        model = Model(logical_elements=4, source_elements=32)
        with self.assertRaisesRegex(ValueError, "B index"):
            model.run(records, GEOMETRY)
        self.assertIsNone(model.tables)

    def test_malformed_bool_index_rejected_before_policy_construction(
        self,
    ) -> None:
        records = [record(0)]
        records[0] = PhysicalRecord(
            **{
                **records[0].__dict__,
                "index": True,
            }
        )
        model = Model(logical_elements=1, source_elements=2)
        with self.assertRaisesRegex(ValueError, "index must be an integer"):
            model.run(records, GEOMETRY)
        self.assertIsNone(model.tables)

    def test_native_slice_traversal_is_bank_outer_bg_inner(self) -> None:
        tables = FiniteTables(ACTIVE_ELEMENTS)
        for slice_id in range(NUM_SLICES):
            inserted, reason, _ = tables.insert(
                record(
                    slice_id,
                    slice_id=slice_id,
                    grow=9,
                    line=slice_id,
                )
            )
            self.assertTrue(inserted, reason)
        tables.begin_issue(0)
        events = []
        while True:
            event = tables.issue_next(0)
            if event is None:
                break
            events.append(event)
        _, peak_slots, peak_words = tables.finish_issue()
        self.assertEqual(
            [event.slice_id for event in events],
            [0, 4, 8, 12, 1, 5, 9, 13, 2, 6, 10, 14, 3, 7, 11, 15],
        )
        self.assertLessEqual(peak_slots, RESPONSE_SLOTS)
        self.assertLessEqual(peak_words, RESPONSE_WORD_POOL)

    def test_actual_unaligned_b_line_accounting(self) -> None:
        records = exact_capacity_records(16_384)
        result = Model(logical_elements=16_384, source_elements=131_072).run(
            records, GEOMETRY
        )
        self.assertEqual(result.b_unique_lines_per_pass, 1025)
        self.assertEqual(result.b_line_reads, 4100)
        self.assertEqual(result.b_reread_lines, 3075)
        self.assertEqual(result.b_semantic_bytes, 262_144)


class EvidenceAndLedgerTest(unittest.TestCase):
    def test_frozen_trace_fails_closed(self) -> None:
        frozen = Path(
            "/data1/nier/dx100-runs/2026-08-02-transparent-spd-premeeting/"
            "native_direct_16k_matched/run/virtual_trace.log"
        )
        if not frozen.exists():
            self.skipTest("frozen external evidence is unavailable")
        with self.assertRaisesRegex(
            SystemExit, "new owner-run physical trace"
        ):
            extract(frozen)

    def test_extractor_accepts_complete_physical_envelope(self) -> None:
        hashes = "a" * 64
        lines = [
            "BOUNDED_ROW_META schema=1 logical=1 source=8 word_bytes=8 "
            "index_bytes=4 source_commit="
            + "b" * 40
            + f" gem5_sha256={hashes} benchmark_sha256={hashes} "
            f"checkpoint_sha256={hashes} mapping=RoBaRaCoCh slices=16 "
            "rows_per_slice=32 lines_per_row=8 offset_entries=4096"
        ]
        lines.extend(
            f"BOUNDED_ROW_APERTURE slice={slice_id} lower=0 upper=65536"
            for slice_id in range(16)
        )
        lines.extend(
            [
                "BOUNDED_ROW_RECORD itr=0 index=3 b_paddr=0x100004 "
                "a_paddr=0x400000 ch=0 rank=0 bg=0 bank=0 row=5 col=0 "
                "wid=0 slice=0 grow=5",
                "BOUNDED_ROW_ORACLE hash=7228541527853630339 errors=0",
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.log"
            path.write_text("\n".join(lines) + "\n")
            result = extract(path)
        self.assertEqual(result["record_count"], 1)
        self.assertEqual(result["workload_errors"], 0)

    def test_ledgers_charge_every_boundary_and_control_field(self) -> None:
        small = storage_ledger(16_384, 4_096)
        large = storage_ledger(65_536, 16_384)
        for ledger in (small, large):
            names = {field["name"] for field in ledger["fields"]}
            self.assertIn("partition.lower_grow", names)
            self.assertIn("partition.upper_exclusive_grow", names)
            self.assertIn("control.scan_iteration", names)
            self.assertIn("control.response_words_used", names)
            self.assertIn("control.owner_instruction_id", names)
            self.assertIn("control.b_base_paddr", names)
            self.assertIn("control.placements_completed", names)
            self.assertIn("control.pending_writes", names)
            self.assertEqual(
                ledger["charged_total_bytes"],
                sum(field["charged_bytes"] for field in ledger["fields"]),
            )
            self.assertIn("no cross-entry", ledger["packing_rule"])
        self.assertEqual(small["row_slots"], 512)
        self.assertEqual(large["row_slots"], 2048)
        self.assertGreater(
            large["charged_total_bytes"], small["charged_total_bytes"]
        )

    def test_future_contract_is_finite_and_non_authorizing(self) -> None:
        contract = json.loads(
            (
                Path(__file__).resolve().parent
                / "future_gem5_screen_contract.json"
            ).read_text()
        )
        self.assertFalse(contract["production_source_edit"])
        self.assertFalse(
            contract["ownership"]["this_session_claims_production_paths"]
        )
        self.assertEqual(contract["finite_state"]["offset_entries"], 4096)
        self.assertEqual(contract["finite_state"]["row_slots"], 512)
        self.assertEqual(contract["finite_state"]["lines_per_row"], 8)
        self.assertIn(
            "generation", contract["ownership"]["single_operation_identity"]
        )
        self.assertEqual(contract["ownership"]["instruction_id_bits"], 16)
        self.assertEqual(
            contract["charged_operation_fields"]["pending_writes_bits"], 7
        )
        self.assertIn("TERMINAL_ERROR", contract["states"])

    def test_committed_summary_matches_executable_report(self) -> None:
        committed = json.loads(
            (
                Path(__file__).resolve().parent / "results_summary.json"
            ).read_text()
        )
        self.assertEqual(committed, model_report())
        self.assertIsNone(committed["workload_a_line_comparisons"])


if __name__ == "__main__":
    unittest.main()
