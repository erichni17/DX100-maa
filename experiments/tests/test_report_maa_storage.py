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
    def write_config(
        self,
        root: Path,
        physical: int,
        native_order: bool,
        direct_retirement_line_handoff: bool = False,
        response_words: int = 0,
        response_pool: int = 480,
    ) -> Path:
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
            "virtual_response_words": str(response_words),
            "virtual_response_word_pool": str(response_pool),
            "virtual_index_buffer_lines": "8",
            "virtual_max_outstanding_writes": "64",
            "virtual_native_issue_order": str(native_order).lower(),
            "direct_retirement_line_handoff": (
                str(direct_retirement_line_handoff).lower()
            ),
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
            buffers = report["virtual_data_buffers"]
            self.assertEqual(
                buffers["source_response_storage_mode"], "packed-word-pool"
            )
            self.assertEqual(
                buffers["configured_source_response_bytes_per_indirect_unit"],
                3840,
            )
            self.assertEqual(buffers["unpacked_line_bytes_per_slot"], 0)
            control = report["incremental_virtual_control_lower_bound"]
            self.assertEqual(
                control["source_response_metadata_bits_per_slot"], 190
            )
            self.assertEqual(
                control["source_response_metadata_bits_per_indirect_unit"],
                128 * 190,
            )
            self.assertEqual(
                buffers["inactive_cpp_response_line_bytes_per_indirect_unit"],
                0,
            )
            conservative = report["conservative_cpp_static_storage_view"]
            self.assertEqual(
                conservative["inactive_fixed_response_line_bytes"], 0
            )
            self.assertEqual(conservative["bounded_state_bytes"], 562499)
            self.assertEqual(
                conservative["comparable_configured_bytes"], 833347
            )
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

    def test_direct_retirement_line_handoff_is_flag_gated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disabled = self.write_config(root, 4096, True)
            result, output = self.run_report(root, disabled, "direct-index")
            self.assertEqual(result.returncode, 0, result.stderr)
            disabled_report = json.loads(
                (output / "maa_storage.json").read_text()
            )
            disabled_state = disabled_report[
                "direct_retirement_line_handoff_state"
            ]
            self.assertFalse(disabled_state["enabled"])
            self.assertEqual(
                disabled_state["hardware_lower_bound"]["total_bytes"], 0
            )
            self.assertEqual(
                disabled_state["conservative_cpp_static_view"]["total_bytes"],
                0,
            )

            enabled_root = root / "enabled"
            enabled_root.mkdir()
            enabled = self.write_config(enabled_root, 4096, True, True)
            result, output = self.run_report(
                enabled_root, enabled, "direct-index"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "maa_storage.json").read_text())
            state = report["direct_retirement_line_handoff_state"]
            self.assertTrue(state["enabled"])
            hardware = state["hardware_lower_bound"]
            self.assertEqual(hardware["queue_line_payload_bytes"], 4096)
            self.assertEqual(hardware["execution_records"], 4)
            self.assertEqual(hardware["request_records"], 64)
            self.assertEqual(hardware["per_port_retry_slots"], 4)
            self.assertEqual(hardware["retry_slot_bytes_64_bit_abi"], 8)
            self.assertEqual(hardware["early_line_ledger_bytes"], 1696)
            self.assertEqual(hardware["producer_line_metadata_bytes"], 1024)
            self.assertEqual(hardware["total_bytes"], 10496)
            cpp = state["conservative_cpp_static_view"]
            self.assertEqual(cpp["queue_payload_bytes"], 4096)
            self.assertEqual(cpp["queue_control_bytes"], 17728)
            self.assertEqual(cpp["execution_bytes_per_record"], 456)
            self.assertEqual(cpp["request_bytes_per_record"], 72)
            self.assertEqual(cpp["retry_slots_bytes"], 32)
            self.assertEqual(cpp["total_bytes"], 31008)
            self.assertEqual(
                report["bounded_state_lower_bound"][
                    "physical_spd_virtual_payload_and_control_bytes"
                ]
                - disabled_report["bounded_state_lower_bound"][
                    "physical_spd_virtual_payload_and_control_bytes"
                ],
                10496,
            )

    def test_unpacked_mode_retains_one_fixed_line_per_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(
                root,
                4096,
                True,
                response_words=0,
                response_pool=0,
            )
            result, output = self.run_report(root, config, "direct-index")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "maa_storage.json").read_text())
            buffers = report["virtual_data_buffers"]
            self.assertEqual(
                buffers["source_response_storage_mode"],
                "unpacked-fixed-lines",
            )
            self.assertEqual(buffers["unpacked_line_bytes_per_slot"], 64)
            self.assertEqual(
                buffers["configured_source_response_bytes_per_indirect_unit"],
                128 * 64,
            )
            self.assertEqual(
                buffers["inactive_cpp_response_line_bytes_per_indirect_unit"],
                0,
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
                report["conservative_cpp_static_storage_view"][
                    "inactive_fixed_response_line_bytes"
                ],
                0,
            )
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

    def test_bounded_offset_capacity_reduces_retained_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(root, 4096, True)
            parser = configparser.ConfigParser()
            parser.read(config)
            parser["system.maa"]["num_row_table_rows_per_slice"] = "16"
            parser["system.maa"]["num_offset_table_entries"] = "4096"
            parser["system.maa"]["num_offset_table_epoch_entries"] = "2048"
            with config.open("w", encoding="utf-8") as stream:
                parser.write(stream)
            result, output = self.run_report(root, config, "direct-index")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "maa_storage.json").read_text())
            metadata = report["retained_logical_metadata"]
            self.assertEqual(
                metadata["offset_entry_capacity_per_indirect_unit"], 4096
            )
            self.assertEqual(
                metadata["logical_iteration_domain_per_indirect_unit"],
                16384,
            )
            self.assertEqual(
                metadata["offset_epoch_capacity_per_indirect_unit"], 2048
            )
            self.assertLess(
                metadata["shared_descriptor_lower_bound_bytes"], 95872
            )

    def test_rejects_nondivisible_dram_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(root, 4096, True)
            result, _ = self.run_report(
                root, config, "direct-index", subslices=48
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must divide evenly", result.stderr)

    def test_rejects_negative_offset_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(root, 4096, True)
            parser = configparser.ConfigParser()
            parser.read(config)
            parser["system.maa"]["num_offset_table_entries"] = "-1"
            with config.open("w", encoding="utf-8") as stream:
                parser.write(stream)
            result, _ = self.run_report(root, config, "direct-index")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be positive", result.stderr)


if __name__ == "__main__":
    unittest.main()
