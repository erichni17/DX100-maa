"""Contract tests for the fresh same-guest CG four-arm matrix."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "cg_fourarm", ROOT / "experiments/scripts/run_cg_strict_fourarm_matrix.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CgFourArmContractTest(unittest.TestCase):
    def test_four_distinct_treatments_and_small_only(self) -> None:
        self.assertEqual(
            [arm.name for arm in MODULE.ARMS],
            ["native16", "native4x4", "original_hybrid", "strict_two_pass"],
        )
        self.assertEqual(
            {arm.selector for arm in MODULE.ARMS},
            {
                "native_16k",
                "native_4kx4",
                "legacy_4k",
                "page_fed_product_soa_jit",
            },
        )
        self.assertEqual(MODULE.CG_NA, 256)

    def test_projection_hides_only_declared_treatment_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = {
                arm.name: MODULE.common_command(
                    root / "gem5.opt",
                    root / arm.name,
                    root / "checkpoint",
                    root / "guest",
                    root / "ramulator.yaml",
                    arm.physical,
                    arm.strict,
                    arm.strict,
                )
                for arm in MODULE.ARMS
            }
        projected = {
            name: json.dumps(MODULE.normalize(command))
            for name, command in commands.items()
        }
        self.assertEqual(len(set(projected.values())), 1)
        self.assertIn(
            "--maa_virtual_strict_two_phase", commands["strict_two_pass"]
        )
        self.assertNotIn(
            "--maa_virtual_strict_two_phase", commands["native16"]
        )
        self.assertIn(
            "--maa_virtual_complete_line_payload_banks=32",
            commands["strict_two_pass"],
        )

    def test_native_selector_is_source_grounded(self) -> None:
        source = (ROOT / "benchmarks/NAS/cg/cg.cpp").read_text()
        for selector in (
            "native_16k",
            "native_4kx4",
            "legacy_4k",
            "page_fed_product_soa_jit",
        ):
            self.assertIn(f'treatment == "{selector}"', source)
        self.assertIn("cg_uses_native_direct()", source)

    def test_storage_bound_is_explicit(self) -> None:
        self.assertLessEqual(256 + 8 * 16, 4096)
        self.assertEqual(
            MODULE.SELECTOR_TARGET,
            "/tmp/cg_strict_fourarm_selector_20260831",
        )

    def test_terminal_parser_requires_exact_m5_and_reductions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restore.log"
            path.write_text(
                "Exiting @ tick 1 because m5_exit instruction encountered\n"
                "ROI End!!!\n"
            )
            with self.assertRaisesRegex(MODULE.MatrixError, "fingerprint"):
                MODULE.parse_terminal(path, "native_16k")


if __name__ == "__main__":
    unittest.main()
