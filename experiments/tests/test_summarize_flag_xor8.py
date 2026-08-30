import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/summarize_flag_xor8.py"


class FlagXor8SummaryTest(unittest.TestCase):
    def test_exact_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {name: root / f"{name}.tsv" for name in ("w16", "x8")}
            for name, path in paths.items():
                with path.open("w", newline="") as stream:
                    writer = csv.DictWriter(
                        stream,
                        fieldnames=("id", "length", "ticks", "writes", "full",
                                    "partial", "stall_cycles", "peak_sum", "hash"),
                        delimiter="\t",
                    )
                    writer.writeheader()
                    for index in range(14):
                        length = 16 + index * 8
                        writer.writerow({
                            "id": f"case{index}", "length": length,
                            "ticks": 90 if name == "x8" else 100,
                            "writes": length // 8, "full": length // 8,
                            "partial": 0, "stall_cycles": 1,
                            "peak_sum": 2, "hash": 1000 + index,
                        })
            output = root / "out"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(paths["w16"]), str(paths["x8"]),
                 str(output)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((output / "flag_xor8.json").read_text())
            self.assertAlmostEqual(report["geomean_latency_change_pct"], -10)


if __name__ == "__main__":
    unittest.main()
