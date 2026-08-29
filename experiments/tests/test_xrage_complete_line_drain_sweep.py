import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/run_xrage_complete_line_drain_sweep.sh"


class XrageCompleteLineDrainSweepTest(unittest.TestCase):
    def test_contract_is_fixed_and_fail_closed(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        source = SCRIPT.read_text()
        for width in (0, 1, 2, 4, 8):
            self.assertIn(str(width), source)
        self.assertIn("MAA_VIRTUAL_COMPLETE_LINE_DRAIN_LINES_PER_CYCLE", source)
        self.assertIn("MAA_VIRTUAL_COMPLETE_LINE_ONLY=1", source)
        self.assertIn("MAA_VIRTUAL_COMBINE_SLOTS=1536", source)
        self.assertIn("MAA_VIRTUAL_COMBINE_WORDS=2560", source)
        self.assertIn("MAA_VIRTUAL_RESPONSE_WORD_POOL=1024", source)
        self.assertIn("$writes -eq 8192", source)
        self.assertIn("$issued -eq 8192", source)
        self.assertIn("output hashes differ", source)
        self.assertIn("timeout=none", source)


if __name__ == "__main__":
    unittest.main()
