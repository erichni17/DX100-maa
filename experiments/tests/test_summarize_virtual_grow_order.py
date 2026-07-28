import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "summarize_virtual_grow_order.py"
SPEC = importlib.util.spec_from_file_location("grow_summary", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class VirtualGrowOrderSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        base = {
            "case": "paged_4k",
            "output_hash": "1234",
            "simTicks": "1000",
            "index_words": "16384",
            "index_hwm": "53",
            "write_issues": "100",
            "write_completions": "100",
            "indirect_spd_reads": "0",
            "pages_ready": "4",
            "pages_ready_before_source_drain": "2",
            "stream_spd_reads": "2052",
            "stream_writes": "4",
            "page_ready_signals": "4",
            "page_wait_reads": "0",
            "page_wait_deferrals": "0",
            "page_wait_responses": "0",
            "row_table_slices": "16",
            "response_slots": "96",
            "response_word_pool": "480",
            "row_table_cache_lines": "1000",
            "source_reads": "900",
            "response_slot_hwm": "96",
            "response_word_hwm": "480",
            "response_pool_stalls": "8",
            "virtual_grow_order": "0",
            "row_table_full_events": "3",
            "virtual_build_rounds": "20",
            "dram_reads": "1000",
            "dram_activates": "500",
            "dram_precharges": "450",
        }
        rows = {
            "legacy": base,
            "grow_grouped": {
                **base,
                "simTicks": "900",
                "virtual_grow_order": "1",
                "dram_activates": "400",
                "dram_precharges": "350",
            },
        }
        for treatment, row in rows.items():
            case_dir = self.root / treatment
            case_dir.mkdir()
            (case_dir / "virtual_tile_consumer_case.pass").touch()
            with (case_dir / "result.tsv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=row, delimiter="\t")
                writer.writeheader()
                writer.writerow(row)
            (case_dir / "artifact_sha256.txt").write_text(
                "a" * 64 + "  gem5.opt\n" + "b" * 64 + "  test_binary\n",
                encoding="utf-8",
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def run_summary(self):
        with mock.patch.object(sys, "argv", [str(SCRIPT), str(self.root)]):
            MODULE.main()

    def test_valid_treatments_write_summary(self):
        self.run_summary()
        self.assertTrue((self.root / "virtual_grow_order.pass").is_file())
        report = (self.root / "grow_order_summary.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("| latency | 1,000 | 900 | -10.000% |", report)
        self.assertIn("| DRAM activates | 500 | 400 | -20.000% |", report)

    def test_changed_output_fails_closed(self):
        result = self.root / "grow_grouped" / "result.tsv"
        result.write_text(result.read_text().replace("1234", "9999"), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "output_hash differs"):
            self.run_summary()

    def test_timing_dependent_counts_may_differ(self):
        result = self.root / "grow_grouped" / "result.tsv"
        text = result.read_text(encoding="utf-8")
        text = text.replace("\t53\t100\t100\t", "\t49\t99\t99\t")
        result.write_text(text, encoding="utf-8")
        self.run_summary()


if __name__ == "__main__":
    unittest.main()
