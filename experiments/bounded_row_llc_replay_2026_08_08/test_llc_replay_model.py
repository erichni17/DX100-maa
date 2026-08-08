#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("llc_replay_model.py")
SPEC = importlib.util.spec_from_file_location("llc_replay_model", MODULE_PATH)
assert SPEC and SPEC.loader
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)


def record(itr: int, row: int, native_slice: int = 0, line: int | None = None):
    if line is None:
        line = (itr + 1) * MODEL.LINE_BYTES
    return MODEL.Record(
        itr=itr,
        b_value=itr % MODEL.SOURCE_ELEMENTS,
        b_paddr=0x1000 + itr * 4,
        a_line_paddr=line,
        native_slice=native_slice,
        row=row,
        grow=row,
        wid=itr % 8,
    )


class LlcReplayModelTest(unittest.TestCase):
    def test_replay_and_modulo_have_identical_a_order(self) -> None:
        records = [record(i, (i * 5) % 12, i % 16) for i in range(64)]
        streams = MODEL.partition_streams(records)
        modulo = MODEL.build_schedule(streams, 16, 4)
        replay = MODEL.build_schedule(streams, 16, 4)
        self.assertEqual(modulo.issue_sha256, replay.issue_sha256)
        self.assertEqual(modulo.placement_sha256, replay.placement_sha256)

    def test_partition_overflow_forces_extra_finite_epoch(self) -> None:
        records = [record(i, 1, 0) for i in range(17)]
        streams = MODEL.partition_streams(records)
        schedule = MODEL.build_schedule(streams, 16, 4)
        self.assertEqual([len(epoch.records) for epoch in schedule.epochs], [16, 1])
        self.assertEqual(schedule.drain_causes["offset_capacity"], 1)

    def test_row_slot_overflow_drains_and_retries(self) -> None:
        records = [record(i, i, 0) for i in range(5)]
        schedule = MODEL.build_schedule([records], 16, 4)
        self.assertEqual([len(epoch.records) for epoch in schedule.epochs], [4, 1])
        self.assertEqual(schedule.drain_causes["row_capacity"], 1)

    def test_generic_and_packed_replay_charge_coherent_writeback(self) -> None:
        records = [record(i, i % 4) for i in range(64)]
        packed = MODEL.replay_traffic(records, 4)
        generic = MODEL.replay_traffic(records, 8)
        self.assertEqual(packed["replay_records"], 48)
        self.assertEqual(
            packed["coherent_line_transfers"],
            packed["original_b_read_lines"]
            + packed["backing_full_line_stores"]
            + packed["backing_replay_read_lines"]
            + packed["eventual_dirty_writeback_lines"],
        )
        self.assertGreater(
            generic["coherent_line_transfers"], packed["coherent_line_transfers"]
        )

    def test_loader_fails_closed_on_hash_and_schema(self) -> None:
        raw = {
            "schema": MODEL.SCHEMA,
            "event": "physical_admission",
            "itr": "0",
            "b_value": "0",
            "b_paddr": "0x1000",
            "a_line_paddr": "0x2000",
            "native_slice": "0",
            "row": "1",
            "grow_addr": "0x1",
            "wid": "0",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            path.write_text(json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(MODEL.ModelError, "expected 16384"):
                MODEL.load_trace(path, digest)
            with self.assertRaisesRegex(MODEL.ModelError, "SHA-256 mismatch"):
                MODEL.load_trace(path, "0" * 64)


if __name__ == "__main__":
    unittest.main()
