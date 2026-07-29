import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = (
    ROOT / "experiments/scripts/run_flag_runtime_attribution_campaign.sh"
)
PERFORMANCE_RUNNER = ROOT / "experiments/scripts/run_xrage_performance_case.sh"
ISSUE_TRACE_RUNNER = (
    ROOT / "experiments/scripts/run_xrage_issue_trace_matrix.sh"
)


class FlagCampaignPackagingTest(unittest.TestCase):
    def test_freezes_comparator_dram_parser_dependency(self) -> None:
        script = CAMPAIGN.read_text(encoding="utf-8")
        self.assertIn(
            'dram_parser="$out/frozen-tools/summarize_xrage_dram.py"',
            script,
        )
        self.assertIn(
            'cp "$root/experiments/scripts/summarize_xrage_dram.py" '
            '"$dram_parser"',
            script,
        )
        self.assertIn('"$dram_parser" "$storage_reporter"', script)

    def test_selected_direct_index_feeder_depth_is_128_lines(self) -> None:
        campaign = CAMPAIGN.read_text(encoding="utf-8")
        performance = PERFORMANCE_RUNNER.read_text(encoding="utf-8")
        issue_trace = ISSUE_TRACE_RUNNER.read_text(encoding="utf-8")
        self.assertIn(
            "run_arm direct4 direct_index_4k direct4 4096 1 128 16384",
            campaign,
        )
        self.assertIn(
            "MAA_VIRTUAL_INDEX_BUFFER_LINES:-128",
            performance,
        )
        self.assertIn(
            "direct_index_4k direct4 4096 128",
            issue_trace,
        )


if __name__ == "__main__":
    unittest.main()
