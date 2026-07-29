#!/usr/bin/env python3
"""Unit checks for the native Branson process evidence audit."""

import importlib.util
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
RUNNER_PATH = HERE / "run_branson_native_process_cpu_smoke.py"
SPEC = importlib.util.spec_from_file_location(
    "branson_native_process", RUNNER_PATH
)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)

METADATA = {
    "roots": 12000,
    "events": 117584,
    "cells": 6000,
    "descriptor_batches": 188,
    "maximum_events_per_root": 27,
    "logical_fp64_updates": 235168,
}


class BransonNativeProcessCpuSmokeTest(unittest.TestCase):
    def test_submission_requires_full_native_timestep(self):
        document = {
            "schema": "branson-lanl-maa-submission-v1",
            "roots": 12000,
            "events": 117584,
            "cells": 6000,
            "descriptor_batches": 188,
            "maximum_events_per_root": 27,
            "reference_fingerprint": "0123456789abcdef",
            "accelerator_fingerprint": "fedcba9876543210",
            "exact_absorbed_cells": 4000,
            "exact_track_cells": 5000,
            "maximum_absorbed_difference": 1.0e-15,
            "maximum_track_difference": 0.0,
            "tolerance": 1.0e-12,
            "tolerance_match": True,
            "scalar_tally_updates_replaced": True,
        }
        RUNNER.validate_submission(document, METADATA)
        document["events"] = 8199
        with self.assertRaisesRegex(ValueError, "events"):
            RUNNER.validate_submission(document, METADATA)

    def test_stats_close_logical_physical_and_retry_accounting(self):
        updates = METADATA["logical_fp64_updates"]
        values = {
            "descriptorDoorbells": 188,
            "descriptorBusyRejections": 0,
            "descriptorRearms": 187,
            "descriptorFetches": 188,
            "descriptorCompletionWrites": 188,
            "descriptorErrors": 0,
            "descriptorBransonRootsLoaded": 12000,
            "descriptorBransonEventsValidated": 117584,
            "descriptorBransonEventsReplayed": 117584,
            "descriptorBransonUpdatesAcknowledged": updates,
            "descriptorBransonEventComputesQueued": updates,
            "descriptorBransonEventComputesIssued": updates,
            "descriptorBransonEventComputesCompleted": updates,
            "descriptorBransonEventComputesCancelled": 0,
            "descriptorBransonEventComputesCancelledInFlight": 0,
            "continuationSteps": updates,
            "continuationExhaustions": 0,
            "activeContextHighWaterMark": 16,
            "physicalLineReads": updates - 10,
            "lineMergeHits": 10,
            "updateCombinerHits": 100,
            "updateDrains": updates - 100,
            "physicalAtomicUpdates": updates - 100,
            "atomicAcknowledgements": updates - 100,
            "portSendFailures": 7,
            "portRetryNotifications": 7,
            "retryPacketResubmissions": 7,
            "retryPacketAcceptances": 7,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stats.txt"
            path.write_text(
                "".join(
                    f"system.lanl_maa.{name} {value}\n"
                    for name, value in values.items()
                )
                + "system.cpu.commitStats0.numInsts 100\n"
                + "simTicks 200\n",
                encoding="utf-8",
            )
            RUNNER.read_stats(path, METADATA)
            values["retryPacketAcceptances"] = 6
            path.write_text(
                "".join(
                    f"system.lanl_maa.{name} {value}\n"
                    for name, value in values.items()
                )
                + "system.cpu.commitStats0.numInsts 100\n"
                + "simTicks 200\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "retry"):
                RUNNER.read_stats(path, METADATA)

    def test_application_output_requires_conservation(self):
        text = (
            "Total Photons transported: 12000\n"
            "Radiation conservation: -1.0e-10\n"
            "Material conservation: 2.0e-11\n"
        )
        self.assertEqual(
            RUNNER.validate_application_output(text), [-1e-10, 2e-11]
        )


if __name__ == "__main__":
    unittest.main()
