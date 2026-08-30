import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/summarize_xrage_lookup_latency.py"


class XrageLookupLatencySummaryTest(unittest.TestCase):
    def test_exact_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "results.tsv"
            with results.open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=("latency", "ticks", "hash", "issues",
                                "completions", "wait_cycles", "peak"),
                    delimiter="\t",
                )
                writer.writeheader()
                for latency in (0, 1, 2, 3, 8):
                    enabled = latency != 0
                    writer.writerow({
                        "latency": latency, "ticks": 100 + latency,
                        "hash": "123", "issues": 65536 if enabled else 0,
                        "completions": 65536 if enabled else 0,
                        "wait_cycles": latency, "peak": latency * 4,
                    })
            output = root / "out"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(results), str(output),
                 "--native16-ticks", "120"], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((output / "lookup_latency.json").read_text())
            self.assertAlmostEqual(report["rows"][1]["delta_vs_latency0_pct"], 1)


if __name__ == "__main__":
    unittest.main()
