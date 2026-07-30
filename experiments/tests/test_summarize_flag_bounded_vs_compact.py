import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "experiments" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "summarize_flag_bounded_vs_compact",
    SCRIPT_DIR / "summarize_flag_bounded_vs_compact.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FlagBoundedVsCompactTest(unittest.TestCase):
    def write_case(self, root: Path, index: int, ratio: float) -> None:
        case = root / f"case_{index:02d}"
        case.mkdir()
        (case / "xrage_comparison.pass").touch()
        row = {
            "pair": "bounded_vs_compact",
            "reference": "compact16",
            "candidate": "bounded4",
            "reference_ticks": "1000",
            "candidate_ticks": str(round(1000 * ratio)),
            "roi_memory_reads_delta": "-10",
            "dram_reads_delta": "-11",
            "dram_activates_delta": "-12",
            "dram_precharges_delta": "-13",
        }
        with (case / "xrage_pairwise.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=row, delimiter="\t")
            writer.writeheader()
            writer.writerow(row)
        arm_rows = [
            {
                "label": "compact16",
                "output_length": "80",
                "virtual_write_issues": "12",
            },
            {
                "label": "bounded4",
                "output_length": "80",
                "virtual_write_issues": "10",
            },
        ]
        with (case / "xrage_comparison.tsv").open(
            "w", encoding="utf-8", newline=""
        ) as stream:
            writer = csv.DictWriter(
                stream, fieldnames=arm_rows[0], delimiter="\t"
            )
            writer.writeheader()
            writer.writerows(arm_rows)

    def test_load_and_summarize_exact_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(MODULE.EXPECTED_CASES):
                self.write_case(root, index, 0.9)
            rows = MODULE.load_rows(root)
            summary = MODULE.summarize(rows)
            self.assertEqual(summary["cases"], MODULE.EXPECTED_CASES)
            self.assertAlmostEqual(summary["latency_geomean_ratio"], 0.9)
            self.assertEqual(summary["wins"], MODULE.EXPECTED_CASES)
            self.assertEqual(summary["roi_memory_reads_delta_sum"], -140)
            self.assertEqual(summary["compact16_excess_c_writes_sum"], 28)
            self.assertEqual(summary["bounded4_excess_c_writes_sum"], 0)

    def test_rejects_incomplete_campaign(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_case(root, 0, 1.0)
            with self.assertRaisesRegex(SystemExit, "expected 14 cases"):
                MODULE.load_rows(root)


if __name__ == "__main__":
    unittest.main()
