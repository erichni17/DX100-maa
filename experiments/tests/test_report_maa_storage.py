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
        inactive_masked_retention_lines: int = 0,
        inactive_payload_capture_lines: int = 0,
        outstanding_writes: int = 64,
        index_lines: int = 8,
        index_issue_width: int = 1,
        combine_words: int = 4096,
        dense_write_allocate: bool = False,
        complete_line_only: bool = False,
        shared_result_payload: bool = False,
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
            "virtual_combine_words": str(combine_words),
            "virtual_shared_result_payload": str(
                shared_result_payload
            ).lower(),
            "virtual_combine_ways": "4",
            "virtual_response_slots": "128",
            "virtual_response_words": str(response_words),
            "virtual_response_word_pool": str(response_pool),
            "virtual_index_buffer_lines": str(index_lines),
            "virtual_index_issue_lines_per_cycle": str(index_issue_width),
            "virtual_max_outstanding_writes": str(outstanding_writes),
            "virtual_native_issue_order": str(native_order).lower(),
            "virtual_dense_write_allocate": str(dense_write_allocate).lower(),
            "virtual_complete_line_only": str(complete_line_only).lower(),
            "direct_retirement_line_handoff": (
                str(direct_retirement_line_handoff).lower()
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
        self,
        root: Path,
        config: Path,
        mechanism: str,
        subslices: int = 32,
        word_bytes: int = 8,
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
                str(word_bytes),
                "--dram-subslices",
                str(subslices),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        return result, output

    def test_shared_result_payload_is_charged_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(
                root,
                4096,
                False,
                combine_words=3072,
                response_pool=1024,
                complete_line_only=True,
                shared_result_payload=True,
            )
            result, output = self.run_report(
                root, config, "generic-virtual", word_bytes=4
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            buffers = json.loads((output / "maa_storage.json").read_text())[
                "virtual_data_buffers"
            ]
            self.assertTrue(buffers["shared_result_payload"])
            self.assertEqual(
                buffers["shared_result_payload_words_per_indirect_unit"],
                4096,
            )
            self.assertEqual(
                buffers["shared_pressure_spill_bitmap_bits_per_indirect_unit"],
                1024,
            )
            self.assertEqual(
                buffers[
                    "shared_pressure_spill_bitmap_bytes_per_indirect_unit"
                ],
                128,
            )
            self.assertEqual(
                buffers["source_response_storage_mode"],
                "shared-result-allocator",
            )
            self.assertEqual(
                buffers["active_source_response_bytes_per_indirect_unit"],
                0,
            )
            self.assertEqual(
                buffers["active_destination_combiner_bytes_per_indirect_unit"],
                4096 * 4,
            )
            self.assertEqual(
                buffers["shared_result_allocator_bytes_per_indirect_unit"],
                4096 * 4,
            )
            self.assertEqual(
                buffers[
                    "excluded_cpp_response_line_shadow_bytes_per_indirect_unit"
                ],
                128 * 64,
            )
            control = json.loads((output / "maa_storage.json").read_text())[
                "incremental_virtual_control_lower_bound"
            ]
            self.assertEqual(
                control["shared_response_word_reference_bits_per_slot"],
                16 * 12,
            )
            self.assertEqual(
                control["shared_response_fanout_counter_bits_per_slot"],
                16 * 15,
            )
            self.assertEqual(
                control["shared_response_fanout_max_uses_per_word"], 16384
            )
            self.assertEqual(
                buffers["destination_combiner_word_pool_per_indirect_unit"],
                4096,
            )

    def test_shared_result_payload_fails_closed_without_bounded_pools(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = ((0, 1024), (3072, 0), (4096, 1))
            for combine_words, response_pool in cases:
                with self.subTest(
                    combine_words=combine_words, response_pool=response_pool
                ):
                    case_root = root / f"{combine_words}-{response_pool}"
                    case_root.mkdir()
                    config = self.write_config(
                        case_root,
                        4096,
                        False,
                        combine_words=combine_words,
                        response_pool=response_pool,
                        shared_result_payload=True,
                    )
                    result, _ = self.run_report(
                        case_root, config, "generic-virtual"
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        "requires explicit nonzero combiner/response "
                        "capacities within physical storage",
                        result.stderr,
                    )

            oversized = self.write_config(
                root,
                16385,
                False,
                combine_words=3072,
                response_pool=1024,
                shared_result_payload=True,
            )
            parser = configparser.ConfigParser()
            parser.read(oversized)
            parser["system.maa"]["num_tile_elements"] = "16385"
            with oversized.open("w", encoding="utf-8") as stream:
                parser.write(stream)
            result, _ = self.run_report(root, oversized, "generic-virtual")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("at most 16384 logical uses", result.stderr)

    def test_direct4_bounded_control_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(root, 4096, True)
            result, output = self.run_report(root, config, "direct-index")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "maa_storage.json").read_text())
            control = report["incremental_virtual_control_lower_bound"]
            self.assertEqual(
                control["metadata_bytes_per_indirect_unit"], 23072
            )
            self.assertEqual(
                control["virtual_retirement_metadata_bytes_per_entry"], 44
            )
            self.assertEqual(
                control[
                    "virtual_retirement_identity_allocator_bytes_per_unit"
                ],
                8,
            )
            self.assertEqual(
                control[
                    "virtual_retirement_scoreboard_bytes_per_indirect_unit"
                ],
                2824,
            )
            self.assertEqual(
                report["bounded_state_lower_bound"][
                    "physical_spd_virtual_payload_and_control_bytes"
                ],
                584492,
            )
            comparable = report["comparable_storage_lower_bound"]
            self.assertEqual(
                comparable["retained_shared_descriptor_bytes"], 254464
            )
            self.assertEqual(comparable["native_total_bytes"], 2417152)
            self.assertEqual(comparable["configured_total_bytes"], 855340)
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
                control[
                    "destination_combiner_global_payload_"
                    "victim_bits_per_indirect_unit"
                ],
                9,
            )
            self.assertEqual(
                buffers[
                    "excluded_cpp_response_line_shadow_bytes_per_indirect_unit"
                ],
                0,
            )
            conservative = report["conservative_cpp_static_storage_view"]
            self.assertEqual(
                conservative[
                    "excluded_simulator_only_response_line_shadow_bytes"
                ],
                0,
            )
            self.assertEqual(conservative["bounded_state_bytes"], 584492)
            self.assertEqual(
                conservative["comparable_configured_bytes"], 855340
            )
            allocated = report["allocated_model_storage_lower_bound"]
            self.assertEqual(
                allocated["retained_shared_descriptor_bytes"], 861120
            )
            self.assertEqual(allocated["native_total_bytes"], 3023808)
            self.assertEqual(allocated["configured_total_bytes"], 1459948)
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
            self.assertEqual(
                hardware["producer_line_metadata_bytes_per_entry"], 0
            )
            self.assertEqual(hardware["producer_identity_allocator_bytes"], 0)
            self.assertEqual(hardware["producer_line_metadata_bytes"], 0)
            self.assertEqual(
                hardware["shared_virtual_retirement_scoreboard_bytes"], 2824
            )
            self.assertEqual(hardware["total_bytes"], 9472)
            cpp = state["conservative_cpp_static_view"]
            self.assertEqual(cpp["queue_payload_bytes"], 4096)
            self.assertEqual(cpp["queue_control_bytes"], 17728)
            self.assertEqual(cpp["execution_bytes_per_record"], 456)
            self.assertEqual(cpp["request_bytes_per_record"], 72)
            self.assertEqual(cpp["retry_slots_bytes"], 32)
            self.assertEqual(cpp["producer_line_metadata_bytes_per_entry"], 0)
            self.assertEqual(cpp["producer_identity_allocator_bytes"], 0)
            self.assertEqual(cpp["producer_line_metadata_bytes"], 0)
            self.assertEqual(
                cpp["shared_virtual_retirement_scoreboard_bytes"], 2824
            )
            self.assertEqual(cpp["total_bytes"], 29984)
            self.assertEqual(
                report["bounded_state_lower_bound"][
                    "physical_spd_virtual_payload_and_control_bytes"
                ]
                - disabled_report["bounded_state_lower_bound"][
                    "physical_spd_virtual_payload_and_control_bytes"
                ],
                9472,
            )

            accepted_root = root / "accepted32"
            accepted_root.mkdir()
            accepted = self.write_config(
                accepted_root,
                4096,
                True,
                True,
                outstanding_writes=32,
            )
            result, output = self.run_report(
                accepted_root, accepted, "direct-index"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            accepted_state = json.loads(
                (output / "maa_storage.json").read_text()
            )["direct_retirement_line_handoff_state"]
            self.assertEqual(
                accepted_state["hardware_lower_bound"][
                    "shared_virtual_retirement_scoreboard_bytes"
                ],
                1416,
            )
            self.assertEqual(
                accepted_state["hardware_lower_bound"]["total_bytes"],
                9472,
            )
            self.assertEqual(
                accepted_state["conservative_cpp_static_view"]["total_bytes"],
                29984,
            )

    def test_fixed_direct_index_packed_storage_and_bounds(self) -> None:
        expected = {
            1: (358, 64 * 8),
            64: (20650, 64 * 64 * 8),
            128: (41259, 128 * 64 * 8),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for lines, (control_bits, payload_bits) in expected.items():
                with self.subTest(lines=lines):
                    case = root / str(lines)
                    case.mkdir()
                    config = self.write_config(
                        case,
                        4096,
                        True,
                        index_lines=lines,
                        index_issue_width=4,
                    )
                    result, output = self.run_report(
                        case, config, "direct-index"
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(
                        (output / "maa_storage.json").read_text()
                    )
                    control = report["incremental_virtual_control_lower_bound"]
                    self.assertEqual(
                        control["index_feeder_metadata_bits_per_line"], 322
                    )
                    self.assertEqual(
                        control[
                            "index_feeder_metadata_bits_per_indirect_unit"
                        ],
                        control_bits,
                    )
                    self.assertEqual(
                        control["index_feeder_logical_owner_bits_per_word"],
                        14,
                    )
                    self.assertEqual(
                        report["virtual_data_buffers"][
                            "configured_index_feeder_bytes_per_indirect_unit"
                        ]
                        * 8,
                        payload_bits,
                    )
                    self.assertEqual(
                        control["index_feeder_supported_capacity_lines"],
                        128,
                    )
                    self.assertEqual(
                        control["index_feeder_fixed_max_payload_bits"],
                        65536,
                    )
                    self.assertEqual(
                        control["index_feeder_fixed_max_control_bits"],
                        41259,
                    )
                    self.assertIn(
                        "excludes C++ sizeof",
                        control["index_feeder_packed_accounting_note"],
                    )

            invalid_lines = root / "invalid-lines"
            invalid_lines.mkdir()
            config = self.write_config(
                invalid_lines, 4096, True, index_lines=129
            )
            result, _ = self.run_report(invalid_lines, config, "direct-index")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be in [1,128]", result.stderr)

            invalid_width = root / "invalid-width"
            invalid_width.mkdir()
            config = self.write_config(
                invalid_width, 4096, True, index_issue_width=3
            )
            result, _ = self.run_report(invalid_width, config, "direct-index")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be 1, 2, or 4", result.stderr)

    def test_derived_combiner_pool_counts_global_victim_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(root, 4096, True, combine_words=0)
            result, output = self.run_report(root, config, "direct-index")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "maa_storage.json").read_text())
            control = report["incremental_virtual_control_lower_bound"]
            self.assertEqual(
                control[
                    "destination_combiner_global_payload_"
                    "victim_bits_per_indirect_unit"
                ],
                9,
            )

    def test_fp32_combiner_payload_uses_four_byte_words(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.write_config(root, 4096, True)
            result, output = self.run_report(
                root, config, "direct-index", word_bytes=4
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((output / "maa_storage.json").read_text())
            buffers = report["virtual_data_buffers"]
            self.assertEqual(
                buffers[
                    "configured_destination_combiner_bytes_per_indirect_unit"
                ],
                4096 * 4,
            )

    def test_dense_backing_bitmap_is_charged_once_per_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline"
            dense = root / "dense"
            baseline.mkdir()
            dense.mkdir()
            baseline_config = self.write_config(baseline, 4096, True)
            dense_config = self.write_config(
                dense, 4096, True, dense_write_allocate=True
            )
            baseline_result, baseline_output = self.run_report(
                baseline, baseline_config, "direct-index"
            )
            dense_result, dense_output = self.run_report(
                dense, dense_config, "direct-index"
            )
            self.assertEqual(baseline_result.returncode, 0)
            self.assertEqual(dense_result.returncode, 0)
            baseline_report = json.loads(
                (baseline_output / "maa_storage.json").read_text()
            )
            dense_report = json.loads(
                (dense_output / "maa_storage.json").read_text()
            )
            control = dense_report["incremental_virtual_control_lower_bound"]
            self.assertEqual(
                control["dense_backing_initialized_bits_per_indirect_unit"],
                2048,
            )
            self.assertEqual(
                control[
                    "dense_backing_initialized_fixed_max_bits_per_indirect_unit"
                ],
                2048,
            )
            self.assertEqual(
                dense_report["bounded_state_lower_bound"][
                    "physical_spd_virtual_payload_and_control_bytes"
                ]
                - baseline_report["bounded_state_lower_bound"][
                    "physical_spd_virtual_payload_and_control_bytes"
                ],
                256,
            )

    def test_complete_line_only_requires_bounded_explicit_pools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid"
            invalid = root / "invalid"
            valid.mkdir()
            invalid.mkdir()
            valid_config = self.write_config(
                valid,
                4096,
                True,
                response_pool=64,
                combine_words=1600,
                complete_line_only=True,
            )
            valid_result, output = self.run_report(
                valid, valid_config, "direct-index"
            )
            self.assertEqual(valid_result.returncode, 0, valid_result.stderr)
            report = json.loads((output / "maa_storage.json").read_text())
            self.assertEqual(
                report["incremental_virtual_control_lower_bound"][
                    "complete_line_only_control_bits_per_indirect_unit"
                ],
                1,
            )
            invalid_config = self.write_config(
                invalid,
                4096,
                True,
                response_pool=480,
                combine_words=4096,
                complete_line_only=True,
            )
            invalid_result, _ = self.run_report(
                invalid, invalid_config, "direct-index"
            )
            self.assertNotEqual(invalid_result.returncode, 0)
            self.assertIn(
                "requires explicit combiner/response word pools",
                invalid_result.stderr,
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
                "bounded_state": 648569,
                "comparable": 919417,
                "allocated": 1524025,
            },
            1024: {
                "payload_bits": 524288,
                "payload_and_output_bytes": 65600,
                "control_bits": 326528,
                "control_bytes": 40816,
                "combined_total_bits": 855148,
                "combined_total_bytes": 106894,
                "bounded_state": 700858,
                "comparable": 971706,
                "allocated": 1576314,
            },
            2048: {
                "payload_bits": 1048576,
                "payload_and_output_bytes": 131136,
                "control_bits": 638852,
                "control_bytes": 79857,
                "combined_total_bits": 1691760,
                "combined_total_bytes": 211470,
                "bounded_state": 805434,
                "comparable": 1076282,
                "allocated": 1680890,
            },
            4096: {
                "payload_bits": 2097152,
                "payload_and_output_bytes": 262208,
                "control_bits": 1263496,
                "control_bytes": 157937,
                "combined_total_bits": 3364980,
                "combined_total_bytes": 420623,
                "bounded_state": 1014587,
                "comparable": 1285435,
                "allocated": 1890043,
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
                buffers[
                    "excluded_cpp_response_line_shadow_bytes_per_indirect_unit"
                ],
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
                    "excluded_simulator_only_response_line_shadow_bytes"
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
