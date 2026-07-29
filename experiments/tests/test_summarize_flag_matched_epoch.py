import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "experiments" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "summarize_flag_matched_epoch",
    SCRIPT_DIR / "summarize_flag_matched_epoch.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FlagMatchedEpochTest(unittest.TestCase):
    def test_geometric_mean(self) -> None:
        self.assertAlmostEqual(MODULE.geometric_mean([1.0, 4.0]), 2.0)

    def test_geometric_mean_rejects_empty_or_nonpositive(self) -> None:
        for values in ([], [0.0], [-1.0]):
            with self.subTest(values=values):
                with self.assertRaisesRegex(SystemExit, "positive observations"):
                    MODULE.geometric_mean(values)


if __name__ == "__main__":
    unittest.main()
