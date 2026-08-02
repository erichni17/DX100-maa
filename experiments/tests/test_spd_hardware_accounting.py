#!/usr/bin/env python3
"""Regression checks for the source-checked SPD accounting ledger."""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments" / "analysis" / "spd_hardware_accounting.py"
SPEC = importlib.util.spec_from_file_location("spd_accounting", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class SpdHardwareAccountingTest(unittest.TestCase):
    def test_default_native_and_transparent_payloads(self) -> None:
        report = MODULE.ledger(4, 8)
        payload = report["spd_payload"]
        self.assertEqual(payload["native_16k_lane_tile_bytes"], 65536)
        self.assertEqual(payload["native_16k_total_bytes"], 2097152)
        self.assertEqual(payload["native_4k_total_bytes"], 524288)
        self.assertEqual(
            payload["transparent_16k_logical_4k_physical_total_bytes"], 524288
        )
        self.assertEqual(
            payload["logical_aperture_bytes_per_address_range"], 2097152
        )

    def test_fp64_uses_two_lane_tiles_and_two_stages_are_64kib(self) -> None:
        fp64 = MODULE.ledger(1, 1)["fp64_tile_semantics"]
        self.assertEqual(fp64["physical_lane_tiles_per_fp64_tile"], 2)
        self.assertEqual(fp64["one_4k_fp64_tile_bytes"], 32768)
        self.assertEqual(fp64["two_4k_fp64_staging_tiles_bytes"], 65536)

    def test_direct_has_one_more_cache_line_than_generic_virtual(self) -> None:
        virtual = MODULE.ledger(1, 1)["selected_virtual_data_capacity"]
        self.assertEqual(
            virtual["generic_virtual_data_bytes_per_indirect_unit"], 1536
        )
        self.assertEqual(
            virtual["direct_virtual_data_bytes_per_indirect_unit"], 1600
        )

    def test_consumer_experiment_totals_across_four_indirect_units(
        self,
    ) -> None:
        consumer = MODULE.ledger(1, 1)["named_virtual_points"][
            "matched_virtual_tile_consumer_experiment"
        ]
        self.assertEqual(consumer["response_slots"], 96)
        self.assertEqual(consumer["combiner_slots"], 384)
        self.assertEqual(consumer["direct_index_lines"], 4)
        self.assertEqual(
            consumer["direct_virtual_data_bytes_per_indirect_unit"], 30976
        )
        self.assertEqual(
            consumer["direct_virtual_data_bytes_all_indirect_units"], 123904
        )

    def test_selected_configuration_is_parameterized(self) -> None:
        virtual = MODULE.ledger(1, 1, 96, 384, 4, 2)[
            "selected_virtual_data_capacity"
        ]
        self.assertEqual(virtual["indirect_units"], 2)
        self.assertEqual(
            virtual["direct_virtual_data_bytes_all_indirect_units"], 61952
        )

    def test_bad_topology_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.ledger(0, 8)


if __name__ == "__main__":
    unittest.main()
