import tempfile
import unittest
from pathlib import Path

from experiments.scripts import analyze_direct_index_issue_width as analyzer


class DirectIndexIssueWidthAnalysisTest(unittest.TestCase):
    def test_counts_bursts_and_serialization_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.log"
            trace.write_text(
                "10: global: event=index_line_issue operation_tick=1\n"
                "10: global: event=index_line_issue operation_tick=1\n"
                "10: global: event=index_line_issue operation_tick=1\n"
                "20: global: event=index_line_issue operation_tick=1\n"
                "30: global: event=index_line_issue operation_tick=2\n"
                "30: global: event=index_line_issue operation_tick=2\n"
            )
            report = analyzer.analyze(trace, 3, 10_000, 10.0, (1, 2))
        self.assertEqual(report["operations"], 2)
        self.assertEqual(report["issue_events"], 6)
        self.assertEqual(report["unique_enqueue_ticks"], 3)
        self.assertEqual(report["maximum_same_tick_burst"], 3)
        self.assertEqual(report["initial_burst_min"], 2)
        self.assertEqual(report["initial_burst_max"], 3)
        self.assertEqual(
            report["serialization_bounds"]["1"][
                "extra_generation_cycles_upper_bound"
            ],
            3,
        )
        self.assertEqual(
            report["serialization_bounds"]["2"][
                "extra_generation_cycles_upper_bound"
            ],
            1,
        )

    def test_rejects_empty_trace_and_invalid_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.log"
            trace.write_text("nothing\n")
            with self.assertRaisesRegex(analyzer.AnalysisError, "no direct"):
                analyzer.analyze(trace, 1, 1, 1.0, (1,))
            with self.assertRaisesRegex(analyzer.AnalysisError, "positive"):
                analyzer.analyze(trace, 0, 1, 1.0, (1,))


if __name__ == "__main__":
    unittest.main()
