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

    def test_four_head_merge_preserves_every_itr_and_wid(self) -> None:
        records = [
            record(i, (i * 7) % 13, i % 16, ((i * 11) % 37 + 1) * 64)
            for i in range(64)
        ]
        runs = MODEL.build_descriptor_runs(records, 16)
        self.assertEqual([len(run) for run in runs], [16, 16, 16, 16])
        merged = MODEL.merge_descriptor_runs(runs)
        self.assertEqual(sorted(item.itr for item in merged), list(range(64)))
        self.assertEqual(
            sorted((item.itr, item.wid) for item in merged),
            [(item.itr, item.wid) for item in records],
        )
        self.assertEqual(list(merged), sorted(merged, key=MODEL.descriptor_sort_key))

    def test_descriptor_spool_is_finite_and_has_analytical_headroom(self) -> None:
        records = [
            record(
                i,
                (i % MODEL.FINITE_OFFSETS) // (MODEL.SLICES * 8),
                i % MODEL.SLICES,
                (i + 1) * MODEL.LINE_BYTES,
            )
            for i in range(MODEL.LOGICAL_ELEMENTS)
        ]
        spool = MODEL.descriptor_spool_model(records)
        self.assertEqual(spool["run_populations"], [4096] * 4)
        self.assertEqual(spool["total_finite_records"], 16384)
        self.assertEqual(spool["record_format"]["used_bits"], 44)
        self.assertEqual(spool["result_iteration_mapping"]["records"], 16384)
        self.assertTrue(
            spool["result_iteration_mapping"]["stable_itr_and_wid_embedded"]
        )
        self.assertFalse(spool["result_iteration_mapping"]["unbounded_mapping_state"])
        self.assertEqual(
            spool[
                "required_records_per_cycle_for_analytical_5pct_stage_headroom"
            ],
            2,
        )
        conservative = spool["analytical_stage_budget_sensitivity"]["2"]
        conservative = conservative["request_cases"]
        conservative = conservative["aug3_full_control_request"]
        conservative = conservative["stage_budget_with_serialized_writeback"]
        self.assertGreaterEqual(
            conservative[
                "analytical_headroom_vs_aug3_modulo_stage_sum_pct"
            ],
            MODEL.MATERIALITY_THRESHOLD_PCT,
        )
        self.assertFalse(conservative["gem5_latency_claim"])

    def test_merge_head_limit_fails_closed(self) -> None:
        runs = tuple(
            (record(index, index, index % MODEL.SLICES),)
            for index in range(MODEL.CONFIGURED_MERGE_HEADS + 1)
        )
        with self.assertRaisesRegex(MODEL.ModelError, "one to 8 finite runs"):
            MODEL.merge_descriptor_runs(runs)
        self.assertEqual(MODEL.GENERAL_SUBRUN_UPPER_BOUND, 512)

    def test_frozen_trace_result_has_eight_subruns_and_no_latency_claim(self) -> None:
        result_path = MODULE_PATH.with_name("results.json")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        spool = result["arms"]["sorted_descriptor_spool"]
        self.assertEqual(spool["run_count"], 8)
        self.assertEqual(
            spool["run_populations"],
            [3883, 213, 3883, 213, 3805, 291, 3883, 213],
        )
        self.assertEqual(spool["merge"]["heads"], 8)
        self.assertEqual(spool["traffic"]["descriptor_padding_bytes"], 256)
        self.assertEqual(spool["traffic"]["descriptor_append_lines"], 2052)
        self.assertEqual(spool["traffic"]["coherent_line_transfers"], 7181)
        self.assertEqual(
            spool["run_formation"]["general_subrun_upper_bound"], 512
        )
        self.assertFalse(result["gate"]["candidate_timing_measured"])
        rendered = json.dumps(result, sort_keys=True)
        self.assertNotIn("latency_reduction", rendered)
        self.assertNotIn("speedup_vs", rendered)

    def test_trace_control_and_timing_calibration_are_not_mixed(self) -> None:
        result = json.loads(
            MODULE_PATH.with_name("results.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            MODULE_PATH.with_name("input_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        trace = result["evidence_sources"]["physical_trace_control_aug8"]
        calibration = result["evidence_sources"]
        calibration = calibration["matched_timing_calibration_aug3"]
        self.assertNotEqual(trace["campaign_root"], calibration["campaign_root"])
        self.assertEqual(
            (trace["simTicks"], trace["row_table_full_events"],
             trace["virtual_build_rounds"], trace["fill_cycles"],
             trace["request_cycles"]),
            (40159152, 845, 103, 13306, 107076),
        )
        full = calibration["full_control"]
        self.assertEqual(
            (full["simTicks"], full["row_table_full_events"],
             full["virtual_build_rounds"], full["fill_cycles"],
             full["request_cycles"]),
            (51504776, 859, 102, 26209, 113320),
        )
        self.assertEqual(
            trace["raw_trace_sha256"],
            result["input"]["raw_trace_sha256"],
        )
        self.assertEqual(
            manifest["physical_trace_control_aug8"]["result_tsv_sha256"],
            trace["result_tsv_sha256"],
        )
        self.assertEqual(
            manifest["matched_timing_calibration_aug3"]
            ["full_control"]["result_tsv_sha256"],
            full["result_tsv_sha256"],
        )

    def test_offline_diagnostic_cannot_claim_native_issue_order(self) -> None:
        result = json.loads(
            MODULE_PATH.with_name("results.json").read_text(encoding="utf-8")
        )
        arms = result["arms"]
        self.assertNotIn("full_metadata", arms)
        diagnostic = arms["unlimited_16k_ordering_diagnostic"]
        self.assertTrue(diagnostic["offline_ordering_diagnostic"])
        self.assertFalse(diagnostic["actual_native_control"])
        self.assertFalse(diagnostic["one_epoch_is_measured"])
        self.assertEqual(diagnostic["peak_row_slots"], 1242)
        self.assertGreater(diagnostic["peak_row_slots"], 16 * 64)
        spool = arms["sorted_descriptor_spool"]
        self.assertEqual(
            spool["merge"]["native_issue_order_relation"],
            "unknown_not_reconstructed",
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
