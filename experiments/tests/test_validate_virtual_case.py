import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

from experiments.tests.virtual_case_fixture import write_evidence

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/scripts/validate_virtual_case.py"
SPEC = importlib.util.spec_from_file_location(
    "validate_virtual_case", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


MANIFEST = {
    "case": "paged_overlap_4k",
    "mode": "paged_overlap",
    "logical_tile_elements": "16384",
    "page_elements": "4096",
    "physical_tile_elements": "4096",
    "row_table_slices": "16",
    "row_table_rows_per_slice": "40",
    "row_table_entries_per_subslice_row": "8",
    "virtual_grow_order": "0",
    "virtual_response_slots": "128",
    "virtual_response_word_pool": "480",
    "virtual_combine_slots": "384",
    "virtual_combine_words": "4096",
    "virtual_combine_ways": "4",
    "virtual_combine_victim_policy": "0",
    "virtual_combine_banks": "0",
    "virtual_index_partitions": "2",
    "virtual_index_filter_words_per_cycle": "4",
    "require_index_filter_wait": "1",
    "source_commit": "deadbeef",
}
RESULT = {
    "case": "paged_overlap_4k",
    "output_hash": "1234",
    "simTicks": "1000",
    "simInsts": "100",
    "index_line_reads": "20",
    "index_words": "32768",
    "index_hwm": "16",
    "index_filter_words": "32768",
    "index_filter_cycles": "8192",
    "index_filter_wait_events": "2",
    "index_filter_wait_cycles": "128",
    "write_issues": "30",
    "write_completions": "30",
    "pages_ready": "0",
    "row_table_cache_lines": "90",
    "row_table_rows_inserted": "20",
    "row_table_unique_cache_lines": "80",
    "row_table_unique_rows": "10",
    "source_reads": "70",
    "row_table_full_events": "0",
    "virtual_build_rounds": "7",
    "dram_reads": "200",
    "dram_activates": "20",
    "dram_precharges": "15",
}


def make_case(root: Path) -> Path:
    path = root / "case"
    path.mkdir()
    manifest = dict(MANIFEST)
    result = dict(RESULT)
    (path / "manifest.txt").write_text(
        "".join(f"{key}={value}\n" for key, value in manifest.items())
    )
    with (path / "result.tsv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=result, delimiter="\t")
        writer.writeheader()
        writer.writerow(result)
    write_evidence(path, manifest, result)
    return path


class ValidateVirtualCaseTest(unittest.TestCase):
    def test_accepts_complete_consistent_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_case(Path(directory))
            evidence = MODULE.validate_case(path)
            self.assertEqual(evidence["result"]["simTicks"], "1000")

    def test_rejects_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_case(Path(directory))
            (path / "restore.exit").write_text("1\n")
            with self.assertRaisesRegex(ValueError, "restore.exit"):
                MODULE.validate_case(path)

    def test_rejects_result_not_backed_by_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_case(Path(directory))
            result = path / "result.tsv"
            result.write_text(result.read_text().replace("1000", "9999"))
            with self.assertRaisesRegex(ValueError, "raw evidence"):
                MODULE.validate_case(path)

    def test_rejects_resolved_config_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_case(Path(directory))
            config = path / "run/config.ini"
            config.write_text(
                config.read_text().replace(
                    "virtual_index_partitions=2",
                    "virtual_index_partitions=3",
                )
            )
            with self.assertRaisesRegex(ValueError, "resolved config"):
                MODULE.validate_case(path)

    def test_rejects_undercharged_partition_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_case(Path(directory))
            stats = path / "run/stats.txt"
            stats.write_text(
                stats.read_text().replace(
                    "I0_IND_VirtIndexFilterCycles 8192",
                    "I0_IND_VirtIndexFilterCycles 8191",
                )
            )
            result_path = path / "result.tsv"
            with result_path.open(newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter="\t"))
            rows[0]["index_filter_cycles"] = "8191"
            with result_path.open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=rows[0], delimiter="\t"
                )
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, "throughput lower bound"):
                MODULE.validate_case(path)

    def test_rejects_missing_required_partition_filter_wait(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_case(Path(directory))
            stats = path / "run/stats.txt"
            stats.write_text(
                stats.read_text()
                .replace(
                    "I0_IND_VirtIndexFilterWaitEvents 2",
                    "I0_IND_VirtIndexFilterWaitEvents 0",
                )
                .replace(
                    "I0_IND_VirtIndexFilterWaitCycles 128",
                    "I0_IND_VirtIndexFilterWaitCycles 0",
                )
            )
            result_path = path / "result.tsv"
            with result_path.open(newline="") as stream:
                rows = list(csv.DictReader(stream, delimiter="\t"))
            rows[0]["index_filter_wait_events"] = "0"
            rows[0]["index_filter_wait_cycles"] = "0"
            with result_path.open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=rows[0], delimiter="\t"
                )
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, "never delayed"):
                MODULE.validate_case(path)

    def test_rejects_mutated_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_case(Path(directory))
            (path / "artifacts/gem5.opt").write_text("mutated")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                MODULE.validate_case(path)

    def test_rejects_fatal_log_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_case(Path(directory))
            log = path / "restore.log"
            log.write_text(log.read_text() + "fatal: injected\n")
            with self.assertRaisesRegex(ValueError, "fatal marker"):
                MODULE.validate_case(path)


if __name__ == "__main__":
    unittest.main()
