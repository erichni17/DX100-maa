"""Contracts for the fresh GZZ matched-consumer matrix."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/run_ume_gzz_matched_consumer_matrix.py"
sys.path.insert(0, str(ROOT))

from experiments.scripts import (  # noqa: E402
    run_ume_gzz_matched_consumer_matrix as matrix,
)


class UmeGzzMatchedConsumerMatrixTest(unittest.TestCase):
    def test_exact_three_fresh_arms_share_one_guest(self) -> None:
        self.assertEqual(
            [arm.name for arm in matrix.ARMS],
            [
                "native16",
                "native4x4",
                "strict_logical16_physical4",
            ],
        )
        self.assertEqual({arm.guest for arm in matrix.ARMS}, {"matched"})
        self.assertEqual(
            [arm.arithmetic_pages for arm in matrix.ARMS], [1, 4, 4]
        )
        self.assertEqual(
            [arm.expected_vector_alus for arm in matrix.ARMS], [2, 8, 8]
        )
        self.assertFalse(matrix.ARMS[0].strict)
        self.assertFalse(matrix.ARMS[1].strict)
        self.assertTrue(matrix.ARMS[2].strict)

    def test_native_gather_semantics_are_not_virtualized(self) -> None:
        source = (ROOT / "benchmarks/UME/gradzatz.cpp").read_text()
        native_begin = source.index("void gradzatz_MAA_matched_native()")
        native_end = source.index("#ifdef VERIFY", native_begin)
        native = source[native_begin:native_end]
        self.assertIn("maa_indirect_load<DATATYPE>(", native)
        self.assertIn("point_gradient.data()", native)
        self.assertNotIn("maa_indirect_load_virtual", native)
        self.assertIn(
            "if (gzz_matched_uses_virtual_gather())\n"
            "        gradzatz_MAA();",
            source,
        )
        self.assertEqual(
            [arm.gather for arm in matrix.ARMS],
            ["native", "native", "virtual"],
        )

    def test_selector_is_exact_and_resolved_before_checkpoint(self) -> None:
        proof = matrix.source_contract()
        self.assertTrue(proof["selector_resolved_before_checkpoint"])
        source = (ROOT / "benchmarks/UME/gradzatz.cpp").read_text()
        self.assertLess(
            source.index(
                "read_gzz_matched_treatment(virtual_consumer_selector)"
            ),
            source.index("m5_checkpoint(0, 0)"),
        )
        for arm in matrix.ARMS:
            self.assertEqual(
                matrix.arm_options(Path(f"{arm.name}.selector")),
                f"16384 {arm.name}.selector",
            )

    def test_strict_flags_are_absent_from_native_commands(self) -> None:
        arguments = dict(
            gem5=Path("gem5.opt"),
            ramulator_config=Path("ramulator.yaml"),
            checkpoint=Path("checkpoint"),
            guest=Path("one_guest"),
            options="16384 selector",
            outdir=Path("run"),
        )
        commands = {
            arm.name: matrix.legacy.common_restore_command(
                **arguments, arm=arm
            )
            for arm in matrix.ARMS
        }
        strict = commands["strict_logical16_physical4"]
        for name in ("native16", "native4x4"):
            self.assertNotIn("--maa_virtual_strict_two_phase", commands[name])
            self.assertNotIn(
                "--maa_virtual_shared_result_payload", commands[name]
            )
        self.assertIn("--maa_virtual_strict_two_phase", strict)
        self.assertIn("--maa_virtual_shared_result_payload", strict)
        self.assertIn(
            "--maa_physical_tile_elements=16384", commands["native16"]
        )
        self.assertIn(
            "--maa_physical_tile_elements=4096", commands["native4x4"]
        )
        self.assertIn("--maa_physical_tile_elements=4096", strict)
        for command in commands.values():
            self.assertIn("one_guest", command)

    def test_build_uses_one_opt_in_guest(self) -> None:
        source = SCRIPT.read_text()
        self.assertIn('guest = build / "gradzatz_matched"', source)
        self.assertIn('"-DUME_GRADZATZ_MATCHED_PAGE_ARITHMETIC"', source)
        self.assertNotIn("gradzatz_native16", source)
        self.assertNotIn("native_controls_reused", source)

    def test_plan_is_nonexecuting_and_rejects_historical_controls(
        self,
    ) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(
            result["schema"], "dx100.ume_gzz_matched_consumer.plan.v1"
        )
        self.assertTrue(result["same_guest_binary"])
        self.assertFalse(result["historical_controls_reused"])
        self.assertEqual(
            result["acceptance"]["vector_alus_per_arithmetic_page"], 2
        )

    def test_rejection_is_terminal_and_not_promotable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matrix.record_rejection(root, "mechanism mismatch")
            failure = json.loads((root / "failure.json").read_text())
            self.assertEqual(failure["decision"], "REJECT")
            self.assertFalse(failure["performance_promotable"])
            self.assertEqual((root / "campaign.exit").read_text(), "1\n")

    def test_execute_requires_output_and_bounded_parallelism(self) -> None:
        with self.assertRaises(SystemExit):
            matrix.parse_args(["--execute"])
        with self.assertRaises(SystemExit):
            matrix.parse_args(["--max-parallel-restores", "4"])
        with self.assertRaises(SystemExit):
            matrix.parse_args(["--no-progress-seconds", "29"])

    def test_trace_progress_reader_does_not_scan_whole_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.log"
            trace.write_text("1: first\n2: second\n")
            self.assertEqual(matrix.last_trace_tick(trace), (2, 19))
            trace.write_text("not a trace line\n")
            self.assertIsNone(matrix.last_trace_tick(trace))


if __name__ == "__main__":
    unittest.main()
