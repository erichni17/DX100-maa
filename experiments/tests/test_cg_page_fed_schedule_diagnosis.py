#!/usr/bin/env python3
"""Adversarial static contract for the matched page-fed schedule diagnosis."""

import ast
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_cg_page_fed_schedule_diagnosis.py"
REPORT = (
    ROOT / "experiments/analysis/cg_page_fed_schedule_diagnosis_2026-08-25.md"
)


class CgPageFedScheduleDiagnosisContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = RUNNER.read_text()
        cls.tree = ast.parse(cls.text)

    def test_one_generic_guest_and_checkpoint_precede_both_restores(self):
        for token in (
            "NUM_TILES_PER_CORE=10",
            "CG_LOGICAL_PAGE_RMW",
            "physical_page_product_soa_jit",
            "page_fed_product_soa_jit",
            "treatment.selector",
            "MAA_DEFERRED {selector}",
            "--checkpoint-dir",
            "checkpoint_sha256",
            "guest_sha256",
        ):
            self.assertIn(token, self.text)
        self.assertEqual(self.text.count("command(checkpoint_cmd,"), 1)
        self.assertNotIn("CG_PAGE_FED_SOA_ONLY", self.text)

    def test_checkpointed_selector_path_is_rewritten_only_for_treatment(self):
        self.assertIn("deferred guest checkpoints this pathname", self.text)
        self.assertIn(
            'selector.write_text(f"token_stream_ld {selection}\\n")', self.text
        )
        self.assertIn("selector.chmod(0o444)", self.text)

    def test_only_compact_schedule_instrumentation_is_enabled(self):
        self.assertIn(
            'DEBUG_FLAGS = "MAAIssueDigest,MAAMacroEvent"', self.text
        )
        self.assertNotIn("MAAReorderTrace", self.text)
        self.assertNotIn("MAAVirtualTrace", self.text)
        self.assertNotIn("MAAIssueTrace", self.text)
        self.assertNotIn(
            "timeout", self.text.lower().replace('"timeout": "none"', "")
        )

    def test_fingerprint_stops_larger_launches_and_native_is_prohibited(self):
        self.assertIn("SIZES = (1024, 4096, 16384, 32768)", self.text)
        self.assertIn(
            'if not result["comparison"]["quantized_fingerprint_equal"]:',
            self.text,
        )
        self.assertIn('"native_reruns": 0', self.text)
        self.assertNotIn("native16", self.text.lower())

    def test_required_stage_closure_is_projected(self):
        for token in (
            "source_issue_order_digest_equal",
            "source_issue_timing_equal",
            "rowtable_admission_projection_equal",
            "a_line_and_alias_closure_equal",
            "product_publication_value_delivery_closes",
            "epoch_drain_equal",
            "IND_SoaJitAliasesApplied",
            "STR_PublishWriteResponses",
            "IND_SoaJitEpochDrains",
            "raw_root.sha256",
        ):
            self.assertIn(token, self.text)

    def test_medium_size_requires_accepted_control_instead_of_rerunning_it(
        self,
    ):
        self.assertIn("--prior-control", self.text)
        self.assertIn("a non-1024 start requires --prior-control", self.text)
        self.assertIn(
            "prior control lacks an accepted NA=1024 fingerprint", self.text
        )

    def test_python_is_syntactically_valid(self):
        subprocess.run(
            ["python3", "-m", "py_compile", str(RUNNER)], check=True
        )

    def test_terminal_4096_handoff_preserves_raw_vs_quantized_truth(self):
        report = REPORT.read_text()
        for token in (
            "NA=4096 terminal matched diagnosis (r1)",
            "2026-08-25-cg-page-fed-schedule-diagnosis-4096-r1",
            "raw FP32 bits differ",
            "physical/page-fed = 1.191876234792x",
            "halves this publication traffic",
            "do not launch CG_NA=16384",
            "deterministic reduction-order CG_NA=4096",
        ):
            self.assertIn(token, report)


if __name__ == "__main__":
    unittest.main()
