#!/usr/bin/env python3
"""Contract checks for the opt-in full-CG logical-page/RMW vertical slice."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
MAKEFILE = (ROOT / "benchmarks/NAS/cg/Makefile").read_text()
RUNNER_PATH = ROOT / "experiments/scripts/run_cg_logical_page_rmw_hybrid.sh"


class CGLogicalPageRmwHybridContract(unittest.TestCase):
    def test_build_is_opt_in_and_reserves_scheduler_lanes(self):
        self.assertIn("$(SUITE)_maa_16K_logical_page_rmw", MAKEFILE)
        for token in (
            "-DCG_LOGICAL_PAGE_RMW",
            "-DNUM_TILES_PER_CORE=10",
            "-DTILE_SIZE=16384",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
        ):
            self.assertIn(token, MAKEFILE)
        self.assertIn("static CgRmwTreatment cg_rmw_treatment =", SOURCE)
        self.assertIn("CgRmwTreatment::Legacy4K;", SOURCE)

    def test_intermediates_are_coherent_aligned_backings(self):
        self.assertIn("constexpr size_t cg_logical_backing_bytes", SOURCE)
        self.assertGreaterEqual(
            SOURCE.count("alignas(cg_logical_backing_bytes)"), 3
        )
        for backing in ("cg_soa_indices", "cg_soa_values", "cg_soa_products"):
            self.assertIn(f"add_mem_region({backing}[core]", SOURCE)
        self.assertIn("alignas(TILE_SIZE * sizeof(float))", SOURCE)

    def test_page_intermediates_use_response_bearing_publication(self):
        helper = SOURCE[
            SOURCE.index("cg_publish_index_value_page") : SOURCE.index(
                "cg_logical_multiply_rmw"
            )
        ]
        self.assertIn(
            "maa_publish_spd_page_logical16_response_bearing<uint32_t>",
            helper,
        )
        self.assertIn(
            "maa_publish_spd_page_logical16_response_bearing<float>", helper
        )
        self.assertEqual(helper.count("wait_ready("), 2)
        self.assertNotIn("atomic_thread_fence", helper)
        self.assertNotRegex(helper, r"cg_soa_(indices|values)\[tid\]\[")

    def test_ordinary_logical_alu_feeds_no_result_soa_rmw(self):
        helper = SOURCE[
            SOURCE.index("cg_logical_multiply_rmw") : SOURCE.index(
                "#endif\n#endif\n\n#ifdef CG_FP_ENABLE"
            )
        ]
        self.assertIn("maa_alu_vector_logical<float>", helper)
        self.assertIn("Operation_t::MUL_OP", helper)
        self.assertIn("maa_indirect_rmw_vector_soa_jit<float>", helper)
        self.assertIn("cg_soa_products[tid], nullptr", helper)
        self.assertIn("Operation_t::ADD_OP", helper)

    def test_both_sparse_matrix_vector_loops_use_the_full_window_chain(self):
        self.assertEqual(SOURCE.count("cg_logical_multiply_rmw(tid,"), 2)
        self.assertEqual(SOURCE.count("cg_publish_index_value_page(\n"), 2)
        self.assertIn("all_spmv_full_windows", SOURCE)
        self.assertIn("host_payload_access=", SOURCE)

    def test_runner_requires_exact_mechanism_and_provenance_closure(self):
        runner = RUNNER_PATH.read_text()
        for token in (
            "CG_REFERENCE_LOG",
            "CG_FINGERPRINT",
            "logical_page_soa_jit",
            "--maa_logical_tile_page_scheduler",
            "logical_page_native_dispatch",
            "logical_page_native_complete",
            "logical_page_retire",
            "STR_PublishTerminals",
            "IND_SoaJitTerminalCompletions",
            "IND_SoaJitFallbacks",
            "IND_SoaJitOpenContexts",
            "source_status.after",
            "reference_sha256",
        ):
            self.assertIn(token, runner)
        self.assertNotIn("native16", runner)
        self.assertNotIn("native4", runner)
        self.assertNotIn("timeout_command", runner)
        self.assertNotRegex(runner, r"(?m)^\s*timeout(?:\s|$)")


if __name__ == "__main__":
    unittest.main()
