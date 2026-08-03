import tempfile
import unittest
from pathlib import Path

from experiments.analysis.analyze_hybrid_tail_instrumented import (
    AuditError,
    audit_failed_attempt,
    build_report,
)


class InstrumentedTailAnalyzerTest(unittest.TestCase):
    def test_failed_attempt_is_bound_to_exact_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "native_direct_16k").mkdir()
            (root / "pair.exit").write_text("1\n")
            (root / "shared-checkpoint.exit").write_text("0\n")
            (root / "native_direct_16k/restore.log").write_text(
                "deferred treatment must contain exactly MODE PAGE\n"
            )
            result = audit_failed_attempt(root)
            self.assertEqual(result["completed_arms"], 0)

    def test_failed_attempt_rejects_completion_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "native_direct_16k").mkdir()
            (root / "pair.exit").write_text("1\n")
            (root / "pair.complete").touch()
            (root / "shared-checkpoint.exit").write_text("0\n")
            with self.assertRaises(AuditError):
                audit_failed_attempt(root)

    def test_report_keeps_residency_caveat(self):
        accepted = {
            "run_counts": {"accepted_gem5_arms_audited": 5},
            "accepted_pair_tail": {
                "roi": {
                    "native_simTicks": 1,
                    "hybrid_simTicks": 2,
                    "delta_simTicks": 1,
                },
                "timeline": {"all_pages_ready_minus_native_roi_end_ticks": 3},
            },
        }
        report = build_report({"tail": {}}, accepted, None)
        self.assertIn(
            "not_speedup_proven",
            report["hypothesis_separation"]["per_page_consumer_serialization"],
        )


if __name__ == "__main__":
    unittest.main()
