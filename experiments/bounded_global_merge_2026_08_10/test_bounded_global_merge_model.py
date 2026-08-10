#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("bounded_global_merge_model.py")
SPEC = importlib.util.spec_from_file_location(
    "bounded_global_merge_model", MODULE_PATH
)
assert SPEC and SPEC.loader
MODEL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)


class BoundedGlobalMergeModelTest(unittest.TestCase):
    def test_six_byte_descriptor_round_trip_and_reserved_bits(self) -> None:
        descriptor = MODEL.Descriptor(logical_i=0x3FFF, b_value=0xFFFF_FFFF)
        payload = descriptor.pack()
        self.assertEqual(len(payload), 6)
        self.assertEqual(MODEL.Descriptor.unpack(payload), descriptor)
        corrupt = bytearray(payload)
        corrupt[-1] |= 0x80
        with self.assertRaisesRegex(MODEL.ModelError, "reserved bits"):
            MODEL.Descriptor.unpack(bytes(corrupt))

    def test_source_decode_obeys_non_numeric_row_table_slice_order(
        self,
    ) -> None:
        translation = MODEL.AddressTranslation(
            base_page_offset=0,
            physical_pages=(
                (0, MODEL.line_for(3, 4, 0)),
                (1, MODEL.line_for(3, 1, 0)),
            ),
        )
        slice_four = MODEL.Descriptor(logical_i=0, b_value=0)
        slice_one = MODEL.Descriptor(logical_i=1, b_value=512)
        self.assertEqual(
            MODEL.decode_descriptor(slice_four, translation).native_slice, 4
        )
        self.assertEqual(
            MODEL.decode_descriptor(slice_one, translation).native_slice, 1
        )
        self.assertLess(
            MODEL.descriptor_key(slice_four, translation),
            MODEL.descriptor_key(slice_one, translation),
        )
        self.assertEqual(
            list(MODEL.ROW_TABLE_SLICE_ORDER[:5]), [0, 4, 8, 12, 1]
        )

    def test_duplicate_logical_iteration_fails_closed(self) -> None:
        trace = MODEL.make_synthetic_trace(
            "duplicate_i", repeated_lines=True, adversarial_order=False
        )
        descriptors = list(trace.descriptors)
        descriptors[1] = MODEL.Descriptor(
            logical_i=0, b_value=descriptors[1].b_value
        )
        duplicate = MODEL.InputTrace(
            name=trace.name,
            descriptors=tuple(descriptors),
            a_translation=trace.a_translation,
            b_base=trace.b_base,
            provenance=trace.provenance,
        )
        with self.assertRaisesRegex(MODEL.ModelError, "duplicate logical i"):
            MODEL.validate_trace(duplicate)

    def test_repeated_lines_across_runs_are_globally_coalesced(self) -> None:
        trace = MODEL.make_synthetic_trace(
            "repeated", repeated_lines=True, adversarial_order=False
        )
        comparison = MODEL.compare_trace(trace)
        current = comparison["arms"]["current_four_pass"]["memory_behavior"]
        candidate = comparison["arms"]["bounded_global_merge"][
            "memory_behavior"
        ]
        oracle = comparison["arms"]["native_global16_oracle"][
            "memory_behavior"
        ]
        self.assertEqual(
            comparison["cross_population_opportunities"][
                "a_lines_in_multiple_populations"
            ],
            6,
        )
        self.assertEqual(current["a_line_requests"], 236)
        self.assertEqual(candidate["a_line_requests"], 228)
        self.assertEqual(candidate["issue_sha256"], oracle["issue_sha256"])
        self.assertEqual(
            candidate["placement_sha256"], oracle["placement_sha256"]
        )
        self.assertEqual(candidate["output_sha256"], oracle["output_sha256"])

    def test_unique_lines_still_remove_dram_row_reactivations(self) -> None:
        trace = MODEL.make_synthetic_trace(
            "unique", repeated_lines=False, adversarial_order=False
        )
        comparison = MODEL.compare_trace(trace)
        current = comparison["arms"]["current_four_pass"]["memory_behavior"]
        candidate = comparison["arms"]["bounded_global_merge"][
            "memory_behavior"
        ]
        self.assertEqual(
            current["a_line_requests"], candidate["a_line_requests"]
        )
        self.assertEqual(current["dram_row_activations"], 11)
        self.assertEqual(candidate["dram_row_activations"], 8)
        self.assertEqual(candidate["dram_row_reactivations"], 0)

    def test_adversarial_ingress_reconstructs_every_exact_output(self) -> None:
        trace = MODEL.make_synthetic_trace(
            "adversarial", repeated_lines=True, adversarial_order=True
        )
        comparison = MODEL.compare_trace(trace)
        arms = comparison["arms"]
        output_hashes = {
            arm["memory_behavior"]["output_sha256"] for arm in arms.values()
        }
        self.assertEqual(len(output_hashes), 1)
        self.assertTrue(comparison["comparison"]["all_outputs_exact"])
        self.assertEqual(
            arms["bounded_global_merge"]["memory_behavior"]["a_line_requests"],
            56,
        )
        self.assertEqual(
            arms["bounded_global_merge"]["memory_behavior"][
                "dram_row_activations"
            ],
            8,
        )

    def test_llc_reader_handles_cross_line_six_byte_records_once(self) -> None:
        counts = [16, 16, 16, 16]
        store = MODEL.LogicalLLCStore(counts)
        records = [MODEL.Descriptor(index, index * 3) for index in range(16)]
        store.write_sorted_run(0, records)
        reader = store.reader(0, "unit")
        decoded = []
        while True:
            descriptor = reader.next()
            if descriptor is None:
                break
            decoded.append(descriptor)
        self.assertEqual(decoded, records)
        self.assertEqual(reader.lines_read, 2)
        self.assertLessEqual(
            reader.max_carry_bytes, MODEL.DESCRIPTOR_BYTES - 1
        )

    def test_run_population_above_4096_is_rejected(self) -> None:
        with self.assertRaisesRegex(MODEL.ModelError, "descriptor bound"):
            MODEL.LogicalLLCStore([4097, 4096, 4096, 4095])

    def test_frozen_full_trace_accounting_and_vertical_slice_gate(
        self,
    ) -> None:
        result = json.loads(
            MODULE_PATH.with_name("results.json").read_text(encoding="utf-8")
        )
        full = next(
            trace
            for trace in result["traces"]
            if trace["trace"]["name"]
            == "current_resident_first_physical_trace"
        )
        candidate = full["arms"]["bounded_global_merge"]
        current = full["arms"]["current_four_pass"]
        oracle = full["arms"]["native_global16_oracle"]
        self.assertEqual(
            candidate["bounds"]["active_descriptor_high_water"], 4096
        )
        self.assertEqual(candidate["plan"]["populations"], [4096] * 4)
        self.assertEqual(
            candidate["traffic"]["backing_reserved_bytes"], 98_304
        )
        self.assertEqual(candidate["traffic"]["backing_reserved_lines"], 1536)
        self.assertEqual(
            candidate["traffic"]["classification_append_records"], 12_288
        )
        self.assertEqual(
            candidate["traffic"]["classification_append_line_writes"], 1152
        )
        self.assertEqual(candidate["traffic"]["sort_input_read_lines"], 1152)
        self.assertEqual(candidate["traffic"]["sorted_run_write_lines"], 1536)
        self.assertEqual(candidate["traffic"]["merge_read_lines"], 1536)
        self.assertEqual(candidate["memory_behavior"]["a_line_requests"], 9523)
        self.assertEqual(current["memory_behavior"]["a_line_requests"], 9577)
        self.assertEqual(
            candidate["memory_behavior"]["dram_row_activations"], 129
        )
        self.assertEqual(
            current["memory_behavior"]["dram_row_activations"], 137
        )
        self.assertEqual(
            candidate["memory_behavior"]["issue_sha256"],
            oracle["memory_behavior"]["issue_sha256"],
        )
        self.assertTrue(
            result["aggregate_gate"]["propose_live_gem5_vertical_slice"]
        )
        self.assertFalse(result["aggregate_gate"]["promotion_claim"])

    def test_measured_comparator_values_are_exact_and_not_candidate_timing(
        self,
    ) -> None:
        result = json.loads(
            MODULE_PATH.with_name("results.json").read_text(encoding="utf-8")
        )
        measured = result["measured_current_context"]
        self.assertEqual(measured["native4"]["simTicks"], 59_267_176)
        self.assertEqual(measured["bounded_paged4"]["simTicks"], 60_913_869)
        self.assertEqual(
            measured["bounded_paged4"]["descriptor_fill_cycles"], 22_029_879
        )
        self.assertEqual(
            measured["bounded_paged4"]["a_request_cycles"], 30_625_172
        )
        self.assertFalse(
            result["model_boundary"]["candidate_gem5_timing_measured"]
        )
        self.assertFalse(result["model_boundary"]["simTicks_claim"])


if __name__ == "__main__":
    unittest.main()
