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

    def test_one_checkpoint_is_shared_by_cpu_and_response_bearing_arms(self):
        self.assertIn("control.selector", self.runner)
        self.assertIn("treatment.selector", self.runner)
        self.assertIn("residual_soa_jit_response_bearing", self.runner)
        self.assertIn(
            "checkpoint is identical",
            self.runner,
        )
        self.assertIn(
            "comparison=one_guest_one_checkpoint_response_bearing_publisher_only",
            self.runner,
        )
        self.assertIn('cmp -s "$out/checkpoint.files.sha256"', self.runner)
        self.assertIn('--checkpoint-dir "$out/checkpoint"', self.runner)

    def test_guest_and_geometry_are_identical(self):
        for token in (
            "-DCG_NA=1024",
            "-DTILE_SIZE=16384",
            "-DMAA_CONSUMER_TILE_SIZE=4096",
            "--maa_num_offset_table_entries=16384",
            "--maa_num_offset_table_epoch_entries=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_soa_jit_predicate_active_credits=16",
            "--maa_soa_jit_active_value_owners=32",
        ):
            self.assertIn(token, self.runner)

    def test_treatment_is_only_the_post_checkpoint_publisher_selector(self):
        self.assertIn("usage: $0 GEM5_BIN OUTDIR", self.runner)
        self.assertIn("selector=$control_selector", self.runner)
        self.assertIn("selector=$treatment_selector", self.runner)
        self.assertIn("--maa_soa_jit_value_prefetch_credits=0", self.runner)
        self.assertNotIn("treatment_flags", self.runner)

    def test_config_comparison_only_normalizes_the_verified_arm_path(self):
        self.assertIn("normalized_config_sha()", self.runner)
        self.assertIn(
            "expected exactly one arm selector path in config.ini", self.runner
        )
        self.assertIn("__CG_ARM_SELECTOR_PATH__", self.runner)
        self.assertIn("checkpoint.selector.sha256.before", self.runner)
        self.assertGreaterEqual(
            self.runner.count(
                'cmp -s "$out/input/checkpoint.selector.sha256.before"'
            ),
            2,
        )

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
            "STR_PublishIssues",
            "STR_PublishWriteResponses",
            "STR_PublishTerminals",
            "STR_PublishOverlapIssues",
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

    def test_rejects_a_slower_or_non_publishing_candidate(self):
        self.assertIn("response-bearing candidate is slower", self.runner)
        self.assertIn("[[ ${publish_issues[$control]} -eq 0", self.runner)
        self.assertIn(
            "[[ ${ticks[$treatment]} -le ${ticks[$control]} ]]", self.runner
        )
        self.assertIn("decision=PERFORMANCE_PROMOTABLE", self.runner)


if __name__ == "__main__":
    unittest.main()
