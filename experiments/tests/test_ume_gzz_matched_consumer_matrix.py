"""Contracts for the GZZ matched MAA-consumer matrix."""

import unittest
from pathlib import Path

from experiments.scripts import run_ume_gzz_matched_consumer_matrix as matrix


class UmeGzzMatchedConsumerMatrixTest(unittest.TestCase):
    def test_matrix_has_exact_three_attribution_arms(self) -> None:
        self.assertEqual(
            [arm.name for arm in matrix.ARMS],
            ["native16", "native4", "strict_bounded_hybrid"],
        )

    def test_guest_builder_accepts_common_consumer_define(self) -> None:
        source = (
            matrix.ROOT / "experiments/scripts/run_ume_two_pass_matrix.py"
        ).read_text()
        self.assertIn("common_defines: tuple[str, ...] = ()", source)
        self.assertIn("*common_defines", source)

    def test_native_and_virtual_paths_use_maa_page_arithmetic(self) -> None:
        source = (matrix.ROOT / "benchmarks/UME/gradzatz.cpp").read_text()
        for token in (
            "UME_GZZ_MAA_PAGE_CONSUMER",
            "page_ratio_tiles",
            "page_product_tiles",
            "Operation_t::DIV_OP",
            "Operation_t::MUL_OP",
            "UME_GZZ_PAGE_CONSUMER mode=maa_div_mul",
        ):
            self.assertIn(token, source)

    def test_runner_freezes_all_inputs_and_requires_exact_output(self) -> None:
        source = Path(matrix.__file__).read_text()
        for token in (
            "base.copy_stable(gem5, frozen_gem5)",
            "base.copy_stable(ramulator, frozen_ramulator)",
            "base.copy_stable(ramulator_config, frozen_config)",
            "base.classify_arm(root, arm, manifest)",
            '"instruction_consumer_matched": True',
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
