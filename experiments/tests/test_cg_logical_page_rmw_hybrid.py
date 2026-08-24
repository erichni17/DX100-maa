#!/usr/bin/env python3
"""Contract checks for the physical-page-product CG hybrid gate."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
MAKEFILE = (ROOT / "benchmarks/NAS/cg/Makefile").read_text()
RUNNER_PATH = ROOT / "experiments/scripts/run_cg_logical_page_rmw_hybrid.sh"


class CGLogicalPageRmwHybridContract(unittest.TestCase):
    def test_build_preserves_logical_scheduler_target_and_adds_lane_free_arm(
        self,
    ):
        logical_start = MAKEFILE.index("%_maa_16K_logical_page_rmw:")
        physical_start = MAKEFILE.index("%_maa_16K_physical_page_product:")
        logical_target = MAKEFILE[logical_start:physical_start]
        physical_target = MAKEFILE[
            physical_start : MAKEFILE.index("%_maa_32K:", physical_start)
        ]
        for token in (
            "-DCG_LOGICAL_PAGE_RMW",
            "-DNUM_TILES_PER_CORE=10",
            "-DTILE_SIZE=16384",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
        ):
            self.assertIn(token, logical_target)
        for token in (
            "-DCG_LOGICAL_PAGE_RMW",
            "-DCG_PHYSICAL_PAGE_PRODUCT_ONLY",
            "-DNUM_TILES_PER_CORE=8",
            "-DTILE_SIZE=16384",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
        ):
            self.assertIn(token, physical_target)
        self.assertNotIn("-DNUM_TILES_PER_CORE=10", physical_target)
        self.assertIn("static CgRmwTreatment cg_rmw_treatment =", SOURCE)
        self.assertIn("CgRmwTreatment::Legacy4K;", SOURCE)
        self.assertIn("PhysicalPageProductSoaJit", SOURCE)
        self.assertIn("physical_page_product_soa_jit", SOURCE)
        self.assertIn("physical-page-product-only build requires", SOURCE)
        self.assertIn("static_assert(NUM_TILES_PER_CORE == 8", SOURCE)

    def test_gate_uses_full_dx100_row_table_geometry(self):
        runner = RUNNER_PATH.read_text()
        self.assertIn("--maa_num_initial_row_table_slices=32", runner)
        self.assertIn("num_initial_row_table_slices=32", runner)
        self.assertNotIn("--maa_num_initial_row_table_slices=16", runner)
        self.assertIn("--mem-channels=2", runner)
        self.assertIn("memory_channels=2", runner)
        self.assertIn("system\\.mem_ctrls[01]", runner)

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

    def test_physical_page_product_treatment_publishes_no_a_values(self):
        helper = SOURCE[
            SOURCE.index("cg_publish_index_product_page") : SOURCE.index(
                "#endif\n#endif\n\n#ifdef CG_FP_ENABLE"
            )
        ]
        self.assertIn(
            "maa_publish_spd_page_logical16_response_bearing<uint32_t>",
            helper,
        )
        self.assertIn(
            "maa_publish_spd_page_logical16_response_bearing<float>", helper
        )
        self.assertIn("cg_soa_products[tid]", helper)
        self.assertNotIn("cg_soa_values[tid]", helper)
        self.assertIn("maa_indirect_rmw_vector_soa_jit<float>", helper)
        self.assertNotIn("maa_alu_vector_logical<float>", helper)

    def test_both_spmv_sites_multiply_and_publish_each_physical_page(self):
        self.assertEqual(SOURCE.count("cg_physical_page_product_rmw(tid,"), 2)
        self.assertEqual(SOURCE.count("cg_publish_index_product_page(\n"), 2)
        self.assertGreaterEqual(
            SOURCE.count("maa_alu_vector<float>(t4, t5, t7,"), 2
        )
        self.assertIn("wait_ready(t6);", SOURCE)

    def test_both_sparse_matrix_vector_loops_use_the_full_window_chain(self):
        self.assertEqual(SOURCE.count("cg_logical_multiply_rmw(tid,"), 2)
        self.assertEqual(SOURCE.count("cg_publish_index_value_page(\n"), 2)
        self.assertIn("all_spmv_full_windows", SOURCE)
        self.assertIn("host_payload_access=", SOURCE)

    def test_each_spmv_site_counts_eligibility_and_routing_independently(self):
        for site in ("q_spmv", "residual_spmv"):
            self.assertEqual(
                SOURCE.count(f"cg_{site}_eligible_windows[tid]++"), 1
            )
            self.assertEqual(
                SOURCE.count(f"cg_{site}_routed_windows[tid]++"), 1
            )
            self.assertIn(f"{site}_eligible_windows=", SOURCE)
            self.assertIn(f"{site}_routed_windows=", SOURCE)
        self.assertIn("q_spmv_eligible_windows > 0", SOURCE)
        self.assertIn(
            "q_spmv_eligible_windows == q_spmv_routed_windows", SOURCE
        )
        self.assertIn("residual_spmv_eligible_windows > 0", SOURCE)
        self.assertIn("residual_spmv_eligible_windows ==", SOURCE)

    def test_storage_accounting_includes_external_and_physical_payloads(self):
        self.assertIn("cg_virtual_gather_coherent_backing_bytes", SOURCE)
        self.assertIn("cg_external_coherent_backing_bytes", SOURCE)
        self.assertIn("cg_physical_spd_payload_bytes", SOURCE)
        self.assertIn(
            "cg_logical_scheduler_reserved_lane_payload_bytes", SOURCE
        )
        runner = RUNNER_PATH.read_text()
        for token in (
            "external_coherent_backing_bytes=786432",
            "physical_spd_payload_bytes=524288",
            "logical_tile_page_scheduler=false",
            "logical_scheduler_reserved_lanes=0",
            "logical_scheduler_reserved_lane_payload_bytes=0",
            "removed_reserved_lane_payload_bytes=131072",
        ):
            self.assertIn(token, runner)

    def test_runner_uses_eight_guest_tiles_and_disables_page_scheduler(self):
        runner = RUNNER_PATH.read_text()
        for token in (
            "-DCG_PHYSICAL_PAGE_PRODUCT_ONLY",
            "-DNUM_TILES_PER_CORE=8",
            "--maa_num_tiles_per_core=8",
            "num_tiles_per_core=8",
            "logical_tile_page_scheduler=false",
        ):
            self.assertIn(token, runner)
        self.assertNotIn("-DNUM_TILES_PER_CORE=10", runner)
        self.assertNotIn("--maa_num_tiles_per_core=10", runner)
        self.assertNotIn("--maa_logical_tile_page_scheduler", runner)

    def test_small_mode_keeps_exact_accepted_predecessor_comparison(self):
        runner = RUNNER_PATH.read_text()
        small_start = runner.index("  small)")
        small_case = runner[small_start : runner.index("    ;;", small_start)]
        for token in (
            "cg_na=1024",
            "/data1/nier/dx100-runs/2026-08-24-cg-page-product-fusion-small-08a7b267-r2",
            "4364635c504c738fcc6026d0dd10351418cd3bc458938082915fda1ee3bd0d32",
            "default_accepted_ticks=6348682603",
            "comparison_contract=accepted_predecessor",
            "trace_mode=enabled_small",
        ):
            self.assertIn(token, small_case)

    def test_full_mode_is_exact_reference_correctness_only(self):
        runner = RUNNER_PATH.read_text()
        full_start = runner.index("  full)")
        full_case = runner[full_start : runner.index("    ;;", full_start)]
        for token in (
            "cg_na=150000",
            "/data1/nier/dx100-runs/2026-08-11-cg-bounded-789cc703-full-v8/bounded4_cached/run.log",
            "0fe931685c37695bc51c74288c67f1494a0c91a723f8e831efa0ac2a7515441c",
            "comparison_contract=correctness_only",
            "trace_mode=disabled_full",
        ):
            self.assertIn(token, full_case)
        self.assertNotIn("accepted", full_case)
        self.assertIn(
            "if [[ $comparison_contract == accepted_predecessor ]]", runner
        )

    def test_full_trace_is_disabled_and_optional_hashing_is_exact(self):
        runner = RUNNER_PATH.read_text()
        for token in (
            "restore_debug_args=()",
            "if [[ $trace_mode == enabled_small ]]",
            "[[ ! -e $trace ]]",
            'result_paths+=("$trace")',
            "trace_mode=%s",
            "logical_page_action_basis=guest_terminal_and_scheduler_disabled",
        ):
            self.assertIn(token, runner)
        self.assertEqual(runner.count("--debug-flags=MAAVirtualTrace"), 1)
        self.assertEqual(
            runner.count("--debug-file=logical_page_trace.log"), 1
        )
        self.assertNotIn('"$out/run/config.ini" "$trace"', runner)

    def test_runner_requires_exact_mechanism_and_provenance_closure(self):
        runner = RUNNER_PATH.read_text()
        for token in (
            "CG_REFERENCE_LOG",
            "CG_FINGERPRINT",
            "physical_page_product_soa_jit",
            "logical_page_native_dispatch",
            "logical_page_native_complete",
            "logical_page_retire",
            "STR_PublishTerminals",
            "IND_SoaJitTerminalCompletions",
            "IND_SoaJitFallbacks",
            "IND_SoaJitOpenContexts",
            "q_spmv_eligible_windows",
            "q_spmv_routed_windows",
            "residual_spmv_eligible_windows",
            "residual_spmv_routed_windows",
            "source_status.after",
            "reference_sha256",
            "CG_ACCEPTED_ROOT",
            "accepted_simTicks",
            "performance_direction_vs_accepted",
            "comparison_contract=correctness_only",
            "expected_publish_pages=$((windows * 8))",
            "publisher_pages_per_window=8",
            "publisher_issue_accept_response=",
            "publisher_terminals=",
            'find "$out" -mindepth 1 -print -quit',
            "refusing nonempty output",
        ):
            self.assertIn(token, runner)
        self.assertNotIn("native16", runner)
        self.assertNotIn("native4", runner)
        self.assertNotIn("timeout_command", runner)
        self.assertNotRegex(runner, r"(?m)^\s*timeout(?:\s|$)")
        self.assertIn("$q_routed -eq $q_eligible", runner)
        self.assertIn("$residual_routed -eq $residual_eligible", runner)
        self.assertIn("$logical_alus -eq 0", runner)
        self.assertIn("$physical_alus -eq $((windows * 4))", runner)

    def test_runner_uses_index_stable_numerical_fingerprint_criterion(self):
        runner = RUNNER_PATH.read_text()
        for token in (
            "exact_quantized_hashes:x_q5,x_q6,z_q5,z_q6",
            "nonfinite_x=0,nonfinite_z=0,result=PASS",
            "x_sum:1e-8",
            "x_norm_sq:1e-8",
            "z_sum:1e-8",
            "z_norm_sq:1e-8",
            "rnorm:1e-3",
            "zeta:1e-10",
            "reference_fingerprint=",
            "candidate_fingerprint=",
            "fingerprint_relative_deltas=",
        ):
            self.assertIn(token, runner)
        self.assertNotIn('grep -Fxc "$reference_line"', runner)


if __name__ == "__main__":
    unittest.main()
