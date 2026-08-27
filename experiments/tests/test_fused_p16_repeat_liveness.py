import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "experiments/scripts/run_fused_p16_repeat_liveness.py"
GUEST_PATH = ROOT / "benchmarks/API/test_fused_p16_repeat_liveness.cpp"
RUNNER_TEXT = RUNNER_PATH.read_text()
GUEST_TEXT = GUEST_PATH.read_text()
SPEC = importlib.util.spec_from_file_location("repeat_liveness", RUNNER_PATH)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def operation_values() -> dict[str, int]:
    values = {name: 0 for name in runner.REQUIRED_STATS}
    values.update(
        {
            "simTicks": 100,
            "IND_FusedP16Operations": 1,
            "IND_FusedP16Epochs": 1,
            "IND_FusedP16SourceOrdinals": runner.WORDS,
            "IND_FusedP16CoefficientReadIssues": 1024,
            "IND_FusedP16CoefficientReadResponses": 1024,
            "IND_FusedP16CoefficientFills": 1024,
            "IND_FusedP16CoefficientDeliveries": runner.WORDS,
            "IND_FusedP16MulAccepts": runner.WORDS,
            "IND_FusedP16MulCompletions": runner.WORDS,
            "IND_FusedP16ProductInsertions": runner.WORDS,
            "IND_FusedP16ProductWriteCompletions": runner.WORDS,
            "IND_SoaJitInstructions": 1,
            "IND_SoaJitTerminalCompletions": 1,
            "IND_SoaJitSelected": runner.WORDS,
            "IND_SoaJitAliasesApplied": runner.WORDS,
            "IND_SoaJitValueReadIssues": 1024,
            "IND_SoaJitValueReadResponses": 1024,
            "IND_SoaJitValueFills": 1024,
            "IND_SoaJitValueHits": runner.WORDS - 1024,
            "IND_SoaJitValueDeliveries": runner.WORDS,
            "IND_SoaJitAReadIssues": 1024,
            "IND_SoaJitAReadResponses": 1024,
            "IND_SoaJitAWriteIssues": 1024,
            "IND_SoaJitAWriteResponses": 1024,
            "IND_SoaJitPageFedOperations": 1,
            "IND_SoaJitPageFedAdmitCommands": runner.PAGES,
            "IND_SoaJitPageFedCloseCommands": 1,
            "IND_SoaJitPageFedCommandResponses": runner.PAGES + 1,
            "IND_SoaJitPageFedAdmittedWords": runner.WORDS,
            "IND_SoaJitPageFedSpdIndexReads": runner.WORDS,
            "IND_SoaJitPageFedRowWrites": runner.WORDS,
        }
    )
    return values


def stats_text(sections: list[dict[str, int]]) -> str:
    records = []
    for section in sections:
        records.append("---------- Begin Simulation Statistics ----------")
        for name, value in section.items():
            prefix = "" if name == "simTicks" else "system.maa.I0_"
            records.append(f"{prefix}{name} {value}")
        records.append("---------- End Simulation Statistics ----------")
    return "\n".join(records) + "\n"


class FusedP16RepeatLivenessContract(unittest.TestCase):
    def test_guest_reuses_one_token_with_distinct_generations_in_one_roi(self):
        for token in (
            "MaxOperations = 64",
            "operations != 16 && operations != 32 && operations != 64",
            "m5_checkpoint(0, 0);",
            "m5_work_begin(0, 0);",
            "maa_indirect_load_virtual_index_product_fp32",
            "wait_ready(ids.fusedCompletion);",
            "maa_indirect_rmw_vector_soa_jit_page_fed_open<float>",
            "maa_soa_jit_page_fed_close(generation)",
            "producer_generation=",
            "q_generation=",
            "FUSED_P16_REPEAT_PROGRESS",
            "m5_dump_reset_stats(0, 0);",
        ):
            self.assertIn(token, GUEST_TEXT)
        self.assertEqual(GUEST_TEXT.count("m5_work_begin(0, 0);"), 1)
        self.assertEqual(GUEST_TEXT.count("m5_work_end(0, 0);"), 1)
        self.assertLess(
            GUEST_TEXT.index("m5_checkpoint(0, 0);"),
            GUEST_TEXT.index("readOperations(argv[2])"),
        )

    def test_guest_has_distinct_deterministic_inputs_and_exact_hashes(self):
        for token in (
            "operation * 4096U",
            "operation * 16384U",
            "input_hash=",
            "reference_hash=",
            "product_hash=",
            "q_hash=",
            "referenceHash != productHash",
            "productHash != qHash",
            "sentinels != 0",
        ):
            self.assertIn(token, GUEST_TEXT)

    def test_runner_is_exact_serial_fail_fast_and_has_no_trace(self):
        self.assertEqual(runner.CASES, (16, 32, 64))
        self.assertIn("for repeats in CASES:", RUNNER_TEXT)
        self.assertLess(
            RUNNER_TEXT.index("run_logged(command"),
            RUNNER_TEXT.index("parse_case(case, repeats)"),
        )
        self.assertNotIn("--debug-flags", RUNNER_TEXT)
        self.assertNotIn("timeout=", RUNNER_TEXT)
        self.assertIn('"timeouts": 0', RUNNER_TEXT)
        self.assertIn('"application_runs": 0', RUNNER_TEXT)
        for knob in (
            "--maa_num_indirect_units_per_maa=1",
            "--maa_num_tile_elements=16384",
            "--maa_physical_tile_elements=4096",
            "--maa_virtual_combine_slots=16",
            "--maa_virtual_response_slots=8",
            "--maa_virtual_max_outstanding_writes=32",
            "--maa_soa_jit_value_cache_enable",
            "--maa_soa_jit_active_value_owners=32",
            "--maa_soa_jit_value_prefetch_credits=0",
        ):
            self.assertIn(knob, RUNNER_TEXT)

    def test_each_stats_window_requires_all_ledgers_and_explicit_zeros(self):
        values = operation_values()
        self.assertIs(runner.require_operation_stats(values), values)
        for name in (
            "IND_FusedP16Operations",
            "IND_FusedP16Epochs",
            "IND_FusedP16CoefficientReadIssues",
            "IND_FusedP16CoefficientReadResponses",
            "IND_FusedP16CoefficientFills",
            "IND_FusedP16CoefficientDeliveries",
            "IND_FusedP16MulAccepts",
            "IND_FusedP16MulCompletions",
            "IND_FusedP16ProductInsertions",
            "IND_FusedP16ProductWriteCompletions",
            "IND_SoaJitTerminalCompletions",
            "IND_SoaJitPageFedCommandResponses",
        ):
            self.assertIn(name, runner.POSITIVE_STATS)
        for name in (
            "IND_FusedP16EpochDrains",
            "IND_FusedP16Fallbacks",
            "IND_FusedP16PublisherLines",
            "IND_FusedP16VirtualPBytes",
            "IND_NumOTEpochDrain",
            "IND_SoaJitEpochDrains",
            "IND_BoundedGlobalMergeFallbacks",
            "STR_PublishIssues",
            "STR_PublishWriteResponses",
        ):
            self.assertIn(name, runner.ZERO_STATS)

    def test_stats_sections_reject_removed_renamed_or_partial_operation(self):
        values = operation_values()
        empty = {name: 0 for name in runner.REQUIRED_STATS}
        with tempfile.TemporaryDirectory() as tmp:
            stats = Path(tmp) / "stats.txt"
            content = stats_text([values, values, empty]).replace(
                "---------- End Simulation Statistics ----------",
                "system.unrelated_ratio inf\n"
                "---------- End Simulation Statistics ----------",
            )
            stats.write_text(content)
            self.assertEqual(len(runner.operation_sections(stats, 2)), 2)

            removed = values.copy()
            removed.pop("IND_FusedP16Fallbacks")
            stats.write_text(stats_text([removed]))
            with self.assertRaisesRegex(RuntimeError, "absent or renamed"):
                runner.operation_sections(stats, 1)

            renamed = values.copy()
            renamed["IND_FusedP16Fallback"] = renamed.pop(
                "IND_FusedP16Fallbacks"
            )
            stats.write_text(stats_text([renamed]))
            with self.assertRaisesRegex(RuntimeError, "absent or renamed"):
                runner.operation_sections(stats, 1)

            partial = values.copy()
            partial["IND_FusedP16Operations"] = 2
            stats.write_text(stats_text([values, partial]))
            with self.assertRaisesRegex(
                RuntimeError, "partial or accumulated"
            ):
                runner.operation_sections(stats, 1)

    def test_terminal_state_contract_and_scaling_classification_are_explicit(
        self,
    ):
        for token in (
            '"combiner_empty"',
            '"coalescer_generation_cleared"',
            '"coalescer_invariants"',
            '"alu_exact_retirement"',
            '"alu_returns_idle"',
            '"fresh_internal_generation"',
            '"coalescer_combiner_alu_empty_per_operation": True',
        ):
            self.assertIn(token, RUNNER_TEXT)
        cases = {
            str(size): {
                "total_simTicks": size * 100,
                "operation_stats": [{"simTicks": 100}] * size,
            }
            for size in runner.CASES
        }
        scaling = runner.classify_scaling(cases)
        self.assertTrue(scaling["linear"])
        self.assertFalse(scaling["state_leak"])
        self.assertEqual(scaling["classification"], "LINEAR_NO_STATE_LEAK")

    def test_success_gate_binds_immutable_artifacts_checkpoint_and_raw_root(
        self,
    ):
        for token in (
            "artifact_sha256.before",
            "artifact_sha256.after",
            "checkpoint_files.before",
            "checkpoint_files.after",
            "checkpoint_before == checkpoint_after",
            "artifacts_before == artifacts_after",
            "raw_root.sha256",
            "gate.complete",
            "raw_root_sha256=",
        ):
            self.assertIn(token, RUNNER_TEXT)


if __name__ == "__main__":
    unittest.main()
