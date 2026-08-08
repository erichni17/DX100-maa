import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.analysis.analyze_hybrid_tail_issue_ready import (
    AuditError,
    audit_config_delta,
    audit_page_readiness,
    build_report,
    classify_mechanism_activation,
)


class HybridTailIssueReadyAnalyzerTest(unittest.TestCase):
    def test_activation_classification_is_fail_closed(self):
        self.assertEqual(
            classify_mechanism_activation(0, 0, 0, 0), "no_activation"
        )
        self.assertEqual(
            classify_mechanism_activation(2, 256, 0, 0),
            "early_release_only_no_dynamic_forward",
        )
        self.assertEqual(
            classify_mechanism_activation(1, 8, 1, 1),
            "bounded_forwarding_activated",
        )
        with self.assertRaises(AuditError):
            classify_mechanism_activation(0, 0, 1, 1)
        with self.assertRaises(AuditError):
            classify_mechanism_activation(1, 8, 0, 1)

    def test_config_delta_accepts_only_treatment_bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = (
                "num_tile_elements=16384\n"
                "physical_tile_elements=4096\n"
                "virtual_max_outstanding_writes=64\n"
                "num_indirect_units_per_maa=1\n"
                "num_maas=1\n"
                "virtual_retirement_forward_latency=1\n"
            )
            control = root / "control.ini"
            candidate = root / "candidate.ini"
            control.write_text(shared + "virtual_page_ready_on_issue=false\n")
            candidate.write_text(shared + "virtual_page_ready_on_issue=true\n")
            delta = audit_config_delta(control, candidate)
            self.assertIn("false->true", delta["only_resolved_config_delta"])

    def test_config_delta_rejects_hidden_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            control = root / "control.ini"
            candidate = root / "candidate.ini"
            control.write_text(
                "num_tile_elements=16384\nphysical_tile_elements=4096\n"
                "virtual_max_outstanding_writes=64\n"
                "num_indirect_units_per_maa=1\n"
                "num_maas=1\n"
                "virtual_retirement_forward_latency=1\n"
                "virtual_page_ready_on_issue=false\n"
            )
            candidate.write_text(
                "num_tile_elements=16384\nphysical_tile_elements=4096\n"
                "virtual_max_outstanding_writes=65\n"
                "num_indirect_units_per_maa=1\n"
                "num_maas=1\n"
                "virtual_retirement_forward_latency=1\n"
                "virtual_page_ready_on_issue=true\n"
            )
            with self.assertRaises(AuditError):
                audit_config_delta(control, candidate)

    def test_page_readiness_reconciles_pending_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page_readiness.tsv"
            path.write_text(
                "tick\tunit\tpage\tready_count\ttotal_pages\tissued_words"
                "\tcompleted_words\tsources_drained\n"
                "1\t0\t0\t1\t4\t4096\t4080\t1\n"
                "2\t0\t1\t2\t4\t4096\t4096\t1\n"
                "3\t0\t2\t3\t4\t4096\t4096\t1\n"
                "4\t0\t3\t4\t4\t4096\t4096\t1\n"
            )
            self.assertEqual(
                audit_page_readiness(path, True)["pending_words"], 16
            )
            with self.assertRaises(AuditError):
                audit_page_readiness(path, False)

    @staticmethod
    def accepted_raw() -> dict:
        return {
            "delta_simTicks": 10,
            "arms": {
                "native_direct_16k": {"simTicks": 90},
                "transparent_4k": {"simTicks": 100},
            },
            "tail": {
                "post_ready_total_ticks": 9,
                "post_ready_blocker_ticks": {
                    "stream_busy_ticks": 8,
                    "alu_busy_ticks": 1,
                },
                "producer_backing_writes": {"post_ready_completions": 0},
            },
        }

    def test_under_two_percent_requires_three_pairs(self):
        accepted = {"fresh_pair": {"path": "/accepted", "delta_simTicks": 10}}
        pair = {"speedup_percent": 1.5}
        with patch(
            "experiments.analysis.analyze_hybrid_tail_issue_ready."
            "audit_attribution_pair",
            return_value=self.accepted_raw(),
        ):
            with self.assertRaises(AuditError):
                build_report([pair], accepted)
            report = build_report([pair, pair, pair], accepted)
        self.assertTrue(report["summary"]["repetitions_satisfied"])


if __name__ == "__main__":
    unittest.main()
