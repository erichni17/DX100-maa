import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/summarize_xrage_complete_line_drain.py"


class XrageDrainSummaryTest(unittest.TestCase):
    def test_exact_width_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "results.tsv"
            with results.open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=("width", "ticks", "hash", "issued",
                                "stall_cycles", "peak", "stats_sha256"),
                    delimiter="\t",
                )
                writer.writeheader()
                for width, ticks, peak in ((0, 100, 4), (1, 120, 1),
                                           (2, 105, 2), (4, 100, 4),
                                           (8, 100, 4)):
                    writer.writerow({
                        "width": width, "ticks": ticks, "hash": "123",
                        "issued": 8192, "stall_cycles": width,
                        "peak": peak, "stats_sha256": str(width) * 64,
                    })
            output = root / "out"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(results), str(output),
                 "--legacy-ticks", "90", "--native16-ticks", "110"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((output / "drain_width.json").read_text())
            self.assertAlmostEqual(report["rows"][1]["delta_vs_width0_pct"], 20)
            self.assertTrue((output / "drain_width.pass").is_file())


if __name__ == "__main__":
    unittest.main()
