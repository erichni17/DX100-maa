#!/usr/bin/env python3
"""Cell-parameterized poison-tail prerequisite tests."""

import importlib.util
import pathlib
import unittest

import umt_factorial_evidence as FACTORIAL

DRIVER_PATH = pathlib.Path(__file__).with_name(
    "run_umt_ordered_wave_poison_tail_smoke.py"
)
SPEC = importlib.util.spec_from_file_location(
    "umt_poison_tail_driver", DRIVER_PATH
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class FactorialPoisonTailEvidenceTest(unittest.TestCase):
    @staticmethod
    def cell(tokens=24, width=1):
        return FACTORIAL.FactorialCell(
            tokens, width, FACTORIAL.CELL_VARIANTS[(tokens, width)]
        )

    @classmethod
    def complete_d32_g16_stats(cls, tokens=24, width=1, line_reads=40):
        cell = cls.cell(tokens, width)
        stats = DRIVER.expected_stats([16], 4, cell)
        stats.update(
            {
                "descriptorUmtInputLineReads": line_reads,
                "lineTableHighWaterMark": 4,
                "controlReadRequests": 1,
                "controlStatusReads": 1,
                "controlOpcodeReads": 0,
                "controlErrorReads": 0,
                "descriptorUmtBatchCycles": 100,
                "descriptorUmtInputLineWaiterHoldLineCycles": 0,
                "descriptorUmtStateDualIssueCycles": 0 if width == 1 else 7,
                "descriptorUmtStateBankReadConflictCycles": 0,
                "descriptorUmtStateWritebackStallCycles": 0,
                "descriptorUmtStatePipelineResultBankStallCycles": 0,
                "descriptorUmtStateDividerNoLaneCycles": 0,
            }
        )
        return stats

    def test_d32_and_d64_matrices_are_explicit(self):
        self.assertEqual(DRIVER.D32_GROUP_COUNTS, (1, 7, 8, 9, 16, 24, 31, 32))
        self.assertEqual(
            DRIVER.D64_GROUP_COUNTS, (1, 7, 8, 9, 31, 32, 33, 63, 64)
        )
        DRIVER.validate_group_request(list(DRIVER.D32_GROUP_COUNTS), 4, True)
        DRIVER.validate_group_request(list(DRIVER.D64_GROUP_COUNTS), 5, False)
        with self.assertRaisesRegex(RuntimeError, "only for D32"):
            DRIVER.validate_group_request([7], 5, True)

    def test_calibration_is_bounded_and_cannot_pass_prerequisite(self):
        cell = self.cell(24, 2)
        stats = self.complete_d32_g16_stats(24, 2)
        expected, bounded = DRIVER.validate(
            stats, [16], 4, cell, calibration=True
        )
        self.assertNotIn("descriptorUmtInputLineReads", expected)
        self.assertEqual(bounded["descriptorUmtInputLineReads"], 40)
        self.assertEqual(bounded["minimumDescriptorUmtInputLineReads"], 32)
        self.assertEqual(bounded["maximumDescriptorUmtInputLineReads"], 256)
        disposition = DRIVER.validation_disposition(True, [16], 4)
        self.assertEqual(disposition["status"], "calibration_only")
        self.assertFalse(disposition["prerequisite_gate_passed"])
        diagnostic = DRIVER.validation_disposition(False, [16], 4)
        self.assertEqual(diagnostic["status"], "diagnostic_passed")
        self.assertFalse(diagnostic["prerequisite_gate_passed"])
        full = DRIVER.validation_disposition(
            False, list(DRIVER.D32_GROUP_COUNTS), 4
        )
        self.assertEqual(full["status"], "prerequisite_passed")
        self.assertTrue(full["prerequisite_gate_passed"])
        candidate = DRIVER.line_read_contract_candidate(
            stats, "a" * 64, cell, [16]
        )
        self.assertEqual(candidate["cell"], cell.document())
        self.assertEqual(candidate["descriptorUmtInputLineReads"], 40)

    def test_confirmation_requires_independent_exact_line_contract(self):
        cell = self.cell(24, 2)
        stats = self.complete_d32_g16_stats(24, 2)
        candidate = DRIVER.line_read_contract_candidate(
            stats, "a" * 64, cell, [16]
        )
        expected_line_reads = DRIVER.validate_line_read_contract(
            candidate, "a" * 64, cell, [16]
        )
        expected, _bounded = DRIVER.validate(
            stats,
            [16],
            4,
            cell,
            input_line_reads=expected_line_reads,
            calibration=False,
        )
        self.assertEqual(expected["descriptorUmtInputLineReads"], 40)
        changed = dict(stats)
        changed["descriptorUmtInputLineReads"] = 41
        with self.assertRaisesRegex(RuntimeError, "stat mismatch"):
            DRIVER.validate(
                changed,
                [16],
                4,
                cell,
                input_line_reads=expected_line_reads,
            )
        with self.assertRaisesRegex(RuntimeError, "requires a line-read"):
            DRIVER.validate(stats, [16], 4, cell)

    def test_line_contract_rejects_manifest_and_cell_mismatch(self):
        cell = self.cell(24, 2)
        stats = self.complete_d32_g16_stats(24, 2)
        candidate = DRIVER.line_read_contract_candidate(
            stats, "a" * 64, cell, [16]
        )
        with self.assertRaisesRegex(RuntimeError, "bind build manifest"):
            DRIVER.validate_line_read_contract(candidate, "b" * 64, cell, [16])
        with self.assertRaisesRegex(RuntimeError, "cell mismatches"):
            DRIVER.validate_line_read_contract(
                candidate, "a" * 64, self.cell(32, 2), [16]
            )

    def test_w1_zero_and_w2_positive_are_enforced_in_poison_gate(self):
        w1 = self.cell(24, 1)
        w1_stats = self.complete_d32_g16_stats(24, 1)
        DRIVER.validate(w1_stats, [16], 4, w1, calibration=True)
        w1_stats["descriptorUmtStateDualIssueCycles"] = 1
        with self.assertRaisesRegex(RuntimeError, "W1 cell"):
            DRIVER.validate(w1_stats, [16], 4, w1, calibration=True)

        w2 = self.cell(24, 2)
        w2_stats = self.complete_d32_g16_stats(24, 2)
        DRIVER.validate(w2_stats, [16], 4, w2, calibration=True)
        w2_stats["descriptorUmtStateDualIssueCycles"] = 0
        with self.assertRaisesRegex(RuntimeError, "did not exercise"):
            DRIVER.validate(w2_stats, [16], 4, w2, calibration=True)

    def test_split_pipeline_counters_are_required_and_bounded(self):
        cell = self.cell(24, 2)
        stats = self.complete_d32_g16_stats(24, 2)
        stats.update(
            {
                "descriptorUmtStateBankReadConflictCycles": 2,
                "descriptorUmtStateWritebackStallCycles": 3,
                "descriptorUmtStatePipelineResultBankStallCycles": 4,
                "descriptorUmtStateDividerNoLaneCycles": 4,
                "descriptorUmtBatchCycles": 5,
            }
        )
        _expected, bounded = DRIVER.validate(
            stats, [16], 4, cell, calibration=True
        )
        self.assertEqual(
            bounded["descriptorUmtStatePipelineResultBankStallCycles"], 4
        )

        split_names = (
            "descriptorUmtStateBankReadConflictCycles",
            "descriptorUmtStateWritebackStallCycles",
            "descriptorUmtStatePipelineResultBankStallCycles",
            "descriptorUmtStateDividerNoLaneCycles",
        )
        for name in split_names:
            with self.subTest(missing=name):
                invalid = dict(stats)
                del invalid[name]
                with self.assertRaisesRegex(
                    RuntimeError, "did not close|exceed active"
                ):
                    DRIVER.validate(invalid, [16], 4, cell, calibration=True)

        invalid_cases = (
            ("descriptorUmtStateBankReadConflictCycles", -1, "did not close"),
            ("descriptorUmtStateWritebackStallCycles", -1, "did not close"),
            (
                "descriptorUmtStatePipelineResultBankStallCycles",
                6,
                "did not close",
            ),
            ("descriptorUmtStateDividerNoLaneCycles", -1, "exceed active"),
            ("descriptorUmtStateDividerNoLaneCycles", 6, "exceed active"),
        )
        for name, value, message in invalid_cases:
            with self.subTest(name=name, value=value):
                invalid = dict(stats)
                invalid[name] = value
                with self.assertRaisesRegex(RuntimeError, message):
                    DRIVER.validate(invalid, [16], 4, cell, calibration=True)

    def test_all_cells_derive_token_high_water_and_cost(self):
        for tokens, width in FACTORIAL.CELL_VARIANTS:
            with self.subTest(tokens=tokens, width=width):
                cell = self.cell(tokens, width)
                expected = DRIVER.expected_stats([32], 4, cell, 64)
                self.assertEqual(
                    expected["descriptorUmtStateTokenHighWaterMark"], tokens
                )
                self.assertEqual(
                    expected["descriptorUmtStateFpIssueWidth"], width
                )
                self.assertEqual(
                    expected[
                        "descriptorUmtStatePhysicalStorePlusLogical"
                        "AuxiliaryBitsFloor"
                    ],
                    54372 if tokens == 24 else 58142,
                )


if __name__ == "__main__":
    unittest.main()
