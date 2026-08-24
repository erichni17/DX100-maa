#!/usr/bin/env python3
"""Contract checks for the candidate-only full SSSP S22 hybrid gate."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_sssp_old_result_hybrid_full.sh"
SOURCE = ROOT / "benchmarks/gapbs/src/sssp.cc"


class SsspOldResultHybridFullContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = RUNNER.read_text(encoding="utf-8")
        cls.source = SOURCE.read_text(encoding="utf-8")

    def test_runner_pins_full_input_oracle_and_native_context(self) -> None:
        for token in (
            "serialized_graph_22.wsg",
            "23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc",
            "native16_ticks=758524789379",
            "vertices=4194304 reached=4194304",
            "distance_sum=569278395",
            "hash_a=aaf3a6a5d4662d36",
            "hash_b=9ffcf4962b364007",
            "native_checkpoint_reused=false",
        ):
            self.assertIn(token, self.runner)

    def test_runner_builds_candidate_and_owns_new_checkpoint(self) -> None:
        for token in (
            "-DSSSP_OLD_RESULT_HYBRID=1",
            "-DSSSP_FP_ENABLE=1",
            'guest="$out/bin/sssp_maa_2G_old_result_hybrid_fp"',
            "--cpu-type AtomicSimpleCPU",
            "--max-checkpoints=1",
            'checkpoint-dir="$out/checkpoint"',
            "candidate_checkpoint=true",
            "checkpoint.files.before.sha256",
            "checkpoint.files.after.sha256",
            "artifacts.before.sha256",
            "artifacts.after.sha256",
            'cmp -s "$out/input/artifacts.before.sha256"',
        ):
            self.assertIn(token, self.runner)
        self.assertNotIn("--checkpoint-dir=/data1/nier/dx100-runs/2026-07", self.runner)

    def test_selected_bounded_configuration_is_explicit(self) -> None:
        for token in (
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_num_offset_table_entries=16384",
            "--maa_num_offset_table_epoch_entries=16384",
            "--maa_num_initial_row_table_slices=32",
            "--maa_soa_jit_old_result_pressure_policy=densest",
            "--maa_soa_jit_old_result_partial_credits=4",
            "--maa_soa_jit_active_contexts=8",
            "--maa_soa_jit_active_value_owners=64",
            "--maa_soa_jit_pre_a_value_lookahead",
            "--maa_soa_jit_value_cache_enable",
            "old_result_payload_bytes=512",
            "old_result_object_bytes=1128",
            "pressure_control_bits_per_unit=3",
        ):
            self.assertIn(token, self.runner)

    def test_full_run_is_trace_free_candidate_only_and_uncapped(self) -> None:
        self.assertNotIn("--debug-flags", self.runner)
        self.assertNotIn("--debug-file", self.runner)
        self.assertNotIn("timeout ", self.runner)
        self.assertIn("candidate_only=1", self.runner)
        self.assertIn("native_arms=0", self.runner)
        self.assertIn("wall_timeout=none", self.runner)
        self.assertIn("trace_mode=disabled_full", self.runner)
        self.assertNotIn("sssp_maa_16K --options", self.runner)

    def test_gate_fails_closed_on_terminal_correctness_and_ledgers(self) -> None:
        for token in (
            "user interrupt",
            "m5_exit instruction encountered",
            "SSSP_OLD_RESULT_HYBRID_TERMINAL",
            "counts_close=1",
            "IND_SoaJitInstructions",
            "IND_SoaJitTerminalCompletions",
            "IND_SoaJitOldResultWriteIssues",
            "IND_SoaJitOldResultWriteResponses",
            "IND_SoaJitAReadResponses",
            "IND_SoaJitAWriteResponses",
            "eligible_windows",
            "routed_windows",
            "gate.complete",
            "result_sha256.txt",
        ):
            self.assertIn(token, self.runner)

    def test_source_keeps_opt_in_full_and_tail_paths(self) -> None:
        self.assertIn("#ifdef SSSP_OLD_RESULT_HYBRID", self.source)
        self.assertIn("SSSP_OLD_RESULT_HYBRID_TERMINAL", self.source)
        self.assertIn("hybrid_legacy_words", self.source)
        self.assertIn("host_spd_reads=0", self.source)


if __name__ == "__main__":
    unittest.main()
