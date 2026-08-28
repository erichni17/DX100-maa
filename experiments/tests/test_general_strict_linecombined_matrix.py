"""Contract tests for the cross-application strict/line-combined audit."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/run_general_strict_linecombined_matrix.py"
sys.path.insert(0, str(ROOT))

from experiments.scripts import (
    run_general_strict_linecombined_matrix as matrix,
)


class GeneralStrictLinecombinedMatrixTest(unittest.TestCase):
    def test_source_contract_binds_default_off_production_paths(self) -> None:
        result = matrix.source_contract()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            set(result["files"]),
            {
                "simulator",
                "instruction",
                "instruction_access",
                "parameters",
                "old_result",
                "api",
                "is",
                "hashjoin",
                "sssp",
            },
        )
        self.assertTrue(
            all(len(item["sha256"]) == 64 for item in result["files"].values())
        )

    def test_is_and_hashjoin_have_no_virtual_result_or_old_result(
        self,
    ) -> None:
        rows = {row["family"]: row for row in matrix.application_matrix()}
        for family in ("is", "hashjoin-pro", "hashjoin-prh"):
            row = rows[family]
            self.assertFalse(row["strict_plus_masked_applicable"])
            self.assertTrue(
                row["numeric_b"][
                    "private_feeder_copy_dead_after_descriptor_insert"
                ]
            )
            self.assertFalse(row["result_semantics"]["old_result_required"])
            self.assertFalse(
                row["result_semantics"]["result_backing_required"]
            )
            self.assertEqual(
                row["masked_64b_retirement"]["classification"],
                "not_applicable_no_virtual_result",
            )
            self.assertIsNone(row["masked_64b_retirement"]["masked_64b_legal"])

    def test_sssp_keeps_application_backing_and_distinct_masked_results(
        self,
    ) -> None:
        row = next(
            item
            for item in matrix.application_matrix()
            if item["family"] == "sssp"
        )
        self.assertTrue(row["numeric_b"]["application_reads_after_completion"])
        self.assertTrue(row["result_semantics"]["old_result_required"])
        self.assertTrue(row["result_semantics"]["result_backing_required"])
        self.assertEqual(
            row["masked_64b_retirement"]["classification"],
            "legal_distinct_old_result_publisher",
        )
        self.assertTrue(row["masked_64b_retirement"]["masked_64b_legal"])
        self.assertFalse(
            row["masked_64b_retirement"]["cg_virtual_retirement_applicable"]
        )

    def test_default_result_authorizes_no_full_launch(self) -> None:
        result = matrix.build_result(matrix.DEFAULT_EVIDENCE, False)
        self.assertEqual(result["native_runs"], 0)
        self.assertEqual(result["candidate_full_runs"], 0)
        self.assertEqual(result["applicable_full_families"], [])
        self.assertEqual(
            result["launch_policy"]["decision"], "NO_NEW_FULL_LAUNCH"
        )
        self.assertEqual(result["evidence_validation"], "not_requested")

    def test_explicit_full_request_fails_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "matrix.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--launch-full",
                    "sssp",
                    "--output",
                    str(output),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn(
                "full launch rejected before execution", completed.stderr
            )
            self.assertIn("old-result backing", completed.stderr)
            self.assertFalse(output.exists())

    def test_output_is_atomic_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "matrix.json"
            first = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(
                json.loads(output.read_text())["schema"],
                "dx100.general_strict_linecombined_matrix.v1",
            )
            second = subprocess.run(
                [sys.executable, str(SCRIPT), "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(second.returncode, 1)
            self.assertIn("refusing existing output", second.stderr)


if __name__ == "__main__":
    unittest.main()
