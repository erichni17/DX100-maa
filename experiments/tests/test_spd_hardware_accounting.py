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
        self.assertEqual(report["configuration"]["spd_lane_tiles"], 32)
        self.assertEqual(report["configuration"]["maas"], 4)
        payload = report["spd_payload"]
        self.assertEqual(payload["native_16k_lane_tile_bytes"], 65536)
        self.assertEqual(payload["native_16k_total_bytes"], 2097152)
        self.assertEqual(payload["native_4k_total_bytes"], 524288)
        self.assertEqual(
            payload["transparent_visible_spd_payload_bytes"], 524288
        )
        self.assertEqual(
            payload["private_logical_spd_payload_bytes_per_maa"], 32768
        )
        self.assertEqual(payload["private_logical_spd_payload_bytes"], 131072)
        self.assertEqual(
            payload[
                "packed_private_logical_spd_metadata_lower_bound_bytes_per_maa"
            ],
            1309,
        )
        self.assertEqual(
            payload["packed_private_logical_spd_metadata_lower_bound_bytes"],
            5236,
        )
        self.assertEqual(
            payload["transparent_visible_plus_private_payload_bytes"],
            655360,
        )
        self.assertEqual(
            payload["transparent_payload_reduction_bytes"], 1441792
        )
        self.assertEqual(
            payload["transparent_payload_reduction_percent"], 68.75
        )
        self.assertEqual(
            payload["logical_aperture_bytes_per_address_range"], 2097152
        )

    def test_private_payload_is_parameterized_by_maa_not_visible_tiles(
        self,
    ) -> None:
        one_maa = MODULE.ledger(4, 8, maas=1)["spd_payload"]
        four_maas = MODULE.ledger(4, 8, maas=4)["spd_payload"]
        self.assertEqual(
            one_maa["transparent_visible_spd_payload_bytes"],
            four_maas["transparent_visible_spd_payload_bytes"],
        )
        self.assertEqual(one_maa["private_logical_spd_payload_bytes"], 32768)
        self.assertEqual(
            four_maas["private_logical_spd_payload_bytes"], 4 * 32768
        )
        self.assertEqual(
            four_maas["transparent_visible_plus_private_payload_bytes"],
            524288 + 4 * 32768,
        )

    def test_fp64_uses_two_lane_tiles_and_two_stages_are_64kib(self) -> None:
        fp64 = MODULE.ledger(1, 1)["fp64_tile_semantics"]
        self.assertEqual(fp64["physical_lane_tiles_per_fp64_tile"], 2)
        self.assertEqual(fp64["one_4k_fp64_tile_bytes"], 32768)
        self.assertEqual(fp64["two_4k_fp64_staging_tiles_bytes"], 65536)

    def test_direct_has_one_more_cache_line_than_generic_virtual(self) -> None:
        virtual = MODULE.ledger(1, 1)["selected_virtual_data_capacity"]
        self.assertEqual(
            virtual["generic_virtual_data_bytes_per_indirect_unit"], 2560
        )
        self.assertEqual(
            virtual["direct_virtual_data_bytes_per_indirect_unit"], 2624
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
        self.assertEqual(consumer["response_payload_mode"], "packed-word-pool")
        self.assertEqual(
            consumer["response_packed_word_bytes_per_indirect_unit"], 1920
        )
        self.assertEqual(
            consumer["inactive_response_line_bytes_per_indirect_unit"], 0
        )
        self.assertEqual(
            consumer["direct_virtual_data_bytes_per_indirect_unit"], 34944
        )
        self.assertEqual(
            consumer["direct_virtual_data_bytes_all_indirect_units"], 139776
        )

    def test_selected_configuration_is_parameterized(self) -> None:
        virtual = MODULE.ledger(1, 1, 96, 384, 4, 2)[
            "selected_virtual_data_capacity"
        ]
        self.assertEqual(virtual["indirect_units"], 2)
        self.assertEqual(
            virtual["direct_virtual_data_bytes_all_indirect_units"], 111104
        )

    def test_configured_combiner_payload_is_independent_of_line_tags(
        self,
    ) -> None:
        points = [
            MODULE.ledger(1, 1, combine_slots=slots, combine_words=4096)[
                "selected_virtual_data_capacity"
            ]
            for slots in (512, 1024, 2048)
        ]
        self.assertEqual(
            {
                point["combiner_payload_pool_bytes_per_indirect_unit"]
                for point in points
            },
            {32768},
        )
        self.assertEqual(
            [
                point["combiner_line_metadata_bits_per_indirect_unit"]
                for point in points
            ],
            [139776, 279552, 559104],
        )
        self.assertEqual(
            {point["combiner_reference_bits"] for point in points}, {12}
        )
        self.assertEqual(
            [
                point[
                    "combiner_simulator_reference_array_bits_per_indirect_unit"
                ]
                for point in points
            ],
            [262144, 524288, 1048576],
        )
        self.assertEqual(
            {
                point[
                    "combiner_simulator_pool_bookkeeping_bits_"
                    "per_indirect_unit"
                ]
                for point in points
            },
            {294912},
        )

    def test_packed_response_modes_exclude_fixed_line_payloads(self) -> None:
        pooled = MODULE.virtual_data_capacity(128, 384, 8, 4, 0, 480, 8)
        self.assertEqual(pooled["response_payload_mode"], "packed-word-pool")
        self.assertEqual(
            pooled["response_payload_bytes_per_indirect_unit"], 3840
        )
        self.assertEqual(pooled["response_line_bytes_per_indirect_unit"], 0)
        self.assertEqual(
            pooled["inactive_response_line_bytes_per_indirect_unit"], 0
        )

        fixed = MODULE.virtual_data_capacity(8, 16, 1, 4, 3, 0, 4)
        self.assertEqual(
            fixed["response_payload_mode"], "packed-words-per-slot"
        )
        self.assertEqual(fixed["response_payload_bytes_per_indirect_unit"], 96)
        self.assertEqual(fixed["response_line_bytes_per_indirect_unit"], 0)

        unpacked = MODULE.virtual_data_capacity(8, 16, 1, 4)
        self.assertEqual(
            unpacked["response_payload_mode"], "unpacked-fixed-lines"
        )
        self.assertEqual(
            unpacked["response_line_bytes_per_indirect_unit"], 512
        )
        self.assertEqual(
            unpacked["response_payload_bytes_per_indirect_unit"], 512
        )

    def test_bad_topology_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MODULE.ledger(0, 8)
        with self.assertRaises(ValueError):
            MODULE.ledger(4, 8, maas=0)


if __name__ == "__main__":
    unittest.main()
