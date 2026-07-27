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


class ContractTests(unittest.TestCase):
    def test_known_16k_on_4k_storage_contract(self):
        values = dict(MODULE.DEFAULTS)
        values.update(
            physical_tile_elements=4096,
            num_initial_row_table_slices=16,
            virtual_combine_slots=384,
            virtual_combine_words=4096,
            virtual_combine_ways=4,
            virtual_response_slots=96,
            virtual_response_word_pool=480,
            virtual_max_outstanding_writes=64,
            virtual_index_buffer_lines=4,
        )
        contract = MODULE.build_contract(values, {"kind": "test"}, 8)
        storage = contract["storage_bytes"]
        self.assertEqual(storage["native_logical"]["counted_total"], 2162688)
        self.assertEqual(
            storage["configured_virtual"]["virtual_retirement"]["total"],
            36864,
        )
        self.assertEqual(
            storage["configured_virtual"]["counted_total"], 577792
        )
        self.assertAlmostEqual(
            storage["reduction_vs_native"]["percent"], 73.2836174242
        )
        self.assertEqual(
            contract["reorder_contract"]["row_table_descriptor_capacity"],
            8192,
        )

    def test_config_ini_and_override_are_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.ini"
            path.write_text(
                "[system.maa]\n"
                "num_cores=4\n"
                "num_tiles_per_core=8\n"
                "num_tile_elements=16384\n"
                "physical_tile_elements=4096\n"
                "virtual_index_buffer_lines=4\n",
                encoding="utf-8",
            )
            values, source = MODULE.load_configuration(path)
            MODULE.apply_overrides(values, ["virtual_index_buffer_lines=8"])
            contract = MODULE.build_contract(values, source, None)
            self.assertEqual(
                contract["configuration"]["virtual_index_buffer_lines"], 8
            )
            self.assertEqual(source["section"], "system.maa")
            self.assertEqual(len(source["sha256"]), 64)

    def test_multiple_maa_sections_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.ini"
            path.write_text(
                "[system.maa]\nnum_tiles_per_core=8\nnum_tile_elements=16384\n"
                "[system.other_maa]\nnum_tiles_per_core=8\nnum_tile_elements=16384\n",
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.ContractError):
                MODULE.load_configuration(path)

    def test_invalid_physical_capacity_fails(self):
        values = dict(MODULE.DEFAULTS)
        values["physical_tile_elements"] = 32768
        with self.assertRaisesRegex(
            MODULE.ContractError, "physical_tile_elements exceeds"
        ):
            MODULE.build_contract(values, {"kind": "test"}, None)

    def test_cli_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "contract.json"
            markdown_path = root / "contract.md"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--set",
                    "physical_tile_elements=4096",
                    "--json",
                    str(json_path),
                    "--markdown",
                    str(markdown_path),
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(json_path.read_text())["schema_version"], 1
            )
            self.assertIn(
                "# Virtual Gather Contract", markdown_path.read_text()
            )


if __name__ == "__main__":
    unittest.main()
