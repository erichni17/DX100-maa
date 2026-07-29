#!/usr/bin/env python3

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "dx_virt_contract.py"
)
SPEC = importlib.util.spec_from_file_location("dx_virt_contract", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_config(path: Path, overrides=None, units=(1, 1), caches=4):
    values = dict(MODULE.INTEGER_DEFAULTS)
    values.update(MODULE.BOOL_DEFAULTS)
    values.update(overrides or {})
    values["num_maas"], values["num_indirect_units_per_maa"] = units
    lines = ["[system.maa]", "type=MAA"]
    for key, value in values.items():
        if isinstance(value, bool):
            value = str(value).lower()
        lines.append(f"{key}={value}")
    for index in range(caches):
        lines.extend(
            [
                "",
                f"[system.maa_retirement_caches{index}]",
                "type=Cache",
                "size=1024",
                "assoc=4",
                "mshrs=16",
                "tgts_per_mshr=16",
                "write_buffers=16",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def case(mode, config="config.ini"):
    expected = MODULE.MODES[mode]
    return {
        "schema_version": 1,
        "case_id": f"test_{mode}",
        "mode": mode,
        "config_ini": config,
        "instruction": {
            "logical_iterations": 16384,
            "element_bytes": 4,
            "index_residency": expected["index_residency"],
            "result_residency": expected["result_residency"],
            "completion_tile_role": expected["completion_tile_role"],
        },
        "topology": {"row_table_effective_entries_per_row": 8},
    }


class ContractTests(unittest.TestCase):
    def build(self, root, mode, overrides=None, units=(1, 1), caches=4):
        config_path = root / "config.ini"
        write_config(config_path, overrides, units, caches)
        values, source = MODULE.load_config(config_path)
        return MODULE.build_contract(case(mode), values, source)

    def test_direct_index_contract_is_source_grounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            contract = self.build(
                Path(temporary),
                "direct_index_virtual",
                {
                    "physical_tile_elements": 4096,
                    "num_initial_row_table_slices": 16,
                    "virtual_combine_slots": 384,
                    "virtual_combine_words": 4096,
                    "virtual_combine_ways": 4,
                    "virtual_response_slots": 96,
                    "virtual_response_word_pool": 480,
                    "virtual_max_outstanding_writes": 64,
                    "virtual_index_buffer_lines": 4,
                    "virtual_index_partitions": 4,
                    "virtual_index_filter_words_per_cycle": 4,
                },
            )
        self.assertEqual(contract["configured_hardware"]["total_tiles"], 32)
        self.assertEqual(
            contract["reorder_resources"]["row_table_unique_line_capacity"],
            8192,
        )
        self.assertEqual(
            contract["reorder_resources"]["index_scan_policy"],
            "dram_grow_modulo",
        )
        self.assertEqual(
            contract["reorder_resources"][
                "direct_index_filter_words_per_cycle"
            ],
            4,
        )
        self.assertEqual(
            contract["reorder_resources"]["issue_order"],
            "bounded_row_id_scan",
        )
        self.assertIn(
            "does not preserve native",
            contract["reorder_resources"]["claim"],
        )
        self.assertEqual(
            contract["simulator_allocation"]["retirement_cache_data_bytes"],
            4096,
        )
        self.assertEqual(
            contract["target_hardware_budget"]["native_reference_bytes"],
            2162688,
        )

    def test_zero_semantics_match_gem5_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            contract = self.build(Path(temporary), "native", caches=0)
        hardware = contract["configured_hardware"]
        self.assertEqual(hardware["virtual_combine_words"], 0)
        self.assertEqual(hardware["virtual_response_word_pool"], 0)
        self.assertEqual(hardware["virtual_words_per_cycle"], 0)
        self.assertEqual(hardware["virtual_index_filter_words_per_cycle"], 0)

    def test_full_logical_index_line_budget_is_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract = self.build(
                root,
                "direct_index_virtual",
                {"virtual_index_buffer_lines": 1024},
            )
            self.assertEqual(
                contract["configured_hardware"]["virtual_index_buffer_lines"],
                1024,
            )
            with self.assertRaisesRegex(
                MODULE.ContractError, "virtual_index_buffer_lines"
            ):
                self.build(
                    root,
                    "direct_index_virtual",
                    {"virtual_index_buffer_lines": 1025},
                )

    def test_grow_order_is_qualified(self):
        with tempfile.TemporaryDirectory() as temporary:
            contract = self.build(
                Path(temporary),
                "direct_index_virtual",
                {"virtual_grow_order": True},
            )
        self.assertEqual(
            contract["reorder_resources"]["issue_order"],
            "bounded_grow_grouping",
        )
        self.assertIn("not equivalent", contract["reorder_resources"]["claim"])

    def test_invalid_victim_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(MODULE.ContractError, "victim_policy"):
                self.build(
                    Path(temporary),
                    "direct_index_virtual",
                    {"virtual_combine_victim_policy": 3},
                )

    def test_per_unit_structures_are_multiplied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            single = self.build(root, "direct_index_virtual", units=(1, 1))
            multi = self.build(root, "direct_index_virtual", units=(2, 3))
        self.assertEqual(
            multi["configured_hardware"]["total_indirect_units"], 6
        )
        self.assertEqual(
            multi["target_hardware_budget"][
                "all_indirect_units_minimum_bytes"
            ],
            6
            * single["target_hardware_budget"][
                "all_indirect_units_minimum_bytes"
            ],
        )

    def test_mode_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "config.ini"
            write_config(config_path)
            values, source = MODULE.load_config(config_path)
            manifest = case("direct_index_virtual")
            manifest["instruction"]["index_residency"] = "scratchpad"
            with self.assertRaisesRegex(MODULE.ContractError, "requires"):
                MODULE.build_contract(manifest, values, source)

    def test_cli_records_resolved_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_config(root / "config.ini", {"physical_tile_elements": 4096})
            manifest_path = root / "case.json"
            manifest_path.write_text(json.dumps(case("direct_index_virtual")))
            output = root / "contract.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--case",
                    str(manifest_path),
                    "--json",
                    str(output),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            contract = json.loads(output.read_text())
            self.assertEqual(len(contract["resolved_input_sha256"]), 64)
            self.assertEqual(contract["mode"], "direct_index_virtual")


if __name__ == "__main__":
    unittest.main()
