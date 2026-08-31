"""Contracts for the CG same-path strict-bit ablation."""

import json
import tempfile
import unittest
from pathlib import Path

from experiments.scripts import run_cg_strict_bit_ablation as runner


class CgStrictBitAblationTest(unittest.TestCase):
    def test_arm_uses_same_page_fed_selector_without_strict(self) -> None:
        self.assertEqual(runner.ARM.selector, "page_fed_product_soa_jit")
        self.assertFalse(runner.ARM.strict)

    def test_derived_command_removes_only_strict_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command = runner.derived_command(Path(directory))
        strict = json.loads(
            (
                runner.AUTHORITY / "arms/strict_two_pass/command.json"
            ).read_text()
        )
        self.assertNotIn(runner.STRICT_FLAG, command)
        self.assertIn(runner.STRICT_FLAG, strict)
        self.assertEqual(
            runner.base.normalize(command), runner.base.normalize(strict)
        )

    def test_authority_is_sealed_and_exact(self) -> None:
        authority = runner.verify_authority()
        strict = authority["arms"]["strict_two_pass"]
        self.assertTrue(strict["strict"])
        self.assertEqual(strict["values"]["simTicks"], 266_578_031)

    def test_ledger_rejects_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact"
            artifact.write_text("first\n")
            runner.write_ledger(root)
            runner.verify_ledger(root)
            artifact.write_text("second\n")
            with self.assertRaisesRegex(
                runner.AblationError, "artifact changed"
            ):
                runner.verify_ledger(root)


if __name__ == "__main__":
    unittest.main()
