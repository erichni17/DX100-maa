import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = (
    ROOT / "experiments/scripts/run_flag_runtime_attribution_campaign.sh"
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


if __name__ == "__main__":
    unittest.main()
