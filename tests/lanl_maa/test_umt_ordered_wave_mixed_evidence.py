#!/usr/bin/env python3
"""Standalone oracle/accounting tests for the mixed UMT evidence gate."""

import importlib.util
import pathlib
import unittest

import umt_factorial_evidence as FACTORIAL

DRIVER_PATH = pathlib.Path(__file__).with_name(
    "run_umt_ordered_wave_mixed_evidence_smoke.py"
)
SPEC = importlib.util.spec_from_file_location(
    "umt_mixed_evidence_driver", DRIVER_PATH
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class MixedUmtEvidenceTest(unittest.TestCase):
    @staticmethod
    def cell(tokens=32, width=2):
        return FACTORIAL.FactorialCell(
            tokens, width, FACTORIAL.CELL_VARIANTS[(tokens, width)]
        )

    def test_interleaved_terminal_sequence_is_fixed(self):
        self.assertEqual(
            [
                (case.abi_version, case.groups, case.expect_error)
                for case in DRIVER.CASES
            ],
            [
                (4, 32, False),
                (5, 64, False),
                (4, 9, False),
                (5, 33, False),
                (4, 8, True),
            ],
        )

    def test_dense_graph_is_twelve_unique_forward_edges(self):
        dense_indices = [
            DRIVER.dense_index(source, destination)
            for source, destination, _coefficient in DRIVER.EDGES
        ]
        self.assertEqual(len(DRIVER.EDGES), 12)
        self.assertEqual(len(set(dense_indices)), 12)
        self.assertEqual(
            dense_indices,
            [0, 1, 3, 7, 8, 13, 15, 18, 20, 22, 25, 27],
        )
        self.assertEqual(DRIVER.edge_mask(), 0x0A54A18B)

    def test_scalar_oracle_pins_forward_rmw_bits(self):
        observed = [
            DRIVER.double_bits(value) for value in DRIVER.scalar_oracle(0, 0)
        ]
        self.assertEqual(
            observed,
            [
                0x4010000000000000,
                0x4013000000000000,
                0x4014900000000000,
                0x4011E90000000000,
                0x4011245000000000,
                0x4016B6C500000000,
                0x40178769D8000000,
                0x4019F0ED3B000000,
            ],
        )
        self.assertEqual(
            DRIVER.oracle_sha256(0, 32),
            "608c718f080f4d3c31d0fc28afdadfe6bcf547e424d2f4e11a374bd42294221b",
        )

    def test_exact_traffic_fp_occupancy_and_cost_ledger(self):
        expected = DRIVER.exact_stats(self.cell())
        self.assertEqual(expected["descriptorDoorbells"], 5)
        self.assertEqual(expected["descriptorRearms"], 4)
        self.assertEqual(expected["descriptorCompletionWrites"], 4)
        self.assertEqual(expected["descriptorErrors"], 1)
        self.assertEqual(expected["descriptorUmtInputReads"], 2280)
        self.assertNotIn("descriptorUmtInputLineReads", expected)
        self.assertIn(
            "descriptorUmtInputLineReads", DRIVER.TIMING_COUNTER_REASONS
        )
        self.assertEqual(expected["descriptorUmtStateInputWrites"], 1168)
        self.assertEqual(
            expected["descriptorUmtStateDenominatorsConsumed"], 1111
        )
        self.assertEqual(expected["descriptorUmtResultLineWrites"], 152)
        self.assertEqual(expected["descriptorUmtFp64AddSubOperations"], 2760)
        self.assertEqual(expected["descriptorUmtFp64MultiplyOperations"], 1656)
        self.assertEqual(expected["descriptorUmtFp64DivideOperations"], 1104)
        self.assertEqual(
            expected["descriptorUmtStateFpOperationsIssued"], 5520
        )
        self.assertEqual(expected["descriptorUmtStateStoreHighWaterMark"], 64)
        self.assertEqual(expected["descriptorUmtStateBankHighWaterMark"], 16)
        self.assertEqual(expected["descriptorUmtStateTokenHighWaterMark"], 32)
        self.assertEqual(
            expected["descriptorUmtStatePhysicalStoreBytes"], 5120
        )
        self.assertEqual(
            expected["descriptorUmtStateInstrumentationLogicalBitsFloor"],
            1170,
        )
        self.assertEqual(
            expected["descriptorUmtStateAuxiliaryLogicalBitsFloor"], 17182
        )
        self.assertEqual(
            expected[
                "descriptorUmtStatePhysicalStorePlusLogicalAuxiliaryBitsFloor"
            ],
            58142,
        )

    @classmethod
    def complete_fake_stats(cls, tokens=32, width=2):
        stats = dict(DRIVER.exact_stats(cls.cell(tokens, width)))
        for name in DRIVER.TIMING_COUNTER_REASONS:
            stats[name] = 1
        stats["descriptorUmtBatchCycles"] = 6000
        stats["descriptorUmtStateFpIssueStallCycles"] = 1
        stats["descriptorUmtStateDualIssueCycles"] = 0 if width == 1 else 1
        stats["lineTableHighWaterMark"] = 32
        stats["controlStatusReads"] = 17
        stats["controlReadRequests"] = 18
        return stats

    @classmethod
    def timing_contract(cls, stats, build_sha="a" * 64, tokens=32, width=2):
        return {
            "schema": DRIVER.TIMING_CONTRACT_SCHEMA,
            "build_manifest_sha256": build_sha,
            "cell": cls.cell(tokens, width).document(),
            "counters": {
                name: stats[name] for name in DRIVER.TIMING_COUNTER_REASONS
            },
        }

    @classmethod
    def build_manifest(cls):
        cell = cls.cell()
        headers = {}
        for label, symbol in FACTORIAL.CONFIG_SYMBOLS.items():
            headers[label] = {
                "path": (
                    f"/source/build/{cell.variant}/config/"
                    f"{symbol.lower()}.hh"
                ),
                "sha256": "7" * 64,
                "symbol": symbol,
                "value": getattr(cell, label),
            }
        return {
            "schema": FACTORIAL.BUILD_MANIFEST_SCHEMA,
            "status": "passed",
            "cell": cell.document(),
            "source_commit": "1" * 40,
            "source_tree": "2" * 40,
            "source_clean_before_and_after": True,
            "source_identity_unchanged": True,
            "command": [
                str(pathlib.Path("/usr/bin/scons").resolve()),
                "--ignore-style",
                f"build/{cell.variant}/gem5.opt",
                "-j4",
            ],
            "returncode": 0,
            "started_at": "2026-08-09T00:00:00+00:00",
            "ended_at": "2026-08-09T00:01:00+00:00",
            "required_relink_observed": True,
            "build_opts": f"/source/build_opts/{cell.variant}",
            "build_opts_sha256": "8" * 64,
            "kconfig_state": f"/source/build/{cell.variant}/gem5.build/config",
            "kconfig_state_sha256": "9" * 64,
            "generated_config_headers": headers,
            "target": f"/source/build/{cell.variant}/gem5.opt",
            "target_size": 1,
            "target_mtime_ns": 1,
            "gem5_sha256": "3" * 64,
            "frozen_gem5": "/identity/gem5.opt",
            "frozen_gem5_sha256": "3" * 64,
            "stdout_sha256": "4" * 64,
            "stderr_sha256": "5" * 64,
            "builder_sha256": "6" * 64,
            "claim_boundary": "local exact build",
        }

    def test_confirmation_requires_exact_external_timing_contract(self):
        cell = self.cell()
        stats = self.complete_fake_stats()
        contract = self.timing_contract(stats)
        exact, timing = DRIVER.validate_confirmation(
            stats, contract, "a" * 64, cell
        )
        self.assertEqual(exact, DRIVER.exact_stats(cell))
        self.assertEqual(set(timing), set(DRIVER.TIMING_COUNTER_REASONS))
        self.assertTrue(
            all(
                item["expected"] == item["observed"]
                for item in timing.values()
            )
        )

    def test_confirmation_rejects_positive_but_changed_timing(self):
        cell = self.cell()
        stats = self.complete_fake_stats()
        contract = self.timing_contract(stats)
        stats["descriptorUmtStateFpIssueStallCycles"] = 2
        with self.assertRaisesRegex(
            RuntimeError, "exact timing stat mismatch"
        ):
            DRIVER.validate_confirmation(stats, contract, "a" * 64, cell)

    def test_split_pipeline_counters_are_required_and_close(self):
        split_names = (
            "descriptorUmtStateBankReadConflictCycles",
            "descriptorUmtStateWritebackStallCycles",
            "descriptorUmtStatePipelineResultBankStallCycles",
            "descriptorUmtStateDividerNoLaneCycles",
        )
        for name in split_names:
            with self.subTest(missing=name):
                stats = self.complete_fake_stats()
                del stats[name]
                with self.assertRaisesRegex(RuntimeError, "absent"):
                    DRIVER.observed_timing_counters(stats, self.cell())

        stats = self.complete_fake_stats()
        stats["descriptorUmtStateBankReadConflictCycles"] = 2
        stats["descriptorUmtStateWritebackStallCycles"] = 3
        stats["descriptorUmtStatePipelineResultBankStallCycles"] = 4
        stats["descriptorUmtStateDividerNoLaneCycles"] = 4
        stats["descriptorUmtBatchCycles"] = 6000
        observed = DRIVER.observed_timing_counters(stats, self.cell())
        self.assertEqual(
            observed["descriptorUmtStatePipelineResultBankStallCycles"], 4
        )

        invalid_cases = (
            ("descriptorUmtStateBankReadConflictCycles", -1, "unique-cycle"),
            (
                "descriptorUmtStateBankReadConflictCycles",
                6001,
                "pipeline-active",
            ),
            ("descriptorUmtStateWritebackStallCycles", -1, "unique-cycle"),
            (
                "descriptorUmtStateWritebackStallCycles",
                6001,
                "pipeline-active",
            ),
            (
                "descriptorUmtStatePipelineResultBankStallCycles",
                6001,
                "pipeline-active",
            ),
            ("descriptorUmtStateDividerNoLaneCycles", -1, "unique-cycle"),
            (
                "descriptorUmtStateDividerNoLaneCycles",
                6001,
                "pipeline-active",
            ),
        )
        for name, value, message in invalid_cases:
            with self.subTest(name=name, value=value):
                invalid = dict(stats)
                invalid[name] = value
                with self.assertRaisesRegex(RuntimeError, message):
                    DRIVER.observed_timing_counters(invalid, self.cell())

    def test_impossible_unique_cycle_ledger_fails_closed(self):
        stats = self.complete_fake_stats()
        stats.update(
            {
                "descriptorUmtBatchCycles": 5,
                "descriptorUmtStateFpOperationsIssued": 5520,
                "descriptorUmtStateDualIssueCycles": 3000,
                "descriptorUmtStateBankReadConflictCycles": 6,
                "descriptorUmtStateWritebackStallCycles": 6,
                "descriptorUmtStatePipelineResultBankStallCycles": 6,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "dual-issue cycles exceed"):
            DRIVER.observed_timing_counters(stats, self.cell())

    def test_unique_cycle_boundary_equality_is_valid(self):
        stats = self.complete_fake_stats()
        stats.update(
            {
                "descriptorUmtBatchCycles": 5520,
                "descriptorUmtStateDualIssueCycles": 1,
                "descriptorUmtStateFpIssueStallCycles": 1,
                "descriptorUmtStateBankReadConflictCycles": 5,
                "descriptorUmtStateWritebackStallCycles": 5,
                "descriptorUmtStatePipelineResultBankStallCycles": 5,
                "descriptorUmtStateDividerNoLaneCycles": 5,
            }
        )
        observed = DRIVER.observed_timing_counters(stats, self.cell())
        self.assertEqual(observed["descriptorUmtStateDualIssueCycles"], 1)

    def test_issue_cycle_and_zero_stall_occupancy_fail_closed(self):
        stats = self.complete_fake_stats()
        stats["descriptorUmtBatchCycles"] = 1
        with self.assertRaisesRegex(RuntimeError, "issue-cycle ledger"):
            DRIVER.observed_timing_counters(stats, self.cell())

        stats["descriptorUmtBatchCycles"] = 5519
        with self.assertRaisesRegex(RuntimeError, "issue-cycle ledger"):
            DRIVER.observed_timing_counters(stats, self.cell())

        stats["descriptorUmtBatchCycles"] = 5520
        stats["descriptorUmtStateFpIssueStallCycles"] = 2
        with self.assertRaisesRegex(RuntimeError, "issue-cycle ledger"):
            DRIVER.observed_timing_counters(stats, self.cell())

    def test_zero_issue_stall_counter_fails_closed(self):
        for value in (None, "1", -1):
            with self.subTest(value=value):
                stats = self.complete_fake_stats()
                if value is None:
                    del stats["descriptorUmtStateFpIssueStallCycles"]
                else:
                    stats["descriptorUmtStateFpIssueStallCycles"] = value
                with self.assertRaisesRegex(
                    RuntimeError, "zero-issue stall|timing counter"
                ):
                    DRIVER.observed_timing_counters(stats, self.cell())

    def test_pipeline_active_counter_fails_closed(self):
        for value in (None, "5", -1):
            with self.subTest(value=value):
                stats = self.complete_fake_stats()
                if value is None:
                    del stats["descriptorUmtBatchCycles"]
                else:
                    stats["descriptorUmtBatchCycles"] = value
                with self.assertRaisesRegex(
                    RuntimeError, "absent or noninteger|pipeline-active"
                ):
                    DRIVER.observed_timing_counters(stats, self.cell())

    def test_packet_retry_counters_are_exactly_predeclared(self):
        cell = self.cell()
        stats = self.complete_fake_stats()
        contract = self.timing_contract(stats)
        for name in DRIVER.PACKET_RETRY_COUNTERS:
            self.assertIn(name, contract["counters"])
            with self.subTest(missing=name):
                incomplete = {
                    **contract,
                    "counters": dict(contract["counters"]),
                }
                del incomplete["counters"][name]
                with self.assertRaisesRegex(RuntimeError, "counter set"):
                    DRIVER.validate_confirmation(
                        stats, incomplete, "a" * 64, cell
                    )

        for name in DRIVER.PACKET_RETRY_COUNTERS:
            with self.subTest(name=name):
                changed = dict(stats)
                for retry_name in DRIVER.PACKET_RETRY_COUNTERS:
                    changed[retry_name] = 2
                DRIVER.validate_packet_retry_counters(changed)
                changed[name] = 3
                with self.assertRaisesRegex(RuntimeError, "retry"):
                    DRIVER.validate_confirmation(
                        changed, contract, "a" * 64, cell
                    )

    def test_semantically_valid_retry_ledgers_are_closed_form(self):
        stats = self.complete_fake_stats()
        stats.update(
            {
                "portSendFailures": 3,
                "portRetryNotifications": 3,
                "retryPacketResubmissions": 3,
                "retryPacketAcceptances": 1,
            }
        )
        self.assertEqual(
            DRIVER.validate_packet_retry_counters(stats),
            {name: stats[name] for name in DRIVER.PACKET_RETRY_COUNTERS},
        )
        for name in DRIVER.PACKET_RETRY_COUNTERS:
            stats[name] = 0
        DRIVER.validate_packet_retry_counters(stats)

    def test_adversarial_retry_contract_cannot_bless_open_obligations(self):
        cell = self.cell()
        stats = self.complete_fake_stats()
        stats.update(
            {
                "portSendFailures": 999,
                "portRetryNotifications": 999,
                "retryPacketResubmissions": 998,
                "retryPacketAcceptances": 0,
            }
        )
        contract = self.timing_contract(stats)
        with self.assertRaisesRegex(RuntimeError, "retry obligation"):
            DRIVER.validate_confirmation(stats, contract, "a" * 64, cell)

    def test_balanced_retry_mutation_still_fails_exact_confirmation(self):
        cell = self.cell()
        stats = self.complete_fake_stats()
        contract = self.timing_contract(stats)
        for name in DRIVER.PACKET_RETRY_COUNTERS:
            stats[name] = 2
        DRIVER.validate_packet_retry_counters(stats)
        with self.assertRaisesRegex(
            RuntimeError, "exact timing stat mismatch"
        ):
            DRIVER.validate_confirmation(stats, contract, "a" * 64, cell)

    def test_terminal_retry_run_requires_an_acceptance(self):
        cell = self.cell()
        stats = self.complete_fake_stats()
        stats.update(
            {
                "portSendFailures": 2,
                "portRetryNotifications": 2,
                "retryPacketResubmissions": 2,
                "retryPacketAcceptances": 0,
            }
        )
        contract = self.timing_contract(stats)
        with self.assertRaisesRegex(RuntimeError, "terminal retry acceptance"):
            DRIVER.validate_confirmation(stats, contract, "a" * 64, cell)

    def test_timing_contract_must_bind_build_manifest(self):
        cell = self.cell()
        stats = self.complete_fake_stats()
        contract = self.timing_contract(stats, "b" * 64)
        with self.assertRaisesRegex(RuntimeError, "bind the build manifest"):
            DRIVER.validate_confirmation(stats, contract, "a" * 64, cell)
        wrong_cell = self.cell(24, 2)
        with self.assertRaisesRegex(RuntimeError, "cell mismatches"):
            DRIVER.validate_timing_contract(contract, "b" * 64, wrong_cell)

    def test_mixed_gate_enforces_w1_zero_and_w2_positive(self):
        w1 = self.cell(32, 1)
        w1_stats = self.complete_fake_stats(32, 1)
        w1_contract = self.timing_contract(w1_stats, tokens=32, width=1)
        DRIVER.validate_confirmation(w1_stats, w1_contract, "a" * 64, w1)
        w1_stats["descriptorUmtStateDualIssueCycles"] = 1
        with self.assertRaisesRegex(RuntimeError, "W1 cell"):
            DRIVER.validate_confirmation(w1_stats, w1_contract, "a" * 64, w1)

        w2 = self.cell(32, 2)
        w2_stats = self.complete_fake_stats(32, 2)
        w2_contract = self.timing_contract(w2_stats)
        DRIVER.validate_confirmation(w2_stats, w2_contract, "a" * 64, w2)
        w2_stats["descriptorUmtStateDualIssueCycles"] = 0
        with self.assertRaisesRegex(RuntimeError, "retained|did not exercise"):
            DRIVER.validate_confirmation(w2_stats, w2_contract, "a" * 64, w2)

    def test_all_cells_derive_mixed_token_issue_and_cost_expectations(self):
        for tokens, width in FACTORIAL.CELL_VARIANTS:
            with self.subTest(tokens=tokens, width=width):
                cell = self.cell(tokens, width)
                expected = DRIVER.exact_stats(cell)
                self.assertEqual(
                    expected["descriptorUmtStateTokenHighWaterMark"], tokens
                )
                self.assertEqual(
                    expected["descriptorUmtStateFpIssueWidth"], width
                )
                self.assertEqual(
                    expected[
                        "descriptorUmtStateFpIssueSelectionCandidateInputs"
                    ],
                    tokens * width,
                )
                self.assertEqual(
                    expected[
                        "descriptorUmtStatePhysicalStorePlusLogical"
                        "AuxiliaryBitsFloor"
                    ],
                    54372 if tokens == 24 else 58142,
                )

    def test_calibration_emits_candidate_but_not_confirmation(self):
        cell = self.cell()
        stats = self.complete_fake_stats()
        exact, candidate = DRIVER.timing_contract_candidate(
            stats, "a" * 64, cell
        )
        self.assertEqual(exact, DRIVER.exact_stats(cell))
        self.assertEqual(
            DRIVER.TIMING_CONTRACT_SCHEMA,
            "lanl-maa-umt-ordered-wave-timing-contract-v3",
        )
        self.assertEqual(
            DRIVER.EVIDENCE_REPORT_SCHEMA,
            "lanl-maa-umt-ordered-wave-mixed-evidence-v3",
        )
        self.assertEqual(candidate["schema"], DRIVER.TIMING_CONTRACT_SCHEMA)
        self.assertEqual(candidate["build_manifest_sha256"], "a" * 64)
        self.assertEqual(candidate["cell"], cell.document())
        self.assertNotIn("status", candidate)
        self.assertEqual(
            DRIVER.validation_disposition("calibration"),
            {
                "status": "calibration_only",
                "gate_scope": "mixed_umt_evidence_prerequisite",
                "prerequisite_gate_passed": False,
                "application_performance_promotion_eligible": False,
            },
        )
        confirmation = DRIVER.validation_disposition("confirmation")
        self.assertEqual(confirmation["status"], "prerequisite_passed")
        self.assertTrue(confirmation["prerequisite_gate_passed"])
        self.assertFalse(
            confirmation["application_performance_promotion_eligible"]
        )
        self.assertNotIn("promotion_eligible", confirmation)

    def test_build_manifest_binds_reproducible_relink_contract(self):
        manifest = self.build_manifest()
        self.assertEqual(
            DRIVER.validate_build_manifest_document(manifest), self.cell()
        )
        changed = dict(manifest)
        changed["required_relink_observed"] = False
        with self.assertRaisesRegex(
            RuntimeError, "required_relink_observed is not true"
        ):
            DRIVER.validate_build_manifest_document(changed)

    def test_validator_rejects_error_as_completion(self):
        cell = self.cell()
        stats = self.complete_fake_stats()
        stats["descriptorCompletionWrites"] = 5
        with self.assertRaisesRegex(RuntimeError, "exact stat mismatch"):
            DRIVER.validate_exact_stats(stats, cell)

    def test_calibration_requires_retained_pipeline_and_stall_work(self):
        cell = self.cell()
        for name in (
            "descriptorUmtBatchCycles",
            "descriptorUmtStateTokenBackpressureEvents",
            "descriptorUmtStateFpIssueStallCycles",
            "descriptorUmtStateInputBankWaitCycles",
            "descriptorUmtStatePipelineResultBankStallCycles",
            "descriptorUmtStateResultDrainBankWaitCycles",
            "descriptorUmtInputLineWaiterHoldLineCycles",
        ):
            with self.subTest(name=name):
                stats = self.complete_fake_stats()
                stats[name] = 0
                with self.assertRaisesRegex(
                    RuntimeError, "retained|dual-issue|accounting"
                ):
                    DRIVER.timing_contract_candidate(stats, "a" * 64, cell)


if __name__ == "__main__":
    unittest.main()
