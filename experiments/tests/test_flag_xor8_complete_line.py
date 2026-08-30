import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/run_flag_xor8_complete_line.sh"


class FlagXor8CompleteLineTest(unittest.TestCase):
    def test_fixed_campaign_contract(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        source = SCRIPT.read_text()
        self.assertIn("MAA_VIRTUAL_COMBINE_SLOTS=2048", source)
        self.assertIn("MAA_VIRTUAL_COMBINE_WORDS=3072", source)
        self.assertIn("FLAG_COMBINE_WAYS:-8", source)
        self.assertIn("FLAG_COMBINE_XOR_SHIFT:-7", source)
        self.assertIn("FLAG_COMBINE_LOOKUP_LATENCY:-0", source)
        self.assertIn("FLAG_PAGE_ORDERED_DRAIN:-0", source)
        self.assertIn('MAA_VIRTUAL_COMBINE_WAYS="$combine_ways"', source)
        self.assertIn(
            'MAA_VIRTUAL_COMBINE_SET_XOR_SHIFT="$combine_xor_shift"', source
        )
        self.assertIn(
            'MAA_VIRTUAL_COMBINE_LOOKUP_LATENCY_CYCLES="$combine_lookup_latency"',
            source,
        )
        self.assertIn(
            'MAA_VIRTUAL_PAGE_ORDERED_COMBINER_DRAIN="$page_ordered_drain"',
            source,
        )
        self.assertIn("MAA_VIRTUAL_COMPLETE_LINE_DRAIN_LINES_PER_CYCLE=1", source)
        self.assertIn("$issued -eq $full", source)
        self.assertIn("timeout=none", source)


if __name__ == "__main__":
    unittest.main()
