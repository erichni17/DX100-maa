#!/usr/bin/env python3

import configparser
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from experiments.scripts.report_maa_storage import (
    materializer_active_page_accounting,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments" / "scripts" / "report_maa_storage.py"


class StorageReportTest(unittest.TestCase):
    def test_materializer_destination_identity_uses_exact_minimum_bits(
        self,
    ) -> None:
        packed = materializer_active_page_accounting(2, 1, 4096, 8)
        self.assertEqual(packed["destination_bits"], 0)
        self.assertEqual(packed["packed_page_control_bits"], 5176)
        self.assertEqual(packed["additional_control_bits_per_context"], 5178)
        self.assertEqual(
            packed["additional_result_payload_bytes_all_contexts"],
            131072,
        )

    def write_config(
        self,
        root: Path,
        physical: int,
        native_order: bool,
        direct_retirement_line_handoff: bool = False,
        response_words: int = 0,
        response_pool: int = 480,
        inactive_masked_retention_lines: int = 0,
        inactive_payload_capture_lines: int = 0,
        materializer_active_pages: int = 1,
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
            "virtual_combine_words": "4096",
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
            "page_materialization_active_pages": str(
                materializer_active_pages
            ),
            "inactive_page_masked_fragment_retention_lines": str(
                inactive_masked_retention_lines
            ),
            "inactive_page_payload_capture_lines": str(
                inactive_payload_capture_lines
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
            self.assertEqual(
                control["metadata_bytes_per_indirect_unit"], 20537
            )
            self.assertEqual(
                report["bounded_state_lower_bound"][
                    "physical_spd_virtual_payload_and_control_bytes"
                ],
                581957,
            )
            comparable = report["comparable_storage_lower_bound"]
            self.assertEqual(
                comparable["retained_shared_descriptor_bytes"], 254464
            )
            self.assertEqual(comparable["native_total_bytes"], 2417152)
            self.assertEqual(comparable["configured_total_bytes"], 852805)
            buffers = report["virtual_data_buffers"]
            self.assertEqual(
                buffers["source_response_storage_mode"], "packed-word-pool"
            )
            self.assertEqual(
                buffers["configured_source_response_bytes_per_indirect_unit"],
                3840,
            )
            self.assertEqual(buffers["unpacked_line_bytes_per_slot"], 0)
            self.assertEqual(
                buffers[
                    "configured_destination_combiner_bytes_per_indirect_unit"
                ],
                32768,
            )
            self.assertEqual(
                buffers["destination_combiner_word_pool_per_indirect_unit"],
                4096,
            )
            self.assertEqual(
                buffers["destination_combiner_reference_bits"], 12
            )
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
            self.assertEqual(conservative["bounded_state_bytes"], 581957)
            self.assertEqual(
                conservative["comparable_configured_bytes"], 852805
            )
            allocated = report["allocated_model_storage_lower_bound"]
            self.assertEqual(
                allocated["retained_shared_descriptor_bytes"], 861120
            )
            self.assertEqual(allocated["native_total_bytes"], 3023808)
            self.assertEqual(allocated["configured_total_bytes"], 1457413)
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

    def test_two_active_materializer_pages_charge_8k_result_sensitivity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_config = self.write_config(
                root, 4096, True, direct_retirement_line_handoff=True
            )
            result, output = self.run_report(
                root, baseline_config, "direct-index"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            baseline = json.loads((output / "maa_storage.json").read_text())
            baseline_state = baseline["page_materialization_active_page_state"]
            self.assertFalse(baseline_state["enabled"])
            self.assertEqual(baseline_state["configured_capacity"], 1)
            self.assertEqual(
                baseline_state["packed_hardware_accounting"][
                    "additional_control_bytes_all_contexts"
                ],
                0,
            )

            dual_root = root / "dual"
            dual_root.mkdir()
            dual_config = self.write_config(
                dual_root,
                4096,
                True,
                direct_retirement_line_handoff=True,
                materializer_active_pages=2,
            )
            result, output = self.run_report(
                dual_root, dual_config, "direct-index"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            dual = json.loads((output / "maa_storage.json").read_text())
            state = dual["page_materialization_active_page_state"]
            self.assertTrue(state["enabled"])
            self.assertEqual(
                state["classification"],
                "8K active-result sensitivity; not iso-area 4K",
            )
            self.assertFalse(state["iso_area_4k_target"])
            self.assertEqual(state["configured_capacity"], 2)
            self.assertEqual(state["shared_line_buffers"], 16)
            self.assertEqual(state["shared_cache_ports"], 4)
            self.assertEqual(state["physical_spd_elements_per_tile"], 4096)
            self.assertEqual(state["physical_spd_capacity_delta_elements"], 0)
            packed = state["packed_hardware_accounting"]
            self.assertEqual(packed["destination_bits"], 5)
            self.assertEqual(packed["staging_map_bits_per_active_page"], 5120)
            self.assertEqual(packed["counter_bits_per_active_page"], 53)
            self.assertEqual(packed["packed_page_control_bits"], 5181)
            self.assertEqual(
                packed["additional_control_bits_per_context"], 5183
            )
            self.assertEqual(
                packed["additional_control_bytes_per_context"], 648
            )
            self.assertEqual(
                packed["additional_control_bits_all_contexts"], 20732
            )
            self.assertEqual(
                packed["additional_control_bytes_all_contexts"], 2592
            )
            self.assertEqual(packed["result_elements_per_active_page"], 4096)
            self.assertEqual(
                packed["configured_active_result_elements_per_context"],
                8192,
            )
            self.assertEqual(
                packed["additional_result_elements_per_context"], 4096
            )
            self.assertEqual(
                packed["additional_result_elements_all_contexts"], 16384
            )
            self.assertEqual(
                packed["additional_result_payload_bytes_per_context"],
                32768,
            )
            self.assertEqual(
                packed["additional_result_payload_bytes_all_contexts"],
                131072,
            )
            self.assertEqual(packed["additional_payload_bytes"], 131072)
            for field in (
                "additional_cache_ports",
                "additional_line_buffers",
                "additional_physical_spd_elements",
            ):
                self.assertEqual(packed[field], 0)
            self.assertEqual(
                dual["scratchpad"]["physical_payload_bytes"],
                baseline["scratchpad"]["physical_payload_bytes"],
            )
            self.assertEqual(
                dual["counted_payload"][
                    "physical_spd_plus_virtual_buffers_bytes"
                ]
                - baseline["counted_payload"][
                    "physical_spd_plus_virtual_buffers_bytes"
                ],
                131072,
            )
            for section in (
                "bounded_state_lower_bound",
                "comparable_storage_lower_bound",
                "allocated_model_storage_lower_bound",
            ):
                key = (
                    "physical_spd_virtual_payload_and_control_bytes"
                    if section == "bounded_state_lower_bound"
                    else "configured_total_bytes"
                )
                self.assertEqual(
                    dual[section][key] - baseline[section][key], 133664
                )

            for invalid_capacity in (0, 3):
                case = root / f"invalid-{invalid_capacity}"
                case.mkdir()
                invalid = self.write_config(
                    case,
                    4096,
                    True,
                    direct_retirement_line_handoff=True,
                    materializer_active_pages=invalid_capacity,
                )
                result, _ = self.run_report(case, invalid, "direct-index")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "page_materialization_active_pages must be one or two",
                    result.stderr,
                )

            non_4k_root = root / "non-4k"
            non_4k_root.mkdir()
            non_4k = self.write_config(
                non_4k_root,
                2048,
                True,
                direct_retirement_line_handoff=True,
                materializer_active_pages=2,
            )
            result, _ = self.run_report(non_4k_root, non_4k, "direct-index")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("8K-result sensitivity", result.stderr)

            no_handoff_root = root / "no-handoff"
            no_handoff_root.mkdir()
            no_handoff = self.write_config(
                no_handoff_root,
                4096,
                True,
                materializer_active_pages=2,
            )
            result, _ = self.run_report(
                no_handoff_root, no_handoff, "direct-index"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "two active materializer pages require",
                result.stderr.lower(),
            )

    def test_inactive_masked_retention_valid_capacities_and_exact_totals(
        self,
    ) -> None:
        expected = {
            512: {
                "payload_bits": 262144,
                "payload_and_output_bytes": 32832,
                "control_bits": 170364,
                "control_bytes": 21296,
                "combined_total_bits": 436840,
                "combined_total_bytes": 54605,
                "bounded_state": 647058,
                "comparable": 917906,
                "allocated": 1522514,
            },
            1024: {
                "payload_bits": 524288,
                "payload_and_output_bytes": 65600,
                "control_bits": 326528,
                "control_bytes": 40816,
                "combined_total_bits": 855148,
                "combined_total_bytes": 106894,
                "bounded_state": 699347,
                "comparable": 970195,
                "allocated": 1574803,
            },
            2048: {
                "payload_bits": 1048576,
                "payload_and_output_bytes": 131136,
                "control_bits": 638852,
                "control_bytes": 79857,
                "combined_total_bits": 1691760,
                "combined_total_bytes": 211470,
                "bounded_state": 803923,
                "comparable": 1074771,
                "allocated": 1679379,
            },
            4096: {
                "payload_bits": 2097152,
                "payload_and_output_bytes": 262208,
                "control_bits": 1263496,
                "control_bytes": 157937,
                "combined_total_bits": 3364980,
                "combined_total_bytes": 420623,
                "bounded_state": 1013076,
                "comparable": 1283924,
                "allocated": 1888532,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_config = self.write_config(root, 4096, True, True)
            result, output = self.run_report(
                root, baseline_config, "direct-index"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            baseline = json.loads((output / "maa_storage.json").read_text())
            baseline_retention = baseline[
                "inactive_masked_fragment_retention_state"
            ]
            self.assertFalse(baseline_retention["enabled"])
            self.assertEqual(
                baseline_retention["packed_hardware_accounting"][
                    "combined_total_bytes"
                ],
                0,
            )

            for capacity, values in expected.items():
                with self.subTest(capacity=capacity):
                    case_root = root / str(capacity)
                    case_root.mkdir()
                    config = self.write_config(
                        case_root,
                        4096,
                        True,
                        True,
                        inactive_masked_retention_lines=capacity,
                    )
                    result, output = self.run_report(
                        case_root, config, "direct-index"
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(
                        (output / "maa_storage.json").read_text()
                    )
                    state = report["inactive_masked_fragment_retention_state"]
                    self.assertTrue(state["enabled"])
                    self.assertEqual(state["descriptor_partitions"], 4)
                    self.assertEqual(state["write_banks"], 4)
                    self.assertEqual(state["token_tiles"], 32)
                    packed = state["packed_hardware_accounting"]
                    self.assertEqual(packed["capacity_entries"], capacity)
                    self.assertEqual(
                        packed["entries_per_partition"], capacity // 4
                    )
                    self.assertEqual(
                        packed["entries_per_bank_per_partition"],
                        capacity // 16,
                    )
                    self.assertEqual(
                        packed["payload_bits"], values["payload_bits"]
                    )
                    self.assertEqual(
                        packed["payload_and_output_bytes"],
                        values["payload_and_output_bytes"],
                    )
                    self.assertEqual(
                        packed["control_bits"], values["control_bits"]
                    )
                    self.assertEqual(
                        packed["control_bytes"], values["control_bytes"]
                    )
                    self.assertEqual(
                        packed["lookup_pipeline_control_bits"], 510
                    )
                    self.assertEqual(
                        packed["fallback_rebind_control_bits"], 1262
                    )
                    self.assertEqual(packed["maa_lookup_control_bits"], 1772)
                    self.assertEqual(
                        packed["persistent_token_incarnation_bits"], 2048
                    )
                    self.assertEqual(
                        packed["combined_total_bits"],
                        values["combined_total_bits"],
                    )
                    self.assertEqual(
                        packed["combined_total_bits"],
                        packed["payload_bits"]
                        + packed["output_payload_bits"]
                        + packed["control_bits"]
                        + packed["maa_lookup_control_bits"]
                        + packed["persistent_token_incarnation_bits"],
                    )
                    self.assertEqual(
                        packed["combined_total_bytes"],
                        values["combined_total_bytes"],
                    )
                    self.assertEqual(
                        report["bounded_state_lower_bound"][
                            "physical_spd_virtual_payload_and_control_bytes"
                        ],
                        values["bounded_state"],
                    )
                    self.assertEqual(
                        report["comparable_storage_lower_bound"][
                            "configured_total_bytes"
                        ],
                        values["comparable"],
                    )
                    self.assertEqual(
                        report["allocated_model_storage_lower_bound"][
                            "configured_total_bytes"
                        ],
                        values["allocated"],
                    )
                    self.assertEqual(
                        report["bounded_state_lower_bound"][
                            "physical_spd_virtual_payload_and_control_bytes"
                        ]
                        - baseline["bounded_state_lower_bound"][
                            "physical_spd_virtual_payload_and_control_bytes"
                        ],
                        values["combined_total_bytes"],
                    )

    def test_inactive_masked_retention_config_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = self.write_config(
                root, 4096, True, True, inactive_masked_retention_lines=513
            )
            result, _ = self.run_report(root, invalid, "direct-index")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("zero or one of 512/1024/2048/4096", result.stderr)

            missing_handoff_root = root / "missing-handoff"
            missing_handoff_root.mkdir()
            missing_handoff = self.write_config(
                missing_handoff_root,
                4096,
                True,
                inactive_masked_retention_lines=2048,
            )
            result, _ = self.run_report(
                missing_handoff_root, missing_handoff, "direct-index"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "requires direct_retirement_line_handoff=true", result.stderr
            )

            exclusive_root = root / "exclusive"
            exclusive_root.mkdir()
            exclusive = self.write_config(
                exclusive_root,
                4096,
                True,
                True,
                inactive_masked_retention_lines=2048,
                inactive_payload_capture_lines=64,
            )
            result, _ = self.run_report(
                exclusive_root, exclusive, "direct-index"
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mutually exclusive", result.stderr)

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
