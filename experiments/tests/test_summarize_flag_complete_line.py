import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/summarize_flag_complete_line.py"


class FlagCompleteLineSummaryTest(unittest.TestCase):
    def test_exact_fourteen_row_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            historical = root / "historical.tsv"
            safe = root / "safe.tsv"
            with historical.open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=(
                        "id",
                        "pattern_length",
                        "fused16_ticks",
                        "compact16_ticks",
                        "direct4_ticks",
                    ),
                    delimiter="\t",
                )
                writer.writeheader()
                for index in range(14):
                    writer.writerow(
                        {
                            "id": f"case{index}",
                            "pattern_length": 16 + index * 8,
                            "fused16_ticks": 100,
                            "compact16_ticks": 80,
                            "direct4_ticks": 120,
                        }
                    )
            with safe.open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=(
                        "id",
                        "length",
                        "ticks",
                        "writes",
                        "full",
                        "partial",
                        "hash",
                    ),
                    delimiter="\t",
                )
                writer.writeheader()
                for index in range(14):
                    length = 16 + index * 8
                    writer.writerow(
                        {
                            "id": f"case{index}",
                            "length": length,
                            "ticks": 60,
                            "writes": length // 8,
                            "full": length // 8,
                            "partial": 0,
                            "hash": str(1000 + index),
                        }
                    )
            output = root / "out"
            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    str(historical),
                    str(safe),
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(
                (output / "flag_complete_line.json").read_text()
            )
            self.assertEqual(report["configurations"], 14)
            self.assertAlmostEqual(
                report["geomean_latency_change_pct"]["vs_fused16"], -40
            )
            self.assertAlmostEqual(
                report["geomean_latency_change_pct"]["vs_compact16"], -25
            )
            with (output / "flag_complete_line.tsv").open(
                newline=""
            ) as stream:
                rows = list(csv.DictReader(stream, delimiter="\t"))
            self.assertEqual(len(rows), 14)
            self.assertEqual(rows[0]["fused16_ticks"], "100")
            self.assertEqual(rows[0]["vs_direct4_ratio"], "0.5")
            self.assertTrue((output / "flag_complete_line.pass").is_file())


if __name__ == "__main__":
    unittest.main()
