#!/usr/bin/env python3
"""Unit checks for the live opcode-8 UME gradzatp evidence audit."""

import importlib.util
import json
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
RUNNER_PATH = HERE / "run_ume_gradzatp_cpu_smoke.py"
SPEC = importlib.util.spec_from_file_location("ume_gradzatp_live", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
METADATA = json.loads(
    (HERE / "ume_gradzatp_cpu_smoke.json").read_text(encoding="utf-8")
)


class UmeGradzatpCpuSmokeTest(unittest.TestCase):
    def write_stats(self, path, overrides=None):
        values = {
            "descriptorDoorbells": 3,
            "descriptorRearms": 2,
            "descriptorCompletionWrites": 2,
            "descriptorErrors": 1,
            "descriptorUmeUpdatesAcknowledged": 20,
            "updateOperationsAcknowledged": 20,
            "atomicFp32AddUpdates": 14,
            "physicalAtomicUpdates": 14,
            "atomicAcknowledgements": 14,
            "atomicOldValuesReturned": 14,
            "descriptorUmeCornersClassified": 16,
            "descriptorUmeActiveCorners": 10,
            "descriptorUmeInactiveCorners": 6,
            "descriptorUmeCornersValidated": 10,
            "descriptorUmeZoneFieldGathers": 10,
            "descriptorUmeOutputZeroReads": 20,
            "descriptorUmeFp32Multiplies": 10,
            "portSendFailures": 3,
            "portRetryNotifications": 3,
            "retryPacketResubmissions": 3,
            "retryPacketAcceptances": 3,
            "updateCombinerHits": 6,
            "updateDrains": 14,
            "physicalLineReads": 20,
            "lineMergeHits": 5,
            "descriptorCycles": 100,
            "engineCycles": 80,
            "logicalItems": 16,
            "completionsRetired": 16,
            "payloadOverlayCompletionWrites": 0,
            "payloadOverlayRetirementReads": 0,
            "payloadOverlayCompletionBankConflictCycles": 0,
            "payloadOverlayCompletionReadConflictCycles": 0,
            "payloadOverlayCompletionWouldBlockCycles": 0,
            "payloadOverlayCompletionQueueHighWaterMark": 0,
            "payloadOverlayResetAllocatedEntries": 0,
            "payloadOverlayResetQueuedCompletions": 0,
            "payloadOverlayResetCompletedEntries": 0,
        }
        values.update(overrides or {})
        path.write_text(
            "".join(
                f"system.lanl_maa.{name} {value}\n"
                for name, value in values.items()
            )
            + "system.cpu.commitStats0.numInsts 100\n"
            + "simTicks 200\n",
            encoding="utf-8",
        )

    def test_stats_close_logical_physical_and_retry_accounting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stats.txt"
            self.write_stats(path)
            metrics = RUNNER.validate_stats(path, METADATA)
            self.assertEqual(metrics["descriptorUmeUpdatesAcknowledged"], 20)
            self.assertEqual(metrics["physicalAtomicUpdates"], 14)

    def test_stats_reject_missing_output_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stats.txt"
            self.write_stats(path, {"descriptorUmeOutputZeroReads": 19})
            with self.assertRaisesRegex(
                RuntimeError, "descriptorUmeOutputZeroReads"
            ):
                RUNNER.validate_stats(path, METADATA)

    def test_stats_reject_retry_ownership_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stats.txt"
            self.write_stats(path, {"retryPacketAcceptances": 2})
            with self.assertRaisesRegex(RuntimeError, "retry accounting"):
                RUNNER.validate_stats(path, METADATA)

    def test_stats_close_modeled_payload_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stats.txt"
            self.write_stats(
                path,
                {
                    "payloadOverlayCompletionWrites": 16,
                    "payloadOverlayRetirementReads": 16,
                    "payloadOverlayCompletionBankConflictCycles": 1,
                    "payloadOverlayCompletionReadConflictCycles": 2,
                    "payloadOverlayCompletionWouldBlockCycles": 3,
                    "payloadOverlayCompletionQueueHighWaterMark": 2,
                },
            )
            metrics = RUNNER.validate_stats(path, METADATA, True)
            self.assertEqual(metrics["payloadOverlayRetirementReads"], 16)

    def test_stats_reject_payload_overlay_conservation_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stats.txt"
            self.write_stats(
                path,
                {
                    "payloadOverlayCompletionWrites": 16,
                    "payloadOverlayRetirementReads": 15,
                },
            )
            with self.assertRaisesRegex(
                RuntimeError, "retirement conservation"
            ):
                RUNNER.validate_stats(path, METADATA, True)


if __name__ == "__main__":
    unittest.main()
