#!/usr/bin/env python3
"""Tests for the reproducible tile-liveness source inventory."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/analysis/inventory_tile_liveness.py"
SPEC = importlib.util.spec_from_file_location(
    "tile_liveness_inventory", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class TileLivenessInventoryTest(unittest.TestCase):
    def parse_fixture(self, source: str):
        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            path = temporary_root / "bench.cpp"
            path.write_text(source, encoding="utf-8")
            saved = MODULE.ROOT
            MODULE.ROOT = temporary_root
            try:
                calls, _ = MODULE.parse_calls(path)
            finally:
                MODULE.ROOT = saved
        return calls

    def test_comments_nested_arguments_and_aliases(self):
        calls = self.parse_fixture(
            """
            void f() {
              // maa_stream_load<int>(b, lo, hi, one, dead);
              maa_stream_load<int>(b, lo, max(x, y), one, idx);
              /* maa_indirect_load<float>(a, dead, out); */
              maa_indirect_rmw<float>(a, idx, value, Operation_t::ADD_OP);
            }
            """
        )
        self.assertEqual(
            [call["name"] for call in calls],
            ["maa_stream_load", "maa_indirect_rmw_vector"],
        )
        self.assertEqual(calls[0]["arguments"][2], "max(x, y)")
        self.assertEqual(calls[1]["inputs"][0]["tile"], "idx")

    def test_chain_categories_and_multiple_consumers(self):
        calls = self.parse_fixture(
            """
            void f() {
              maa_stream_load<int>(b, lo, hi, one, idx);
              maa_indirect_load<float>(a, idx, gathered);
              maa_alu_scalar<float>(gathered, scale, transformed, Operation_t::MUL_OP);
              maa_stream_store<float>(c, lo, hi, one, transformed);
              maa_indirect_rmw_vector<float>(d, idx, gathered, Operation_t::ADD_OP);
            }
            """
        )
        edges, outgoing = MODULE.connect_calls(calls)
        counts = {
            name: 0
            for name in (
                "stream_index_to_indirect",
                "indirect_result_to_alu",
                "result_to_stream_store",
                "indirect_store_rmw_operand",
            )
        }
        for edge in edges:
            if edge["class"] in counts:
                counts[edge["class"]] += 1
        self.assertEqual(counts["stream_index_to_indirect"], 2)
        self.assertEqual(counts["indirect_result_to_alu"], 1)
        self.assertEqual(counts["result_to_stream_store"], 1)
        self.assertEqual(counts["indirect_store_rmw_operand"], 1)
        self.assertEqual(len(outgoing[calls[0]["id"]]), 2)
        legal = MODULE.legality(calls, edges, outgoing)
        required = legal["spd_or_llc_backed_storage_required"]
        self.assertTrue(
            any("multiple_consumers" in row["reasons"] for row in required)
        )

    def test_sibling_branch_definition_does_not_reach_other_branch(self):
        calls = self.parse_fixture(
            """
            void f(bool choose) {
              if (choose) {
                maa_stream_load<int>(b, lo, hi, one, idx);
              } else {
                maa_indirect_load<float>(a, idx, gathered);
              }
            }
            """
        )
        edges, _ = MODULE.connect_calls(calls)
        self.assertEqual(edges, [])

    def test_direct_sinks_preserve_materialization_distinction(self):
        calls = self.parse_fixture(
            """
            void f() {
              maa_indirect_load_virtual_index<double>(a, b, done, backing, lo, hi, one);
              maa_indirect_load_spd_stream<double>(a, idx, result, c, lo, hi, one);
              maa_virtual_tile_alu_scalar_store<double>(backing, c, done, input, output, scale, lo, hi, one, Operation_t::MUL_OP);
            }
            """
        )
        sinks = MODULE.terminal_sinks(calls)
        by_kind = {row["kind"]: row["note"] for row in sinks}
        self.assertIn(
            "both index and result payload tiles eliminated",
            by_kind["direct_index_gather_to_backing"],
        )
        self.assertIn(
            "materialized", by_kind["spd_mediated_gather_stream_store"]
        )
        self.assertIn(
            "physical input/output pages retained",
            by_kind["paged_scalar_transform_to_direct_store"],
        )

    def test_cpu_payload_is_distinct_from_wait_and_size(self):
        calls = self.parse_fixture(
            """
            void f() {
              float *result_ptr = get_cacheable_tile_pointer<float>(result);
              maa_indirect_load<float>(a, idx, result);
              wait_ready(result);
              int size = get_tile_size(result);
              sink(result_ptr[0], size);
            }
            """
        )
        edges, _ = MODULE.connect_calls(calls)
        classes = {edge["class"] for edge in edges}
        self.assertIn("result_to_cpu_payload", classes)
        self.assertIn("result_to_cpu_ready_wait", classes)
        self.assertIn("result_to_cpu_size_read", classes)
        self.assertNotIn("result_to_cpu_pointer_exposure", classes)

    def test_required_storage_arithmetic(self):
        rows = {
            (
                row["physical_elements"],
                row["logical_elements"],
                row["datatype"],
            ): row
            for row in MODULE.storage_arithmetic()
        }
        fp32_4_16 = rows[(4096, 16384, "FP32")]
        self.assertEqual(fp32_4_16["physical_page_bytes"], 16384)
        self.assertEqual(fp32_4_16["logical_tile_bytes"], 65536)
        fp64_4_16 = rows[(4096, 16384, "FP64")]
        self.assertEqual(fp64_4_16["physical_page_bytes"], 32768)
        self.assertEqual(fp64_4_16["logical_tile_bytes"], 131072)
        fp32_16_64 = rows[(16384, 65536, "FP32")]
        self.assertEqual(fp32_16_64["physical_page_bytes"], 65536)
        self.assertEqual(fp32_16_64["logical_tile_bytes"], 262144)
        fp64_16_64 = rows[(16384, 65536, "FP64")]
        self.assertEqual(fp64_16_64["physical_page_bytes"], 131072)
        self.assertEqual(fp64_16_64["logical_tile_bytes"], 524288)
        self.assertTrue(
            all(row["pages_per_logical_tile"] == 4 for row in rows.values())
        )

    def test_primary_paper_provenance_is_machine_readable(self):
        paper = MODULE.PAPER_PROVENANCE
        self.assertEqual(paper["doi"], "10.1145/3695053.3731015")
        self.assertEqual(paper["arxiv"], "2505.23073v2")
        self.assertEqual(
            paper["verified_local_sha256"],
            "ec18bdc585f32e3da5c0fd467e686dd2137b3db88d4c327d510509213e7c44a3",
        )
        self.assertIn(
            "duplicate-address",
            " ".join(
                paper["tile_sweep_interpretation"]["explicit_paper_mechanisms"]
            ),
        )

    def test_committed_artifact_matches_exact_base_and_inputs(self):
        artifact_path = (
            ROOT
            / "experiments/evidence/2026-08-08_tile_liveness_inventory.json"
        )
        if not artifact_path.exists():
            self.skipTest(
                "artifact is generated after the implementation test"
            )
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        generated = MODULE.build_inventory(
            MODULE.DEFAULT_ROOTS,
            artifact["source"]["analyzed_revision"],
        )
        self.assertEqual(generated, artifact)


if __name__ == "__main__":
    unittest.main()
