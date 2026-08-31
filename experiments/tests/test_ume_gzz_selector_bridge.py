"""Contracts for GZZ hybrid-only selector recovery."""

import tempfile
import unittest
from pathlib import Path

from experiments.scripts import run_ume_gzz_selector_bridge as runner


class UmeGzzSelectorBridgeTest(unittest.TestCase):
    def test_only_failed_hybrid_arms_are_relaunched(self) -> None:
        self.assertEqual(
            [arm.name for arm in runner.HYBRID_ARMS],
            ["original_hybrid", "strict_bounded_hybrid"],
        )

    def test_fresh_checkpoint_options_bind_each_selector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for arm in runner.HYBRID_ARMS:
                selector = root / f"{arm.name}.selector"
                self.assertEqual(
                    runner.base.arm_options(arm, selector),
                    f"16384 {selector}",
                )

    def test_source_resolves_selector_before_checkpoint(self) -> None:
        source = (runner.ROOT / "benchmarks/UME/gradzatz.cpp").read_text()
        self.assertLess(
            source.index("maa_read_virtual_consumer_mode"),
            source.index("m5_checkpoint(0, 0)"),
        )

    def test_authority_native_controls_are_exact(self) -> None:
        authority = runner.verify_authority()
        self.assertEqual(
            authority["native"]["native16"]["output_hash"],
            runner.base.EXPECTED_OUTPUT_HASH,
        )

    def test_ledger_rejects_changed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "artifact"
            artifact.write_text("one\n")
            runner.write_ledger(root)
            runner.verify_ledger(root)
            artifact.write_text("two\n")
            with self.assertRaisesRegex(
                runner.BridgeError, "artifact changed"
            ):
                runner.verify_ledger(root)


if __name__ == "__main__":
    unittest.main()
