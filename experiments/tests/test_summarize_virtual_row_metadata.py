import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/scripts/summarize_virtual_row_metadata.py"
SPEC = importlib.util.spec_from_file_location(
    "row_metadata_summary", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


MANIFEST_BASE = {
    "case": "paged_overlap_4k",
    "mode": "paged_overlap",
    "logical_tile_elements": "16384",
    "page_elements": "4096",
    "physical_tile_elements": "4096",
    "row_table_slices": "16",
    "virtual_grow_order": "0",
    "virtual_response_slots": "128",
    "virtual_response_word_pool": "480",
    "virtual_combine_slots": "384",
    "virtual_combine_words": "4096",
    "virtual_combine_ways": "4",
    "virtual_combine_victim_policy": "0",
    "virtual_combine_banks": "0",
    "source_commit": "deadbeef",
    "timeout": "none",
}

RESULT_BASE = {
    "case": "paged_overlap_4k",
    "output_hash": "1234",
    "index_line_reads": "1025",
    "index_words": "16384",
    "row_table_slices": "16",
    "row_table_unique_cache_lines": "9523",
    "row_table_unique_rows": "129",
    "response_slots": "128",
    "response_word_pool": "480",
    "row_table_full_events": "1",
    "virtual_build_rounds": "77",
    "row_table_cache_lines": "9841",
    "row_table_rows_inserted": "1425",
    "source_reads": "9841",
    "write_issues": "5102",
    "dram_reads": "27202",
    "dram_activates": "5776",
    "dram_precharges": "4733",
}


def write_point(root, label, rows, entries, ticks, artifact="a" * 64):
    point = root / label
    point.mkdir()
    manifest = dict(MANIFEST_BASE)
    manifest["row_table_rows_per_slice"] = str(rows)
    manifest["row_table_entries_per_subslice_row"] = str(entries)
    (point / "manifest.txt").write_text(
        "".join(f"{key}={value}\n" for key, value in manifest.items())
    )
    result = dict(RESULT_BASE)
    result["row_table_rows_per_slice"] = str(rows)
    result["row_table_entries_per_subslice_row"] = str(entries)
    result["simTicks"] = str(ticks)
    with (point / "result.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=result, delimiter="\t")
        writer.writeheader()
        writer.writerow(result)
    (point / "artifact_sha256.txt").write_text(
        f"{artifact}  /tmp/gem5.opt\n{'b' * 64}  /tmp/test.bin\n"
    )
    (point / "virtual_tile_consumer_case.pass").touch()


class RowMetadataSummaryTest(unittest.TestCase):
    def test_summary_calculates_capacity_and_latency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_point(root, "r64_e8", 64, 8, 1000)
            write_point(root, "r32_e8", 32, 8, 1100)
            rows = MODULE.summarize(MODULE.collect(root))
            by_point = {row["point"]: row for row in rows}
            self.assertEqual(by_point["r64_e8"]["descriptor_slots"], "8192")
            self.assertEqual(by_point["r32_e8"]["descriptor_slots"], "4096")
            self.assertEqual(
                by_point["r32_e8"]["latency_delta_percent"], "10.000000"
            )

    def test_rejects_mixed_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_point(root, "r64_e8", 64, 8, 1000)
            write_point(root, "r32_e8", 32, 8, 1100, artifact="c" * 64)
            with self.assertRaisesRegex(ValueError, "artifact hashes differ"):
                MODULE.collect(root)

    def test_rejects_output_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_point(root, "r64_e8", 64, 8, 1000)
            write_point(root, "r32_e8", 32, 8, 1100)
            result = root / "r32_e8/result.tsv"
            result.write_text(result.read_text().replace("1234", "4321"))
            with self.assertRaisesRegex(
                ValueError, "mismatched result output_hash"
            ):
                MODULE.collect(root)

    def test_rejects_mislabeled_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_point(root, "r64_e8", 32, 8, 1000)
            with self.assertRaisesRegex(ValueError, "manifest rows"):
                MODULE.collect(root)


if __name__ == "__main__":
    unittest.main()
