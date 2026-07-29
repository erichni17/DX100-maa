import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments" / "scripts" / "summarize_xrage_index_depth.py"
SPEC = importlib.util.spec_from_file_location(
    "summarize_xrage_index_depth", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class XrageIndexDepthSummaryTest(unittest.TestCase):
    fields = [
        "label",
        "arm",
        "gem5_sha256",
        "binary_sha256",
        "input_sha256",
        "output_hash",
        "logical_tile_elements",
        "physical_tile_elements",
        "index_buffer_lines",
        "virtual_native_issue_order",
        "roi_simTicks",
    ]

    def write_comparison(self, root: Path, rows: list[dict[str, str]]) -> Path:
        comparison = root / "xrage_comparison.tsv"
        with comparison.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, self.fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        (root / "xrage_comparison.pass").touch()
        return comparison

    def row(self, label: str, depth: int, ticks: int) -> dict[str, str]:
        return {
            "label": label,
            "arm": "direct_index_4k",
            "gem5_sha256": "a" * 64,
            "binary_sha256": "b" * 64,
            "input_sha256": "c" * 64,
            "output_hash": "1234",
            "logical_tile_elements": "16384",
            "physical_tile_elements": "4096",
            "index_buffer_lines": str(depth),
            "virtual_native_issue_order": "1",
            "roi_simTicks": str(ticks),
        }

    def test_selects_smallest_depth_within_tolerance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comparison = self.write_comparison(
                root,
                [
                    self.row("direct64", 64, 43_914_213),
                    self.row("direct128", 128, 43_586_189),
                    self.row("direct256", 256, 43_586_189),
                ],
            )
            output = root / "summary"
            with mock.patch(
                "sys.argv",
                [str(SCRIPT), str(comparison), "--output-dir", str(output)],
            ):
                self.assertEqual(MODULE.main(), 0)
            report = json.loads(
                (output / "xrage_index_depth.json").read_text()
            )
            self.assertEqual(
                report["selection"]["recommended_index_buffer_lines"], 128
            )
            self.assertEqual(
                report["selection"][
                    "recommended_payload_bytes_per_indirect_unit"
                ],
                8192,
            )
            self.assertEqual(
                report["selection"]["equal_tick_plateau_pairs"], [[128, 256]]
            )
            self.assertTrue((output / "xrage_index_depth.pass").is_file())

    def test_rejects_missing_comparison_pass_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comparison = self.write_comparison(
                root,
                [self.row("direct64", 64, 2), self.row("direct128", 128, 1)],
            )
            (root / "xrage_comparison.pass").unlink()
            with mock.patch(
                "sys.argv",
                [
                    str(SCRIPT),
                    str(comparison),
                    "--output-dir",
                    str(root / "summary"),
                ],
            ):
                with self.assertRaisesRegex(SystemExit, "marker is missing"):
                    MODULE.main()

    def test_rejects_duplicate_depths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            comparison = self.write_comparison(
                root,
                [self.row("first", 64, 2), self.row("second", 64, 1)],
            )
            with mock.patch(
                "sys.argv",
                [
                    str(SCRIPT),
                    str(comparison),
                    "--output-dir",
                    str(root / "summary"),
                ],
            ):
                with self.assertRaisesRegex(SystemExit, "duplicate"):
                    MODULE.main()


if __name__ == "__main__":
    unittest.main()
