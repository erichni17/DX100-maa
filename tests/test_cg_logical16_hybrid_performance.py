#!/usr/bin/env python3
"""Static contract for the exact CG logical-16 hybrid performance gate."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CGLogical16HybridPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = (
            ROOT / "experiments/scripts/run_cg_logical16_hybrid_performance.sh"
        ).read_text()

    def test_one_selector_and_one_checkpoint_are_shared_by_every_arm(self):
        self.assertIn("residual_soa_jit.selector", self.runner)
        self.assertIn(
            "checkpoint is selector-identical and treatment-neutral",
            self.runner,
        )
        self.assertIn(
            "comparison=one_guest_one_selector_one_checkpoint_treatment_flags_only",
            self.runner,
        )
        self.assertIn('cmp -s "$out/checkpoint.files.sha256"', self.runner)
        self.assertIn('--checkpoint-dir "$out/checkpoint"', self.runner)

    def test_guest_and_geometry_are_identical(self):
        for token in (
            '-DCG_NA="$cg_na"',
            "-DTILE_SIZE=16384",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
            "--maa_num_offset_table_entries=16384",
            "--maa_num_offset_table_epoch_entries=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_soa_jit_predicate_active_credits=16",
            "--maa_soa_jit_active_value_owners=32",
        ):
            self.assertIn(token, self.runner)

    def test_workload_size_is_explicit_and_positive(self):
        self.assertIn("cg_na=${CG_HYBRID_NA:-1024}", self.runner)
        self.assertIn("CG_HYBRID_NA must be a positive integer", self.runner)
        self.assertIn("printf 'cg_na=%s", self.runner)

    def test_treatment_is_explicit_and_control_has_no_extra_flags(self):
        self.assertIn(
            "usage: $0 GEM5_BIN OUTDIR [TREATMENT_GEM5_FLAG ...]", self.runner
        )
        self.assertIn(
            "at least one explicit simulator-only treatment flag is required",
            self.runner,
        )
        self.assertIn(
            '[[ $arm == control ]] || command+=("${treatment_flags[@]}")',
            self.runner,
        )
        self.assertIn("--maa_soa_jit_value_prefetch_credits=0", self.runner)
        self.assertIn("normalize_config", self.runner)
        self.assertIn("treatment_config_lines", self.runner)

    def test_cli_treatment_names_match_config_ini_parameter_names(self):
        self.assertIn("resolved=${resolved#maa_}", self.runner)
        self.assertIn(
            "[[ $resolved == 'soa_jit_pre_a_value_lookahead=true' ]]",
            self.runner,
        )

    def test_config_identity_ignores_only_declared_and_run_local_deltas(self):
        self.assertIn('print "host_paths=<RUN>"', self.runner)
        self.assertIn('print keys[i] "=<TREATMENT>"', self.runner)

    def test_validates_exact_outputs_provenance_and_mechanism_ledgers(self):
        for token in (
            "CG_FINGERPRINT",
            "CG_LOGICAL16_RMW_TERMINAL",
            "artifact_sha256.txt",
            "checkpoint.identity.sha256",
            "IND_SoaJitValueReadIssues",
            "IND_SoaJitValueFills",
            "IND_SoaJitAReadIssues",
            "IND_SoaJitAWriteResponses",
            "simTicks",
            "provenance.txt",
        ):
            self.assertIn(token, self.runner)

    def test_at_least_two_deterministic_replicas_are_required(self):
        self.assertIn("replicas=${CG_HYBRID_REPLICAS:-2}", self.runner)
        self.assertIn("CG_HYBRID_REPLICAS must be at least two", self.runner)
        self.assertIn(
            "for ((replica = 1; replica <= replicas; replica++))", self.runner
        )

    def test_restores_are_parallel_and_timeout_is_optional(self):
        self.assertIn("CG_HYBRID_TIMEOUT_SECONDS:-0", self.runner)
        self.assertIn(
            'timeout_command=(timeout "$timeout_seconds")', self.runner
        )
        self.assertIn('run_arm control "$replica" &', self.runner)
        self.assertIn('run_arm treatment "$replica" &', self.runner)
        self.assertIn(
            'for pid in "${pids[@]}"; do wait "$pid"; done', self.runner
        )

    def test_run_directory_does_not_expand_an_unbound_local(self):
        self.assertIn('local name="${arm}_r${replica}"\n', self.runner)
        self.assertIn('local run="$out/runs/$name"\n', self.runner)
        self.assertNotIn(
            'local name="${arm}_r${replica}" run="$out/runs/$name"',
            self.runner,
        )

    def test_stat_sum_emits_a_numeric_line_not_a_literal_escape(self):
        self.assertIn('printf "%.0f\\n", sum', self.runner)
        self.assertNotIn('printf "%.0f\\\\n", sum', self.runner)

    def test_decision_uses_real_newlines(self):
        self.assertIn("replica_%s_control_simTicks=%s\\n", self.runner)
        self.assertNotIn("replica_%s_control_simTicks=%s\\\\n", self.runner)


if __name__ == "__main__":
    unittest.main()
