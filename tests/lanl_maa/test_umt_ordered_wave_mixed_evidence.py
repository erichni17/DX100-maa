#!/usr/bin/env python3
"""Standalone oracle/accounting tests for the mixed UMT evidence gate."""

import importlib.util
import pathlib
import unittest

DRIVER_PATH = pathlib.Path(__file__).with_name(
    "run_umt_ordered_wave_mixed_evidence_smoke.py"
)
SPEC = importlib.util.spec_from_file_location(
    "umt_mixed_evidence_driver", DRIVER_PATH
)
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class MixedUmtEvidenceTest(unittest.TestCase):
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
        expected = DRIVER.exact_stats()
        self.assertEqual(expected["descriptorDoorbells"], 5)
        self.assertEqual(expected["descriptorRearms"], 4)
        self.assertEqual(expected["descriptorCompletionWrites"], 4)
        self.assertEqual(expected["descriptorErrors"], 1)
        self.assertEqual(expected["descriptorUmtInputReads"], 2280)
        self.assertEqual(expected["descriptorUmtInputLineReads"], 313)
        self.assertEqual(expected["descriptorUmtStateInputWrites"], 1168)
        self.assertEqual(
            expected["descriptorUmtStateDenominatorsConsumed"], 1111
        )
        self.assertEqual(expected["descriptorUmtResultLineWrites"], 152)
        self.assertEqual(expected["descriptorUmtFp64AddSubOperations"], 2760)
        self.assertEqual(expected["descriptorUmtFp64MultiplyOperations"], 1656)
        self.assertEqual(expected["descriptorUmtFp64DivideOperations"], 1104)
        self.assertEqual(expected["descriptorUmtStateStoreHighWaterMark"], 64)
        self.assertEqual(expected["descriptorUmtStateBankHighWaterMark"], 16)
        self.assertEqual(
            expected["descriptorUmtStatePhysicalStoreBytes"], 5120
        )
        self.assertEqual(
            expected[
                "descriptorUmtStatePhysicalStorePlusLogicalAuxiliaryBitsFloor"
            ],
            58078,
        )

    @staticmethod
    def complete_fake_stats():
        stats = dict(DRIVER.exact_stats())
        for name in DRIVER.TIMING_COUNTER_REASONS:
            stats[name] = 1
        stats["lineTableHighWaterMark"] = 32
        stats["controlStatusReads"] = 17
        stats["controlReadRequests"] = 18
        return stats

    @staticmethod
    def timing_contract(stats, build_sha="a" * 64):
        return {
            "schema": DRIVER.TIMING_CONTRACT_SCHEMA,
            "build_manifest_sha256": build_sha,
            "counters": {
                name: stats[name] for name in DRIVER.TIMING_COUNTER_REASONS
            },
        }

    @staticmethod
    def build_manifest():
        return {
            "schema": DRIVER.BUILD_MANIFEST_SCHEMA,
            "status": "passed",
            "source_commit": "1" * 40,
            "source_tree": "2" * 40,
            "source_clean_before_and_after": True,
            "source_identity_unchanged": True,
            "command": [
                str(pathlib.Path("/usr/bin/scons").resolve()),
                "--ignore-style",
                "build/X86/gem5.opt",
                "-j4",
            ],
            "returncode": 0,
            "started_at": "2026-08-09T00:00:00+00:00",
            "ended_at": "2026-08-09T00:01:00+00:00",
            "required_relink_observed": True,
            "target": "/source/build/X86/gem5.opt",
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
        stats = self.complete_fake_stats()
        contract = self.timing_contract(stats)
        exact, timing = DRIVER.validate_confirmation(stats, contract, "a" * 64)
        self.assertEqual(exact, DRIVER.exact_stats())
        self.assertEqual(set(timing), set(DRIVER.TIMING_COUNTER_REASONS))
        self.assertTrue(
            all(
                item["expected"] == item["observed"]
                for item in timing.values()
            )
        )

    def test_confirmation_rejects_positive_but_changed_timing(self):
        stats = self.complete_fake_stats()
        contract = self.timing_contract(stats)
        stats["descriptorUmtStateFpIssueStallCycles"] = 2
        with self.assertRaisesRegex(
            RuntimeError, "exact timing stat mismatch"
        ):
            DRIVER.validate_confirmation(stats, contract, "a" * 64)

    def test_packet_retry_counters_are_exactly_predeclared(self):
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
                    DRIVER.validate_confirmation(stats, incomplete, "a" * 64)

        for name in DRIVER.PACKET_RETRY_COUNTERS:
            with self.subTest(name=name):
                changed = dict(stats)
                for retry_name in DRIVER.PACKET_RETRY_COUNTERS:
                    changed[retry_name] = 2
                DRIVER.validate_packet_retry_counters(changed)
                changed[name] = 3
                with self.assertRaisesRegex(RuntimeError, "retry"):
                    DRIVER.validate_confirmation(changed, contract, "a" * 64)

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
            DRIVER.validate_confirmation(stats, contract, "a" * 64)

    def test_balanced_retry_mutation_still_fails_exact_confirmation(self):
        stats = self.complete_fake_stats()
        contract = self.timing_contract(stats)
        for name in DRIVER.PACKET_RETRY_COUNTERS:
            stats[name] = 2
        DRIVER.validate_packet_retry_counters(stats)
        with self.assertRaisesRegex(
            RuntimeError, "exact timing stat mismatch"
        ):
            DRIVER.validate_confirmation(stats, contract, "a" * 64)

    def test_terminal_retry_run_requires_an_acceptance(self):
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
            DRIVER.validate_confirmation(stats, contract, "a" * 64)

    def test_timing_contract_must_bind_build_manifest(self):
        stats = self.complete_fake_stats()
        contract = self.timing_contract(stats, "b" * 64)
        with self.assertRaisesRegex(RuntimeError, "bind the build manifest"):
            DRIVER.validate_confirmation(stats, contract, "a" * 64)

    def test_calibration_emits_candidate_but_not_confirmation(self):
        stats = self.complete_fake_stats()
        exact, candidate = DRIVER.timing_contract_candidate(stats, "a" * 64)
        self.assertEqual(exact, DRIVER.exact_stats())
        self.assertEqual(
            DRIVER.TIMING_CONTRACT_SCHEMA,
            "lanl-maa-umt-ordered-wave-timing-contract-v2",
        )
        self.assertEqual(
            DRIVER.EVIDENCE_REPORT_SCHEMA,
            "lanl-maa-umt-ordered-wave-mixed-evidence-v2",
        )
        self.assertEqual(candidate["schema"], DRIVER.TIMING_CONTRACT_SCHEMA)
        self.assertEqual(candidate["build_manifest_sha256"], "a" * 64)
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
        self.assertIs(
            DRIVER.validate_build_manifest_document(manifest), manifest
        )
        changed = dict(manifest)
        changed["required_relink_observed"] = False
        with self.assertRaisesRegex(
            RuntimeError, "required_relink_observed is not true"
        ):
            DRIVER.validate_build_manifest_document(changed)

    def test_validator_rejects_error_as_completion(self):
        stats = self.complete_fake_stats()
        stats["descriptorCompletionWrites"] = 5
        with self.assertRaisesRegex(RuntimeError, "exact stat mismatch"):
            DRIVER.validate_exact_stats(stats)

    def test_calibration_requires_retained_pipeline_and_stall_work(self):
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
                with self.assertRaisesRegex(RuntimeError, "retained"):
                    DRIVER.timing_contract_candidate(stats, "a" * 64)


if __name__ == "__main__":
    unittest.main()
