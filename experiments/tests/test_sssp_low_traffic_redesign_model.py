#!/usr/bin/env python3
"""Regression tests for the host-only SSSP low-traffic redesign model."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL_PATH = ROOT / "experiments/analysis/sssp_low_traffic_redesign_model.py"
SPEC = importlib.util.spec_from_file_location(
    "sssp_low_traffic_model", MODEL_PATH
)
MODEL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODEL
SPEC.loader.exec_module(MODEL)


class EvidenceCostTest(unittest.TestCase):
    def test_accepted_micro_arithmetic(self):
        derived = MODEL.derived_evidence()
        self.assertAlmostEqual(derived["slowdown_vs_native4"], 12.784006, 6)
        self.assertAlmostEqual(
            derived["native16_speedup_vs_native4"], 1.087764704, 9
        )
        self.assertAlmostEqual(
            derived["hybrid_speedup_vs_native4"], 0.078222740, 9
        )
        self.assertGreater(
            derived["excess_cycles_explained_by_idle_fraction"], 0.98
        )
        self.assertEqual(derived["publisher_transport_bytes"], 524_288)
        self.assertEqual(derived["old_result_semantic_bytes"], 262_144)
        self.assertEqual(derived["old_result_transport_bytes"], 1_000_512)
        self.assertGreater(derived["old_result_line_amplification"], 3.8)
        self.assertEqual(derived["explicit_accounted_request_lines"], 74_963)

    def test_option_storage_and_traffic_are_explicit(self):
        costs = MODEL.option_costs()
        option_a = costs["A_direct_inline_handoff"]
        self.assertEqual(option_a["coherent_publication_lines"], 0)
        self.assertEqual(option_a["incremental_external_backing_bytes"], 0)
        self.assertEqual(
            option_a["live_inline_operand_bytes_in_existing_offset_aux"],
            65_536,
        )
        self.assertTrue(option_a["preserves_4k_spd"])
        self.assertFalse(
            option_a["preserves_only_4k_total_payload_without_aux_reuse"]
        )

        option_c = costs["C_post_update_snapshot_recompute"]
        self.assertEqual(
            option_c["incremental_external_backing_bytes"], 278_532
        )
        self.assertFalse(option_c["fixed_geometry_backing"])

        combined = costs["recommended_A_plus_D"]
        self.assertLessEqual(combined["incremental_sram_bytes_per_unit"], 1024)
        self.assertEqual(combined["coherent_index_value_publication_lines"], 0)
        self.assertEqual(combined["old_result_write_lines"], 0)
        self.assertGreater(
            combined["serialized_write_line_reduction_fraction"], 0.65
        )


class CorrectnessTest(unittest.TestCase):
    def test_duplicate_and_conflict_search(self):
        result = MODEL.exhaustive_correctness_summary()
        self.assertEqual(result["candidate_assignments"], 81)
        self.assertEqual(result["linearization_schedules"], 1_944)
        self.assertEqual(result["A_C_D_final_distance_and_progress"], "PASS")
        self.assertEqual(result["B_progress"], "FAIL")
        self.assertGreater(result["stale_but_safe_D_retirements"], 0)

    def test_unconditional_push_has_positive_weight_nontermination(self):
        distance, trace = MODEL.unconditional_same_bin_trace(12)
        self.assertEqual(distance, (0, 1))
        self.assertEqual(set(trace), {(0, 1), (1, 0)})
        self.assertEqual(trace[0], trace[2])

    def test_value_lifetime_requires_retention_across_all_pages(self):
        pages = (
            (MODEL.Lane(0, 0, 0, 3),),
            (MODEL.Lane(0, 1, 1, 4),),
            (MODEL.Lane(0, 2, 0, 8),),
            (MODEL.Lane(0, 3, 1, 9),),
        )
        lanes = tuple(lane for page in pages for lane in page)
        self.assertEqual(
            MODEL.option_a_inline_handoff((10, 10), lanes), (3, 4)
        )
        self.assertEqual(
            MODEL.option_a_overwritten_page((10, 10), pages), (10, 9)
        )

    def test_range_loop_cursor_is_exact_across_physical_pages(self):
        bounds = ((0, 3000), (3000, 7000), (7000, 17000), (17000, 20000))
        pages, terminal = MODEL.range_loop_pages(bounds)
        flattened = tuple(item for page in pages for item in page)
        self.assertEqual(
            tuple(edge for _, edge in flattened), tuple(range(20000))
        )
        self.assertEqual(
            tuple(len(page) for page in pages), (4096, 4096, 4096, 4096, 3616)
        )
        self.assertEqual(terminal, (4, -1))


class SourceGroundingTest(unittest.TestCase):
    def test_model_matches_current_producer_and_consumer_shapes(self):
        sssp = (ROOT / "benchmarks/gapbs/src/sssp.cc").read_text()
        tables = (ROOT / "src/mem/MAA/Tables.hh").read_text()
        indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        range_fuser = (ROOT / "src/mem/MAA/RangeFuser.cc").read_text()

        self.assertIn("PublishSsspHybridPage(", sssp)
        self.assertIn("maa_indirect_rmw_vector_soa_jit_old_result(", sssp)
        self.assertIn("for (size_t page = 0; page < 4; ++page)", sssp)
        self.assertIn("int pass;", tables)
        self.assertIn("insertPageFedSoaJitIndex", indirect)
        self.assertIn(
            "offset_table->consume_entry(context.nextOffset)", indirect
        )
        self.assertIn("soa_jit_old_result_buffer.capture(", indirect)
        self.assertIn("my_last_i = maa->rf->getData<int>", range_fuser)
        self.assertIn("my_last_j = maa->rf->getData<int>", range_fuser)
        self.assertIn(
            "maa->rf->setData<int>(my_instruction->dst1RegID", range_fuser
        )
        self.assertIn(
            "maa->rf->setData<int>(my_instruction->dst2RegID", range_fuser
        )


if __name__ == "__main__":
    unittest.main()
