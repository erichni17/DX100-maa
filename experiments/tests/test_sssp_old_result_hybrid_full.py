import pathlib
import re
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_sssp_old_result_hybrid_full.sh"
SMALL_RUNNER = ROOT / "experiments/scripts/run_sssp_old_result_hybrid_small.sh"
SOURCE = ROOT / "benchmarks/gapbs/src/sssp.cc"


class SsspOldResultHybridFullContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = RUNNER.read_text()
        cls.small_runner = SMALL_RUNNER.read_text()
        cls.source = SOURCE.read_text()

    def test_is_a_new_candidate_only_full_runner(self):
        self.assertIn("full_graph=true", self.runner)
        self.assertIn("native_arms=0", self.runner)
        self.assertIn("native_checkpoint_execution=not_reused", self.runner)
        self.assertIn("native_guest_execution=not_reused", self.runner)
        self.assertNotIn("run_native", self.runner)
        self.assertNotIn("sssp_maa_16K", self.runner)
        self.assertIn("full_graph=false", self.small_runner)
        self.assertNotIn("serialized_graph_22.wsg", self.small_runner)

    def test_freezes_exact_external_s22_reference(self):
        for exact in (
            "/data1/nier/worktrees/DX100-full-tile-sweep-20260720/"
            "benchmarks/gapbs/serialized_graph_22.wsg",
            "23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc",
            "external_graph_bytes=1090514493",
            "native_first_roi_ticks=758524789379",
            "candidate_options=-f INPUT -n 1 -v",
            "sssp_s22_t16384_m2GB_gem5.opt.ovl_base_sha256_"
            "1ff4a396b98d6c838f695c4cbd631ca16e7ed12407365f17707bcf6df93e1343",
            "vertices=4194304 reached=4194304 unreachable=0",
            "distance_sum=569278395 max_distance=258",
            "hash_a=aaf3a6a5d4662d36 hash_b=9ffcf4962b364007",
            "triangle_violations=0 missing_predecessors=0",
            "nonpositive_weights=0 negative_distances=0 result=PASS",
        ):
            self.assertIn(exact, self.runner)
        self.assertIn('grep -Fxc "$oracle"', self.runner)

    def test_pins_archived_candidate_binary_and_selected_mapping(self):
        expected_sha = (
            "1e079112469892681d661925db09ccfbc845d1a2ce45c79e1d9a4902c19a9863"
        )
        self.assertGreaterEqual(self.runner.count(expected_sha), 2)
        self.assertIn("SSSP_CANDIDATE_GEM5", self.runner)
        self.assertIn("SSSP_CANDIDATE_GEM5_SHA256", self.runner)
        self.assertIn("default_gem5", self.runner)
        self.assertIn("default_gem5_sha256", self.runner)
        self.assertIn("candidate_gem5_path", self.runner)
        self.assertIn("candidate_gem5_sha256", self.runner)
        for flag in (
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_num_offset_table_entries=16384",
            "--maa_num_offset_table_epoch_entries=16384",
            "--maa_num_initial_row_table_slices=32",
            "--maa_num_indirect_units_per_maa=4",
            "--maa_soa_jit_old_result_pressure_policy=densest",
            "--maa_soa_jit_old_result_partial_credits=4",
            "--maa_soa_jit_active_contexts=8",
            "--maa_soa_jit_value_cache_enable",
            "--maa_soa_jit_active_value_owners=64",
            "--maa_soa_jit_pre_a_value_lookahead",
        ):
            self.assertEqual(self.runner.count(flag), 1)
        for resolved in (
            "num_tile_elements=16384",
            "physical_tile_elements=4096",
            "num_initial_row_table_slices=32",
            "soa_jit_old_result_pressure_policy=densest",
            "soa_jit_old_result_partial_credits=4",
            "soa_jit_active_contexts=8",
            "soa_jit_active_value_owners=64",
            "soa_jit_value_cache_enable=true",
            "soa_jit_pre_a_value_lookahead=true",
        ):
            self.assertIn(resolved, self.runner)

    def test_candidate_override_is_frozen_in_manifest_and_ledgers(self):
        self.assertIn('gem5=$(realpath -m "$gem5")', self.runner)
        self.assertIn(
            '$(manifest_value "$manifest" candidate_gem5_path) == "$gem5"',
            self.runner,
        )
        self.assertIn(
            '$(manifest_value "$manifest" candidate_gem5_sha256) == "$gem5_sha256"',
            self.runner,
        )
        self.assertGreaterEqual(
            self.runner.count('grep -Fqx "$gem5_sha256  $gem5"'), 2
        )

    def test_post_exit_validation_adopts_frozen_candidate_without_env(self):
        self.assertIn("adopt_validation_manifest", self.runner)
        self.assertIn("-z ${SSSP_CANDIDATE_GEM5+x}", self.runner)
        self.assertIn("-z ${SSSP_CANDIDATE_GEM5_SHA256+x}", self.runner)
        self.assertIn("-z ${SSSP_APERTURE_CANDIDATE_GATE+x}", self.runner)
        self.assertIn(
            'gem5=$(manifest_value "$manifest" candidate_gem5_path)',
            self.runner,
        )
        self.assertIn(
            'gem5_sha256=$(manifest_value "$manifest" candidate_gem5_sha256)',
            self.runner,
        )
        self.assertIn('"$manifest" aperture_candidate_gate', self.runner)
        self.assertLess(
            self.runner.index('adopt_validation_manifest "$validation_out"'),
            self.runner.index('validate_callback "$validation_out"'),
        )

    def test_aperture_gate_is_opt_in_and_rejects_real_out_of_range_accesses(
        self,
    ):
        self.assertIn("SSSP_APERTURE_CANDIDATE_GATE:-false", self.runner)
        self.assertIn("require_boolean", self.runner)
        self.assertIn("aperture_candidate_gate", self.runner)
        self.assertIn("cpu_spd_boundary_prefetch_drops", self.runner)
        self.assertIn("cpu_spd_out_of_range_rejections", self.runner)
        self.assertIn("stat_sum_optional_zero", self.runner)
        self.assertIn("found ? sum : 0", self.runner)
        self.assertIn("aperture_rejections == 0", self.runner)
        self.assertNotIn("boundary_drops > 0", self.runner)
        self.assertIn("record_aperture_stats", self.runner)
        self.assertIn(
            "first_window_cpu_spd_boundary_prefetch_drops", self.runner
        )
        self.assertIn(
            "first_window_cpu_spd_out_of_range_rejections", self.runner
        )

    def test_is_trace_free_unbounded_and_records_exact_statuses(self):
        self.assertIn("trace=false", self.runner)
        self.assertIn("wall_timeout=none", self.runner)
        self.assertNotIn("--debug-flags", self.runner)
        self.assertNotRegex(self.runner, r"(^|[;&|]\s*)timeout\s")
        self.assertIn("printf '%s\\n' \"$checkpoint_rc\"", self.runner)
        self.assertIn("printf '%s\\n' \"$restore_rc\"", self.runner)
        self.assertIn("write_wrapper_status", self.runner)
        self.assertIn("grep -Fqx 'exit_code=0'", self.runner)
        self.assertIn("user[ -]?interrupt|interrupt received", self.runner)

    def test_freezes_candidate_input_guest_and_checkpoint_before_after(self):
        self.assertIn("cp --reflink=auto", self.runner)
        self.assertIn('chmod 0444 "$graph"', self.runner)
        self.assertIn("candidate_guest_sha256", self.runner)
        self.assertIn("coherent_fallback_helper_path", self.runner)
        self.assertIn("coherent_fallback_helper_sha256", self.runner)
        self.assertGreaterEqual(self.runner.count('"$helper_file"'), 4)
        self.assertIn("checkpoint.before.files.sha256", self.runner)
        self.assertIn("checkpoint.after.files.sha256", self.runner)
        self.assertIn("checkpoint.callback.files.sha256", self.runner)
        self.assertIn("artifacts.before.sha256", self.runner)
        self.assertIn("artifacts.after.sha256", self.runner)
        self.assertGreaterEqual(self.runner.count("cmp -s"), 3)
        self.assertIn(
            'find "$out/checkpoint" -type f -exec chmod 0444', self.runner
        )

    def test_requires_one_certificate_exit_roi_and_final_stats(self):
        self.assertIn('grep -Fxc "$oracle"', self.runner)
        self.assertIn("m5_exit instruction encountered", self.runner)
        self.assertIn("grep -Fxc 'ROI End!!!'", self.runner)
        self.assertIn("Begin Simulation Statistics", self.runner)
        self.assertIn("End Simulation Statistics", self.runner)
        self.assertIn("${#sim_ticks[@]} -eq 2", self.runner)
        self.assertIn("sim_ticks[1] >= sim_ticks[0]", self.runner)

    def test_balances_soa_a_old_result_and_terminal_count(self):
        for counter in (
            "IND_SoaJitPredicateLineReads",
            "IND_SoaJitPredicateLineResponses",
            "IND_SoaJitValueReadIssues",
            "IND_SoaJitValueReadResponses",
            "IND_SoaJitValueFills",
            "IND_SoaJitValueCachedResponses",
            "IND_SoaJitLookaheadIssues",
            "IND_SoaJitLookaheadResponses",
            "IND_SoaJitAReadIssues",
            "IND_SoaJitAReadResponses",
            "IND_SoaJitAWriteIssues",
            "IND_SoaJitAWriteResponses",
            "IND_SoaJitOldResultCaptures",
            "IND_SoaJitOldResultWriteIssues",
            "IND_SoaJitOldResultWriteResponses",
            "IND_SoaJitTerminalCompletions",
        ):
            self.assertIn(counter, self.runner)
        self.assertIn(
            "instructions == routed && terminals == routed", self.runner
        )
        self.assertIn("old_issues == old_responses", self.runner)
        self.assertIn("a_read_issues == a_write_issues", self.runner)

    def test_preserves_and_reports_tails_and_fallbacks(self):
        self.assertIn("tails_and_fallbacks=preserved", self.runner)
        self.assertIn("routed > 0 && routed <= eligible", self.runner)
        self.assertEqual(self.runner.count("routed == eligible"), 1)
        self.assertIn("legacy_words", self.runner)
        self.assertIn(
            "eligible_subset_routed_fallbacks_preserved", self.runner
        )
        self.assertIn("if (!route_page)", self.source)
        self.assertIn(
            "sssp_hybrid_legacy_words[tid] += curr_size", self.source
        )
        self.assertIn("routed_windows <= eligible_windows", self.source)
        for field in (
            "fallback_pages",
            "fallback_publication_issue_pages",
            "fallback_publication_response_pages",
            "fallback_publication_words",
            "fallback_consumed_words",
            "predicate_restore_words",
            "coherent_tail_batches",
            "coherent_tail_words",
            "host_spd_reads",
            "illegal_host_spd_line_starts",
            "response_closure",
        ):
            self.assertIn(field, self.runner)
        self.assertIn("fallback_pages > 0", self.runner)
        self.assertIn(
            "fallback_issue_pages == fallback_pages * 3", self.runner
        )
        self.assertIn("legacy_words == fallback_consumed", self.runner)

    def test_validate_mode_fails_closed_without_wrapper_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [str(RUNNER), "--validate", tmp],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)

    def test_no_shell_assignment_reuses_native_artifacts_for_execution(self):
        executable_assignments = re.findall(
            r"^(?:guest|graph|gem5)=([^\n]+)$", self.runner, re.MULTILINE
        )
        joined = "\n".join(executable_assignments)
        self.assertNotIn("native_out", joined)
        self.assertNotIn("sssp_maa_16K", joined)

    def test_small_runner_has_exact_full_restore_cache_and_maa_surface(self):
        # The small graph changes coverage only; it must not dilute the cache,
        # memory, aperture, or old-result configuration under test.
        exact_flags = (
            "--l1d_size=32kB",
            "--l1d_assoc=8",
            "--l1d-hwp-type=StridePrefetcher",
            "--l1d_mshrs=16",
            "--l1d_write_buffers=8",
            "--l1i_size=32kB",
            "--l1i_assoc=8",
            "--l1i-hwp-type=StridePrefetcher",
            "--l1i_mshrs=16",
            "--l1i_write_buffers=8",
            "--l2_size=256kB",
            "--l2_assoc=4",
            "--l2-hwp-type=StridePrefetcher",
            "--l2_mshrs=32",
            "--l2_write_buffers=16",
            "--l3_size=8MB",
            "--l3_assoc=16",
            "--l3_mshrs=256",
            "--l3_write_buffers=128",
            "--l3_ports=4",
            "--maa_l2_uncacheable",
            "--maa_l3_uncacheable",
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_soa_jit_old_result_pressure_policy=densest",
            "--maa_soa_jit_old_result_partial_credits=4",
            "--maa_soa_jit_active_contexts=8",
            "--maa_soa_jit_active_value_owners=64",
            "--mem-type Ramulator2",
            "--mem-channels=2",
            "--maa_ncbus_width=32",
        )
        for flag in exact_flags:
            self.assertEqual(self.runner.count(flag), 1)
            self.assertEqual(self.small_runner.count(flag), 1)
        self.assertIn("artifacts.before.sha256", self.small_runner)
        self.assertIn("artifacts.after.sha256", self.small_runner)
        self.assertIn("frozen_ramulator_sha256", self.small_runner)
        self.assertIn("resolved_ramulator", self.small_runner)
        self.assertIn("ramulator_library_sha256", self.small_runner)
        self.assertEqual(
            self.small_runner.count('"$frozen_ramulator" "$guest"'), 2
        )
        self.assertIn(
            "cpu_spd_boundary_prefetch_drops=reported_not_forced",
            self.small_runner,
        )
        self.assertIn(
            "cpu_spd_out_of_range_rejections=0_required", self.small_runner
        )
        self.assertIn("aperture_rejections -eq 0", self.small_runner)
        self.assertIn("stat_sum_optional_zero", self.small_runner)
        self.assertIn("found ? sum : 0", self.small_runner)
        self.assertNotIn("run_native", self.small_runner)
        self.assertIn('printf "%.0f\\n", sum', self.small_runner)
        self.assertNotIn('printf "%.0f\\\\n", sum', self.small_runner)
        for runner in (self.runner, self.small_runner):
            self.assertIn('$1 == "system.maa." suffix', runner)
            self.assertIn('$1 ~ ("_" suffix "$")', runner)


if __name__ == "__main__":
    unittest.main()
