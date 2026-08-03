#!/usr/bin/env python3

import ast
import hashlib
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
from extract_grounded_trace import (
    DDR4_MODELED_ADDRESS_LIMIT,
    EXPECTED_FIELD_COUNT,
    PHYSICAL_FIELD_ORDER,
    PHYSICAL_SCHEMA,
    REJECTION_REASON,
    REJECTION_SCHEMA,
    GroundingError,
    _verify_declared_artifacts,
    audit_terminal_evidence,
    compare_semantics,
    encode_grounded_result,
    ground_pair,
    sha256,
    validate_evidence_inventory,
    validate_records,
    validate_rejection_contract,
)

GEOMETRY = ApertureGeometry.synthetic_full_ddr4()
FROZEN_EVIDENCE_ROOT = Path(
    "/data1/nier/worktrees/codex-coordination/sessions/"
    "hybrid-overhead-attribution-20260803-145457-f54ef7d1/"
    "pair_evidence/rejected_schema_v1_attempt"
)


def physical_json_record(itr: int, *, b_value: int | None = None) -> dict:
    if b_value is None:
        b_value = itr + 3
    a_paddr = 0x100000 + b_value * 8
    a_line = a_paddr & ~63
    line_number = a_line >> 6
    bank_group = (line_number >> 7) & 3
    bank = (line_number >> 9) & 3
    row = (line_number >> 11) & 0xFFFF
    fields = {
        "schema": PHYSICAL_SCHEMA,
        "event": "physical_admission",
        "itr": str(itr),
        "b_paddr": hex(0x200000 + itr * 4),
        "b_value": str(b_value),
        "a_paddr": hex(a_paddr),
        "a_line_paddr": hex(a_line),
        "channel": "0",
        "rank": "0",
        "bank_group": str(bank_group),
        "bank": str(bank),
        "row": str(row),
        "column": str(line_number & 0x7F),
        "native_slice": str(bank_group * 4 + bank),
        "grow_addr": hex(row),
        "wid": str((a_paddr >> 3) & 7),
        "generation_available": "0",
        "generation": "0",
        "opcode": "14",
        "optype": "16",
        "if_id": "0",
        "cid": "0",
        "pc": "0x4000",
        "operation_tick": "100",
        "controller_managed": "0",
        "controller_action": "0",
        "controller_transaction": "0",
        "controller_page": "-1",
        "rt_config": "3",
        "aperture_slice_begin": "0",
        "aperture_slice_end": "16",
        "aperture_slices": "16",
        "provenance": "direct_index_descriptor_admission",
    }
    return {"trace_line": itr + 1, "sim_tick": 1000 + itr, **fields}


def set_a_address(item: dict, a_paddr: int) -> None:
    a_line = a_paddr & ~63
    line_number = a_line >> 6
    bank_group = (line_number >> 7) & 3
    bank = (line_number >> 9) & 3
    row = (line_number >> 11) & 0xFFFF
    item.update(
        {
            "a_paddr": hex(a_paddr),
            "a_line_paddr": hex(a_line),
            "bank_group": str(bank_group),
            "bank": str(bank),
            "row": str(row),
            "column": str(line_number & 0x7F),
            "native_slice": str(bank_group * 4 + bank),
            "grow_addr": hex(row),
            "wid": str((a_paddr >> 3) & 7),
        }
    )


def write_physical_fixture(
    root: Path,
    case: str,
    records: list[dict],
) -> tuple[Path, Path]:
    case_dir = root / case
    run_dir = case_dir / "run"
    run_dir.mkdir(parents=True)
    records_path = case_dir / "physical_admission_records.jsonl"
    encoded = "".join(
        json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
        for item in records
    )
    records_path.write_text(encoded)
    trace_path = run_dir / "virtual_trace.log"
    trace_path.write_text("frozen physical source trace\n")
    payload = hashlib.sha256()
    for item in sorted(records, key=lambda record: int(record["itr"], 0)):
        payload.update(
            (
                " ".join(
                    f"{field}={item[field]}" for field in PHYSICAL_FIELD_ORDER
                )
                + "\n"
            ).encode()
        )
    validation = {
        "aperture": {"slice_begin": 0, "slice_end": 16, "slices": 16},
        "field_count": EXPECTED_FIELD_COUNT,
        "generation": {
            "available_records": sum(
                item["generation_available"] == "1" for item in records
            ),
            "unavailable_is_explicit": True,
            "unavailable_records": sum(
                item["generation_available"] == "0" for item in records
            ),
        },
        "operation_ticks": sorted(
            {int(item["operation_tick"], 0) for item in records}
        ),
        "record_count": len(records),
        "record_sha256": payload.hexdigest(),
        "records": {
            "format": "jsonl",
            "order": "logical_itr_ascending",
            "path": str(records_path),
            "record_count": len(records),
            "sha256": sha256(records_path),
        },
        "schema": PHYSICAL_SCHEMA,
        "trace_path": str(trace_path),
        "trace_sha256": sha256(trace_path),
    }
    validation_path = case_dir / "physical_validation.json"
    validation_path.write_text(json.dumps(validation, indent=2) + "\n")
    return records_path, validation_path


def write_terminal_fixture(
    case_dir: Path,
    *,
    case: str = "native_direct_16k",
    mode: str = "native_direct",
    page_elements: int = 16384,
    errors: int = 0,
    terminal: bool = True,
    record_sha256: str = "a" * 64,
) -> None:
    (case_dir / "run").mkdir(parents=True, exist_ok=True)
    (case_dir / "checkpoint.exit").write_text("0\n")
    (case_dir / "restore.exit").write_text("0\n")
    (case_dir / "virtual_tile_consumer_case.pass").write_bytes(b"")
    log = (
        f"VIRTUAL_TILE_CONSUMER_RESULT mode={mode} "
        f"page_elements={page_elements} hash=123 errors={errors}\n"
    )
    if terminal:
        log += "Exiting @ tick 99 because m5_exit instruction encountered\n"
    (case_dir / "restore.log").write_text(log)
    (case_dir / "run" / "stats.txt").write_text(
        "---------- Begin Simulation Statistics ----------\n"
        "x 1\n"
        "---------- End Simulation Statistics   ----------\n"
    )
    (case_dir / "result.tsv").write_text(
        "case\toutput_hash\tphysical_records\tphysical_record_sha256\n"
        f"{case}\t123\t16384\t{record_sha256}\n"
    )


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
    def test_jsonl_accepts_exact_33_field_physical_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            records, validation = write_physical_fixture(
                Path(temporary),
                "native_direct_16k",
                [physical_json_record(0), physical_json_record(1)],
            )
            result = validate_records(records, validation, expected_count=2)
        self.assertEqual(len(result.records), 2)

    def test_jsonl_rejects_schema_field_count_hash_and_provenance(
        self,
    ) -> None:
        mutations = (
            ("schema", "wrong.schema"),
            ("provenance", "inferred_not_physical"),
            ("aperture_slices", "8"),
        )
        for field, value in mutations:
            with self.subTest(
                field=field
            ), tempfile.TemporaryDirectory() as tmp:
                item = physical_json_record(0)
                item[field] = value
                records, validation = write_physical_fixture(
                    Path(tmp), "native_direct_16k", [item]
                )
                with self.assertRaises(GroundingError):
                    validate_records(records, validation, expected_count=1)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, validation = write_physical_fixture(
                root, "native_direct_16k", [physical_json_record(0)]
            )
            envelope = json.loads(validation.read_text())
            envelope["field_count"] = 32
            validation.write_text(json.dumps(envelope))
            with self.assertRaisesRegex(GroundingError, "exactly 33"):
                validate_records(records, validation, expected_count=1)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, validation = write_physical_fixture(
                root, "native_direct_16k", [physical_json_record(0)]
            )
            records.write_text(
                records.read_text().replace("0x200000", "0x200004")
            )
            with self.assertRaisesRegex(GroundingError, "SHA-256"):
                validate_records(records, validation, expected_count=1)

    def test_jsonl_rejects_malformed_duplicate_missing_and_unordered_itr(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, validation = write_physical_fixture(
                root, "native_direct_16k", [physical_json_record(0)]
            )
            raw = records.read_text()
            records.write_text(raw.replace("{", '{"a_paddr":"0x0",', 1))
            envelope = json.loads(validation.read_text())
            envelope["records"]["sha256"] = sha256(records)
            validation.write_text(json.dumps(envelope))
            with self.assertRaisesRegex(GroundingError, "duplicate JSON"):
                validate_records(records, validation, expected_count=1)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, validation = write_physical_fixture(
                root,
                "native_direct_16k",
                [physical_json_record(1), physical_json_record(0)],
            )
            with self.assertRaises(GroundingError):
                validate_records(records, validation, expected_count=2)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            records, validation = write_physical_fixture(
                root,
                "native_direct_16k",
                [physical_json_record(0), physical_json_record(0)],
            )
            with self.assertRaises(GroundingError):
                validate_records(records, validation, expected_count=2)

    def test_jsonl_rejects_physical_decode_inconsistency(self) -> None:
        for field, value in (
            ("a_line_paddr", "0x100040"),
            ("native_slice", "15"),
            ("grow_addr", "99"),
            ("column", "126"),
        ):
            with self.subTest(
                field=field
            ), tempfile.TemporaryDirectory() as tmp:
                item = physical_json_record(0)
                item[field] = value
                records, validation = write_physical_fixture(
                    Path(tmp), "native_direct_16k", [item]
                )
                with self.assertRaises(GroundingError):
                    validate_records(records, validation, expected_count=1)

    def test_jsonl_enforces_exact_33_bit_ddr_address_domain(self) -> None:
        for address in (0, DDR4_MODELED_ADDRESS_LIMIT - 1):
            with self.subTest(
                address=address
            ), tempfile.TemporaryDirectory() as tmp:
                item = physical_json_record(0)
                set_a_address(item, address)
                paths = write_physical_fixture(
                    Path(tmp), "native_direct_16k", [item]
                )
                validated = validate_records(*paths, expected_count=1)
                self.assertEqual(len(validated.records), 1)

        base = physical_json_record(0)
        base_address = int(base["a_paddr"], 0)
        for address in (
            DDR4_MODELED_ADDRESS_LIMIT,
            base_address + DDR4_MODELED_ADDRESS_LIMIT,
        ):
            with self.subTest(
                address=address
            ), tempfile.TemporaryDirectory() as tmp:
                item = physical_json_record(0)
                set_a_address(item, address)
                paths = write_physical_fixture(
                    Path(tmp), "native_direct_16k", [item]
                )
                with self.assertRaisesRegex(GroundingError, "modeled 33-bit"):
                    validate_records(*paths, expected_count=1)

    def test_rejection_contract_is_mandatory_and_exact(self) -> None:
        source_commit = "1" * 40
        gem5_hash = "2" * 64
        rejection = {
            "schema": REJECTION_SCHEMA,
            "status": "rejected",
            "implementation_commit": source_commit,
            "gem5_sha256": gem5_hash,
            "reason": REJECTION_REASON,
            "publication_allowed": False,
            "physical_admission_schema": PHYSICAL_SCHEMA,
            "physical_records_preserved": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rejection.json"
            with self.assertRaisesRegex(GroundingError, "mandatory"):
                validate_rejection_contract(
                    path, source_commit=source_commit, gem5_hash=gem5_hash
                )
            path.write_text(json.dumps(rejection) + "\n")
            self.assertEqual(
                validate_rejection_contract(
                    path, source_commit=source_commit, gem5_hash=gem5_hash
                ),
                sha256(path),
            )
            mutations = {
                "schema": "wrong.schema",
                "status": "accepted",
                "reason": "different reason",
                "physical_admission_schema": "wrong.physical.schema",
                "physical_records_preserved": False,
            }
            for field, value in mutations.items():
                with self.subTest(field=field):
                    changed = {**rejection, field: value}
                    path.write_text(json.dumps(changed) + "\n")
                    with self.assertRaises(GroundingError):
                        validate_rejection_contract(
                            path,
                            source_commit=source_commit,
                            gem5_hash=gem5_hash,
                        )
            for changed in (
                {
                    key: value
                    for key, value in rejection.items()
                    if key != "reason"
                },
                {**rejection, "unexpected": True},
            ):
                path.write_text(json.dumps(changed) + "\n")
                with self.assertRaisesRegex(GroundingError, "fields missing"):
                    validate_rejection_contract(
                        path, source_commit=source_commit, gem5_hash=gem5_hash
                    )

    def test_legacy_inventory_semantic_labels_and_content_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "native_direct_16k"
            case_dir.mkdir()
            inventory = case_dir / "artifact_sha256.txt"
            inventory.write_text(f"{'0' * 64}  /unknown/artifact\n")
            with self.assertRaisesRegex(GroundingError, "semantic label"):
                _verify_declared_artifacts(
                    case_dir, root, root / "input", inventory
                )

            gem5 = root / "gem5.opt"
            gem5.write_bytes(b"frozen gem5")
            inventory.write_text(f"{'0' * 64}  /input/gem5.opt\n")
            with self.assertRaisesRegex(GroundingError, "content mismatch"):
                _verify_declared_artifacts(
                    case_dir, root, root / "input", inventory
                )

    def test_inventory_digest_and_labels_fail_closed(self) -> None:
        entry = {
            "label": "case:manifest.txt",
            "path": "case/manifest.txt",
            "sha256": "3" * 64,
            "size_bytes": 10,
        }
        unsigned = {
            "case": "native_direct_16k",
            "entries": [entry],
            "schema": "dx100.bounded_row_evidence_inventory.v1",
        }
        inventory = {
            **unsigned,
            "digest_sha256": hashlib.sha256(
                json.dumps(
                    unsigned, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        }
        validate_evidence_inventory(inventory)
        for field, value in (
            ("label", "case:wrong-label"),
            ("path", "case/wrong-path"),
            ("sha256", "4" * 64),
        ):
            changed = json.loads(json.dumps(inventory))
            changed["entries"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                GroundingError, "digest"
            ):
                validate_evidence_inventory(changed)

    def test_pair_compares_semantics_but_excludes_treatment_metadata(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native_item = physical_json_record(0)
            transparent_item = physical_json_record(0)
            transparent_item["opcode"] = "99"
            transparent_item["pc"] = "0x9999"
            transparent_item["operation_tick"] = "777"
            transparent_item["sim_tick"] = 888
            native_paths = write_physical_fixture(
                root, "native_direct_16k", [native_item]
            )
            transparent_paths = write_physical_fixture(
                root, "transparent_4k", [transparent_item]
            )
            native = validate_records(*native_paths, expected_count=1)
            transparent = validate_records(
                *transparent_paths, expected_count=1
            )
            self.assertEqual(
                compare_semantics(native, transparent), native.semantic_sha256
            )

            changed_paths = write_physical_fixture(
                root,
                "transparent_changed",
                [physical_json_record(0, b_value=4)],
            )
            changed = validate_records(*changed_paths, expected_count=1)
            with self.assertRaisesRegex(
                GroundingError, "semantic physical mismatch"
            ):
                compare_semantics(native, changed)

    def test_terminal_evidence_rejects_errors_and_missing_m5_exit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            case_dir = Path(temporary) / "native_direct_16k"
            write_terminal_fixture(case_dir)
            result = audit_terminal_evidence(
                case_dir,
                expected_case="native_direct_16k",
                expected_mode="native_direct",
                expected_page_elements=16384,
                expected_record_sha256="a" * 64,
            )
            self.assertEqual(result["workload_errors"], 0)

        for errors, terminal in ((1, True), (0, False)):
            with self.subTest(errors=errors, terminal=terminal):
                temporary = tempfile.TemporaryDirectory()
                self.addCleanup(temporary.cleanup)
                tmp = temporary.name
                case_dir = Path(tmp) / "native_direct_16k"
                write_terminal_fixture(
                    case_dir, errors=errors, terminal=terminal
                )
                with self.assertRaises(GroundingError):
                    audit_terminal_evidence(
                        case_dir,
                        expected_case="native_direct_16k",
                        expected_mode="native_direct",
                        expected_page_elements=16384,
                        expected_record_sha256="a" * 64,
                    )

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
        study_dir = Path(__file__).resolve().parent
        committed = json.loads(
            (study_dir / "results_summary.json").read_text()
        )
        self.assertEqual(committed, model_report())
        self.assertTrue(
            committed["workload_a_line_comparisons"]["exact_semantic_match"]
        )

    def test_committed_manifest_regenerates_byte_for_byte(self) -> None:
        study_dir = Path(__file__).resolve().parent
        grounded_path = study_dir / "grounded_physical_result_manifest.json"
        grounded = json.loads(grounded_path.read_text())
        self.assertEqual(grounded["status"], "grounded")
        self.assertTrue(grounded["pair_comparison"]["exact_match"])
        self.assertEqual(
            grounded["claims"]["gem5_timing_performance"], "not_claimed"
        )

        regenerated = ground_pair(
            FROZEN_EVIDENCE_ROOT
            / "native_direct_16k/physical_admission_records.jsonl",
            FROZEN_EVIDENCE_ROOT
            / "native_direct_16k/physical_validation.json",
            FROZEN_EVIDENCE_ROOT
            / "transparent_4k/physical_admission_records.jsonl",
            FROZEN_EVIDENCE_ROOT / "transparent_4k/physical_validation.json",
        )
        self.assertEqual(
            encode_grounded_result(regenerated), grounded_path.read_bytes()
        )
        for case in ("native", "transparent"):
            inventory = regenerated["provenance"]["cases"][case][
                "evidence_inventory"
            ]
            self.assertGreater(len(inventory["entries"]), 50)
            self.assertEqual(
                inventory["digest_sha256"],
                regenerated["provenance"]["case_inventory_sha256"][case],
            )


if __name__ == "__main__":
    unittest.main()
