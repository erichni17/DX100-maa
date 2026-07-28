#!/usr/bin/env python3

import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).parents[1] / "scripts" / "compare_virtual_destination_control.py"
SPEC = importlib.util.spec_from_file_location("destination_control", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class DestinationControlTest(unittest.TestCase):
    def make_case(self, root: Path, name: str, ticks: int, writes: int) -> Path:
        path = root / name
        path.mkdir()
        manifest = {
            "case": name,
            "logical_tile_elements": "16384",
            "row_table_slices": "16",
            "virtual_grow_order": "0",
            "virtual_response_slots": "128",
            "virtual_response_word_pool": "480",
            "source_commit": "abc",
            "timeout": "none",
        }
        (path / "manifest.txt").write_text(
            "".join(f"{key}={value}\n" for key, value in manifest.items())
        )
        result = {
            "case": name,
            "output_hash": "123",
            "simTicks": str(ticks),
            "index_line_reads": "1025",
            "index_words": "16384",
            "row_table_slices": "16",
            "virtual_grow_order": "0",
            "response_slots": "128",
            "response_word_pool": "480",
            "source_reads": "100",
            "write_issues": str(writes),
            "dram_reads": "200",
            "dram_activates": "50",
            "dram_precharges": "40",
        }
        with (path / "result.tsv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=result, delimiter="\t")
            writer.writeheader()
            writer.writerow(result)
        (path / "artifact_sha256.txt").write_text(
            "".join(f"deadbeef  /tmp/{name}\n" for name in MODULE.MATCHED_ARTIFACTS)
        )
        return path

    def test_valid_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = self.make_case(root, "native_direct_16k", 100, 0)
            virtual = self.make_case(root, "paged_overlap_4k", 110, 8)
            summary = MODULE.compare(native, virtual)
            self.assertEqual(summary["virtual_overhead_percent"], "10.000000")
            self.assertEqual(summary["backing_write_delta"], "8")

    def test_rejects_mismatched_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = self.make_case(root, "native_direct_16k", 100, 0)
            virtual = self.make_case(root, "paged_overlap_4k", 110, 8)
            hashes = (virtual / "artifact_sha256.txt").read_text()
            (virtual / "artifact_sha256.txt").write_text(
                hashes.replace("deadbeef", "badc0de", 1)
            )
            with self.assertRaisesRegex(ValueError, "mismatched artifact"):
                MODULE.compare(native, virtual)


if __name__ == "__main__":
    unittest.main()
