"""Focused contracts for the fresh UME GZZ strict two-pass matrix."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/run_ume_two_pass_matrix.py"
sys.path.insert(0, str(ROOT))

from experiments.scripts import run_ume_two_pass_matrix as matrix  # noqa: E402


class UmeTwoPassMatrixTest(unittest.TestCase):
    def test_audit_selects_only_the_direct_gzz_edge(self) -> None:
        audit = matrix.source_contract()
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["selection"], "GZZ")
        applications = {item["name"]: item for item in audit["applications"]}
        self.assertFalse(applications["GZP"]["selected_for_matrix"])
        self.assertIn("published-source/RMW", applications["GZP"]["reason"])
        self.assertTrue(applications["GZZ"]["selected_for_matrix"])
        self.assertIn("unpredicated", applications["GZZ"]["reason"])
        self.assertEqual(
            audit["physical_result_word_bound"], matrix.PHYSICAL_ELEMENTS
        )

    def test_frozen_fingerprint_is_recomputed_from_fixed_input(self) -> None:
        self.assertEqual(
            matrix.deterministic_output_hash(matrix.ELEMENTS),
            int(matrix.EXPECTED_OUTPUT_HASH),
        )
        self.assertEqual(matrix.EXPECTED_ACTIVE_CORNERS, 15_564)
        self.assertEqual(matrix.OUTPUT_ELEMENTS, 196_384)

    def test_four_arms_are_bounded_and_share_hybrid_guest(self) -> None:
        self.assertEqual(
            [arm.name for arm in matrix.ARMS],
            [
                "native16",
                "native4",
                "original_hybrid",
                "strict_bounded_hybrid",
            ],
        )
        original, strict = matrix.ARMS[2:]
        self.assertEqual(original.guest, strict.guest)
        self.assertEqual(original.result_words, matrix.RESULT_WORD_BOUND)
        self.assertEqual(strict.result_words, matrix.RESULT_WORD_BOUND)
        self.assertFalse(original.strict)
        self.assertTrue(strict.strict)
        self.assertTrue(strict.complete_line)
        self.assertEqual(original.selector, "stream_control")
        self.assertEqual(strict.selector, "token_stream_ld")

    def test_strict_restore_delta_activates_only_selected_arm(self) -> None:
        original, strict = matrix.ARMS[2:]
        arguments = dict(
            gem5=Path("gem5.opt"),
            ramulator_config=Path("ramulator.yaml"),
            checkpoint=Path("checkpoint"),
            guest=Path("guest"),
            options="16384 selector",
            outdir=Path("run"),
        )
        control = matrix.common_restore_command(**arguments, arm=original)
        candidate = matrix.common_restore_command(**arguments, arm=strict)
        for token in (
            "--maa_virtual_strict_two_phase",
            "--maa_virtual_shared_result_payload",
            "--maa_virtual_complete_line_only",
            "--maa_virtual_page_ordered_combiner_drain",
            "--maa_virtual_combine_lookup_latency_cycles=3",
            "--maa_virtual_complete_line_payload_words_per_cycle=8",
        ):
            self.assertNotIn(token, control)
            self.assertIn(token, candidate)
        self.assertIn("--maa_virtual_combine_words=3584", control)
        self.assertIn("--maa_virtual_response_word_pool=512", control)
        self.assertIn("--maa_virtual_combine_words=3072", candidate)
        self.assertIn("--maa_virtual_response_word_pool=1024", candidate)
        for command in (control, candidate):
            self.assertIn("--maa_num_initial_row_table_slices=32", command)
            self.assertIn("--maa_virtual_index_buffer_lines=64", command)
            self.assertIn(
                "--debug-flags=MAAVirtualTrace,MAAMacroEvent", command
            )

    def test_plan_is_nonexecuting_and_requires_positive_activation(
        self,
    ) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["schema"], "dx100.ume_gzz_two_pass.plan.v1")
        self.assertTrue(result["fresh_controls_required"])
        self.assertEqual(result["acceptance"]["strict_operations"], 1)
        self.assertEqual(
            result["acceptance"]["a_issues_at_admission_close"], 0
        )
        self.assertEqual(result["acceptance"]["result_words_at_most"], 4_096)

    def test_execute_requires_an_output_and_parallelism_is_bounded(
        self,
    ) -> None:
        with self.assertRaises(SystemExit):
            matrix.parse_args(["--execute"])
        with self.assertRaises(SystemExit):
            matrix.parse_args(["--max-parallel-restores", "5"])

    def test_rejection_record_is_terminal_and_cannot_authorize_full_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            matrix.record_rejection(root, "strict path did not activate")
            rejection = json.loads((root / "failure.json").read_text())
            self.assertEqual(rejection["decision"], "REJECT")
            self.assertFalse(rejection["strict_activation_accepted"])
            self.assertFalse(rejection["full_run_authorized"])
            self.assertEqual((root / "campaign.exit").read_text(), "1\n")

    def test_gzz_resolves_selector_before_checkpoint(self) -> None:
        source = (ROOT / "benchmarks/UME/gradzatz.cpp").read_text()
        self.assertLess(
            source.index("maa_read_virtual_consumer_mode"),
            source.index("m5_checkpoint(0, 0)"),
        )

    def test_build_admission_uses_shared_source_credit(self) -> None:
        source = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        self.assertIn("virtual_pending_source_fanout))", source)
        self.assertIn("virtual_fanout))", source)
        self.assertIn("event=shared_source_credit_stall schema=1", source)
        self.assertIn("event=shared_source_partial_spill schema=1", source)
        self.assertIn("spillVirtualCombinePartialForSourceCredit", source)
        self.assertIn("scheduleExecuteInstructionEvent(1)", source)
        bounded_global = source[
            source.index("issueBoundedGlobalSourceLine") :
            source.index("void IndirectAccessUnit::serviceBoundedGlobalMerge")
        ]
        self.assertIn("spillVirtualCombinePartialForSourceCredit", bounded_global)
        self.assertIn("scheduleExecuteInstructionEvent(1)", bounded_global)
        self.assertNotIn("response_slots=%d/%zu", source)

    def test_shared_source_payload_charges_unique_words_with_fanout(self) -> None:
        implementation = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        header = (ROOT / "src/mem/MAA/IndirectAccess.hh").read_text()
        fanout = (ROOT / "src/mem/MAA/VirtualSourceFanout.hh").read_text()
        for token in (
            "buildVirtualSourceFanout",
            "virtualSourcePayloadWords",
            "VirtualSourceFanout",
            "virtual_pending_source_fanout",
            "shared response retained",
            "shared result word",
        ):
            self.assertIn(token, implementation + header + fanout)
        self.assertNotIn("remaining_word_uses", implementation + header)
        self.assertIn("static constexpr uint16_t ScanWidth = 4", fanout)
        self.assertGreaterEqual(
            implementation.count(
                "virtual_response_word_pool_limit != 0 &&\n"
                "        !virtual_shared_result_payload"
            ),
            1,
        )
        self.assertNotIn(
            "if (virtual_response_word_pool_limit != 0)\n"
            "                                panic_if(virtual_words >",
            implementation,
        )

    def test_page_materializer_closes_strict_consumer_lifetime(self) -> None:
        source = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        self.assertIn(
            "observeStrictTwoPhaseConsumerBegin(token, generation, curTick())",
            source,
        )
        self.assertIn(
            "completeStrictTwoPhaseConsumer(\n"
            "            key.tokenTile, key.generation, curTick())",
            source,
        )


if __name__ == "__main__":
    unittest.main()
