import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/summarize_flag_current_controls.py"


def write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


class FlagCurrentControlsSummaryTest(unittest.TestCase):
    def test_matched_controls_and_guard_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            safe_rows = []
            control_rows = []
            for index in range(14):
                case_id = f"case{index}"
                length = 16 + index * 8
                safe_rows.append({"id": case_id, "length": length,
                                  "ticks": 80, "writes": length // 8,
                                  "full": length // 8, "partial": 0,
                                  "hash": 1000 + index})
                for arm, ticks in (("fused16", 100), ("compact16", 90),
                                   ("direct4_small", 120), ("direct4_max", 80)):
                    control_rows.append({
                        "id": case_id, "arm": arm, "length": length,
                        "ticks": ticks, "writes": length // 8,
                        "completions": length // 8, "full": length // 8,
                        "partial": 0, "hash": 1000 + index,
                    })
            safe = root / "safe.tsv"
            controls = root / "controls.tsv"
            write_tsv(safe, ("id", "length", "ticks", "writes", "full",
                             "partial", "hash"), safe_rows)
            write_tsv(controls, ("id", "arm", "length", "ticks", "writes",
                                 "completions", "full", "partial", "hash"),
                      control_rows)
            output = root / "out"
            result = subprocess.run(
                ["python3", str(SCRIPT), str(safe), str(controls), str(output)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads((output / "flag_current_controls.json").read_text())
            self.assertTrue(report["guard_timing_neutral"])
            self.assertAlmostEqual(
                report["geomean_latency_change_pct"]["fused16"], -20
            )
            self.assertAlmostEqual(
                report["geomean_latency_change_pct"]["direct4_small"],
                -100 / 3,
            )


if __name__ == "__main__":
    unittest.main()
