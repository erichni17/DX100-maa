import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = (
    Path(__file__).parents[1] / "scripts" / "summarize_virtual_page_overlap.py"
)
SPEC = importlib.util.spec_from_file_location("overlap_summary", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class VirtualPageOverlapSummaryTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        base = {
            "output_hash": "1234",
            "simInsts": "100",
            "index_words": "16384",
            "index_hwm": "53",
            "write_issues": "5301",
            "write_completions": "5301",
            "indirect_spd_reads": "0",
            "pages_ready": "4",
            "pages_ready_before_source_drain": "2",
            "first_page_ready_cycles": "95",
            "all_pages_ready_cycles": "120",
            "page_ready_span_cycles": "25",
            "stream_spd_reads": "2052",
            "stream_writes": "4",
            "alu_compute_cycles": "1245",
            "page_ready_signals": "4",
            "page_wait_reads": "0",
            "page_wait_deferrals": "0",
            "page_wait_responses": "0",
            "l3_read_hits_maa": "2032",
            "l3_read_misses_maa": "1",
            "memory_bytes_read_maa": "925632",
            "cpu_cycles": "150000",
        }
        rows = {
            "native_16k": {
                **base,
                "simTicks": "1000",
                "index_words": "0",
                "index_hwm": "0",
                "write_issues": "0",
                "write_completions": "0",
                "indirect_spd_reads": "100",
                "pages_ready": "0",
                "pages_ready_before_source_drain": "0",
                "first_page_ready_cycles": "0",
                "all_pages_ready_cycles": "0",
                "page_ready_span_cycles": "0",
                "page_ready_signals": "0",
            },
            "paged_4k": {**base, "simTicks": "1100"},
            "paged_overlap_4k": {
                **base,
                "simTicks": "1080",
                "write_issues": "5325",
                "write_completions": "5325",
                "page_wait_reads": "4",
                "page_wait_deferrals": "1",
                "page_wait_responses": "4",
            },
        }
        for case, row in rows.items():
            case_dir = self.root / case
            case_dir.mkdir()
            (case_dir / "virtual_tile_consumer_case.pass").touch()
            with (case_dir / "result.tsv").open(
                "w", newline="", encoding="utf-8"
            ) as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["case", *row], delimiter="\t"
                )
                writer.writeheader()
                writer.writerow({"case": case, **row})
            (case_dir / "artifact_sha256.txt").write_text(
                "a" * 64 + "  gem5.opt\n" + "b" * 64 + "  test_binary\n",
                encoding="utf-8",
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def run_summary(self):
        with mock.patch.object(sys, "argv", [str(SCRIPT), str(self.root)]):
            MODULE.main()

    def test_valid_matrix_writes_summary_and_pass_marker(self):
        self.run_summary()
        self.assertTrue((self.root / "virtual_page_overlap.pass").is_file())
        summary = (self.root / "overlap_summary.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("recovers 20.0%", summary)
        self.assertIn("5,301 to 5,325", summary)

    def test_mismatched_output_hash_fails_closed(self):
        result = self.root / "paged_overlap_4k" / "result.tsv"
        result.write_text(
            result.read_text().replace("1234", "9999"), encoding="utf-8"
        )
        with self.assertRaisesRegex(SystemExit, "output hashes differ"):
            self.run_summary()

    def test_changed_virtual_invariant_fails_closed(self):
        result = self.root / "paged_overlap_4k" / "result.tsv"
        result.write_text(
            result.read_text().replace("\t16384\t53\t", "\t16000\t53\t"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(SystemExit, "index_words differs"):
            self.run_summary()


if __name__ == "__main__":
    unittest.main()
