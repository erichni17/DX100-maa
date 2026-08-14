import importlib.util
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).parents[1]
    / "experiments/scripts/extract_hybrid_first_stats.py"
)
SPEC = importlib.util.spec_from_file_location("first_stats", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def stats(*windows: str) -> str:
    return "\n".join(
        "---------- Begin Simulation Statistics ----------\n"
        + window
        + "\n---------- End Simulation Statistics ----------"
        for window in windows
    )


class FirstStatsTest(unittest.TestCase):
    def test_extracts_only_the_first_statistics_window(self):
        path = Path(self._testMethodName + ".txt")
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text(
            stats(
                "simTicks 101\ncycles_TOTAL 7", "simTicks 202\ncycles_TOTAL 9"
            )
        )
        self.assertEqual(
            MODULE.extract(path, ["simTicks", "cycles_TOTAL"]),
            {
                "simTicks": 101,
                "cycles_TOTAL": 7,
            },
        )

    def test_fails_closed_for_missing_or_duplicate_metric(self):
        missing = Path(self._testMethodName + "-missing.txt")
        duplicate = Path(self._testMethodName + "-duplicate.txt")
        self.addCleanup(missing.unlink, missing_ok=True)
        self.addCleanup(duplicate.unlink, missing_ok=True)
        missing.write_text(stats("simTicks 101"))
        with self.assertRaisesRegex(ValueError, "missing metric"):
            MODULE.extract(missing, ["cycles_TOTAL"])

        duplicate.write_text(stats("simTicks 101\nsimTicks 102"))
        with self.assertRaisesRegex(ValueError, "duplicate metric"):
            MODULE.extract(duplicate, ["simTicks"])


if __name__ == "__main__":
    unittest.main()
