#!/usr/bin/env python3
"""Unit checks for the native SPARTA process evidence audit."""

import importlib.util
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
RUNNER_PATH = HERE / "run_sparta_native_process_cpu_smoke.py"
SPEC = importlib.util.spec_from_file_location(
    "sparta_native_process", RUNNER_PATH
)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class SpartaNativeProcessCpuSmokeTest(unittest.TestCase):
    def test_submission_requires_exact_index_stable_result(self):
        document = {
            "schema": "sparta-lanl-maa-submission-v1",
            "timestep": 1,
            "rank": 0,
            "cell_count": 27,
            "particle_count": 64,
            "species_count": 1,
            "expected_writes": 156,
            "completion_writes": 156,
            "exact_words_checked": 162,
            "exact_words_matching": 162,
            "scalar_fingerprint": "0123456789abcdef",
            "accelerator_fingerprint": "0123456789abcdef",
            "exact_match": True,
        }
        self.assertEqual(
            RUNNER.validate_submission(document),
            (156, "0123456789abcdef"),
        )
        document["exact_words_matching"] = 161
        with self.assertRaisesRegex(ValueError, "exact_words_matching"):
            RUNNER.validate_submission(document)

    def test_submission_timestep_is_explicit(self):
        document = {
            "schema": "sparta-lanl-maa-submission-v1",
            "timestep": 56,
            "rank": 0,
            "cell_count": 27,
            "particle_count": 64,
            "species_count": 1,
            "expected_writes": 156,
            "completion_writes": 156,
            "exact_words_checked": 162,
            "exact_words_matching": 162,
            "scalar_fingerprint": "0123456789abcdef",
            "accelerator_fingerprint": "0123456789abcdef",
            "exact_match": True,
        }
        self.assertEqual(
            RUNNER.validate_submission(document, 56),
            (156, "0123456789abcdef"),
        )
        with self.assertRaisesRegex(ValueError, "timestep"):
            RUNNER.validate_submission(document, 48)

    def test_stats_require_acknowledgement_and_retry_conservation(self):
        required = {
            "descriptorFetches": 2,
            "descriptorErrors": 0,
            "descriptorCompletionWrites": 1,
            "descriptorSpartaFusedCellsLoaded": 27,
            "descriptorSpartaFusedParticlesVisited": 64,
            "descriptorSpartaFusedEligibleParticles": 64,
            "descriptorSpartaFusedFp64Multiplies": 448,
            "descriptorSpartaFusedFp64Adds": 512,
            "descriptorSpartaFusedTallyZeroReads": 162,
            "descriptorSpartaFusedWritesAcknowledged": 156,
            "descriptorResultWrites": 156,
            "activeContextHighWaterMark": 8,
            "portSendFailures": 4,
            "portRetryNotifications": 4,
            "retryPacketResubmissions": 4,
            "retryPacketAcceptances": 3,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stats.txt"
            path.write_text(
                "".join(
                    f"system.lanl_maa.{name} {value}\n"
                    for name, value in required.items()
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unbalanced retry"):
                RUNNER.read_stats(path, 156)


if __name__ == "__main__":
    unittest.main()
