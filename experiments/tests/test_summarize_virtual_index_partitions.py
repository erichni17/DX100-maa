import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

from experiments.tests.virtual_case_fixture import write_evidence

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "experiments/scripts/summarize_virtual_index_partitions.py"
)
SPEC = importlib.util.spec_from_file_location("partition_summary", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


MANIFEST_BASE = {
    "case": "paged_overlap_4k",
    "mode": "paged_overlap",
    "logical_tile_elements": "16384",
    "page_elements": "4096",
    "physical_tile_elements": "4096",
    "row_table_slices": "16",
    "row_table_entries_per_subslice_row": "8",
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
    "row_table_slices": "16",
    "row_table_entries_per_subslice_row": "8",
    "virtual_grow_order": "0",
    "row_table_unique_cache_lines": "100",
    "row_table_unique_rows": "10",
    "row_table_cache_lines": "110",
    "row_table_rows_inserted": "20",
    "write_issues": "10",
    "write_completions": "10",
    "pages_ready": "0",
    "simInsts": "100",
    "index_hwm": "16",
    "source_reads": "110",
    "row_table_full_events": "1",
    "virtual_build_rounds": "10",
    "dram_reads": "200",
    "dram_activates": "20",
    "dram_precharges": "15",
}


def write_case(
    root: Path, label: str, ticks: int, output_hash="1234", artifact="a" * 64
):
    match = MODULE.LABEL_RE.match(label)
    assert match
    dimensions = match.groupdict()
    path = root / label
    path.mkdir()
    manifest = dict(MANIFEST_BASE)
    manifest["row_table_rows_per_slice"] = dimensions["rows"]
    manifest["virtual_index_partitions"] = dimensions["partitions"]
    (path / "manifest.txt").write_text(
        "".join(f"{key}={value}\n" for key, value in manifest.items())
    )
    result = dict(RESULT_BASE)
    result.update(
        {
            "simTicks": str(ticks),
            "output_hash": output_hash,
            "row_table_rows_per_slice": dimensions["rows"],
            "virtual_index_partitions": dimensions["partitions"],
            "index_words": str(16384 * int(dimensions["partitions"])),
            "index_line_reads": str(1025 * int(dimensions["partitions"])),
        }
    )
    with (path / "result.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=result, delimiter="\t")
        writer.writeheader()
        writer.writerow(result)
    write_evidence(path, manifest, result, artifact)
    return path


class PartitionSummaryTest(unittest.TestCase):
    def test_reports_recovery_against_both_baselines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = write_case(root, "r64_e8_g0_p1", 1000)
            constrained = write_case(root, "r32_e8_g0_p1", 1300)
            treatment = write_case(root, "r32_e8_g0_p2", 1100)
            rows = MODULE.summarize(
                MODULE.collect(full, constrained, [treatment])
            )
            self.assertEqual(rows[2]["delta_vs_full_percent"], "10.000000")
            self.assertEqual(
                rows[2]["delta_vs_constrained_percent"], "-15.384615"
            )
            self.assertEqual(rows[2]["descriptor_slots"], "4096")

    def test_rejects_mixed_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = write_case(root, "r64_e8_g0_p1", 1000)
            constrained = write_case(root, "r32_e8_g0_p1", 1300)
            treatment = write_case(
                root, "r32_e8_g0_p2", 1100, artifact="c" * 64
            )
            with self.assertRaisesRegex(ValueError, "artifact hashes differ"):
                MODULE.collect(full, constrained, [treatment])

    def test_rejects_output_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            full = write_case(root, "r64_e8_g0_p1", 1000)
            constrained = write_case(root, "r32_e8_g0_p1", 1300)
            treatment = write_case(
                root, "r32_e8_g0_p2", 1100, output_hash="4321"
            )
            with self.assertRaisesRegex(ValueError, "output hash differs"):
                MODULE.collect(full, constrained, [treatment])

    def test_rejects_wrong_scan_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            treatment = write_case(root, "r32_e8_g0_p2", 1100)
            result = treatment / "result.tsv"
            result.write_text(result.read_text().replace("32768", "16384"))
            with self.assertRaisesRegex(
                ValueError, "does not match raw evidence"
            ):
                MODULE.load_case(treatment)


if __name__ == "__main__":
    unittest.main()
