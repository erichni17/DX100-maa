#!/usr/bin/env python3

import configparser
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments" / "scripts" / "report_maa_storage.py"


class StorageReportTest(unittest.TestCase):
    def write_config(self, root: Path, physical: int, native_order: bool) -> Path:
        values = {
            "num_cores": "4",
            "num_tiles_per_core": "8",
            "num_tile_elements": "16384",
            "physical_tile_elements": str(physical),
            "num_maas": "1",
            "num_indirect_units_per_maa": "1",
            "num_memory_channels": "2",
            "num_initial_row_table_slices": "32",
            "num_row_table_rows_per_slice": "64",
            "num_row_table_entries_per_subslice_row": "8",
            "virtual_combine_slots": "384",
            "virtual_combine_ways": "4",
            "virtual_response_slots": "128",
            "virtual_response_words": "0",
            "virtual_response_word_pool": "480",
            "virtual_index_buffer_lines": "8",
            "virtual_max_outstanding_writes": "64",
            "virtual_native_issue_order": str(native_order).lower(),
        }
        config = configparser.ConfigParser()
        config["system.maa"] = values
        path = root / "config.ini"
        with path.open("w", encoding="utf-8") as stream:
            config.write(stream)
        return path

    def run_report(
        self, root: Path, config: Path, mechanism: str, subslices: int = 32
    ) -> tuple[subprocess.CompletedProcess[str], Path]:
        output = root / "report"
        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                str(config),
                "--output-dir",
                str(output),
                "--mechanism",
                mechanism,
                "--word-bytes",
                "8",
                "--dram-subslices",
                str(subslices),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        return result, output

    def test_direct4_bounded_control_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(root, 4096, True)
            result, output = self.run_report(root, config, "direct-index")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "maa_storage.json").read_text())
            control = report["incremental_virtual_control_lower_bound"]
            self.assertEqual(control["metadata_bytes_per_indirect_unit"], 9271)
            self.assertEqual(
                report["bounded_state_lower_bound"][
                    "physical_spd_virtual_payload_and_control_bytes"
                ],
                562499,
            )
            comparable = report["comparable_storage_lower_bound"]
            self.assertEqual(
                comparable["retained_shared_descriptor_bytes"], 254464
            )
            self.assertEqual(comparable["native_total_bytes"], 2417152)
            self.assertEqual(comparable["configured_total_bytes"], 833347)
            allocated = report["allocated_model_storage_lower_bound"]
            self.assertEqual(
                allocated["retained_shared_descriptor_bytes"], 861120
            )
            self.assertEqual(allocated["native_total_bytes"], 3023808)
            self.assertEqual(allocated["configured_total_bytes"], 1437955)
            metadata = report["retained_logical_metadata"]
            self.assertEqual(
                metadata["allocated_row_entry_capacity_per_indirect_unit"],
                65536,
            )
            self.assertEqual(
                report["configuration"]["row_table_organizations_allocated"],
                [4, 8, 16, 32],
            )

    def test_native_has_no_incremental_virtual_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(root, 16384, False)
            result, output = self.run_report(root, config, "native")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "maa_storage.json").read_text())
            control = report["incremental_virtual_control_lower_bound"]
            self.assertEqual(control["metadata_bytes_per_indirect_unit"], 0)
            self.assertEqual(
                report["bounded_state_lower_bound"][
                    "reduction_vs_native_spd_pct"
                ],
                0,
            )
            comparable = report["comparable_storage_lower_bound"]
            self.assertEqual(
                comparable["configured_total_bytes"],
                comparable["native_total_bytes"],
            )
            self.assertEqual(comparable["reduction_vs_native_pct"], 0)
            allocated = report["allocated_model_storage_lower_bound"]
            self.assertEqual(
                allocated["configured_total_bytes"],
                allocated["native_total_bytes"],
            )
            self.assertEqual(allocated["reduction_vs_native_pct"], 0)

    def test_rejects_nondivisible_dram_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(root, 4096, True)
            result, _ = self.run_report(
                root, config, "direct-index", subslices=48
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must divide evenly", result.stderr)


if __name__ == "__main__":
    unittest.main()
