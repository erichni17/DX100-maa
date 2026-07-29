#!/usr/bin/env python3
"""Unit checks for the occupancy-parameterized opcode-7 smoke audit."""

import importlib.util
import json
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
RUNNER_PATH = HERE / "run_sparta_fused_cell_cpu_batch_smoke.py"
SERIES = pathlib.Path(
    "/data1/nier/dx100-runs/2026-07-28-lanl-maa-sparta-native-record-v2/"
    "raw-series"
)
SPEC = importlib.util.spec_from_file_location(
    "sparta_fused_batch", RUNNER_PATH
)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def load_step(step):
    path = SERIES / f"thermal_grid_step_{step:012d}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class SpartaFusedCellCpuBatchSmokeTest(unittest.TestCase):
    def test_expected_writes_cover_series_extremes(self):
        high = RUNNER.expected_metrics(load_step(0))
        low = RUNNER.expected_metrics(load_step(44))
        self.assertEqual(high["descriptorResultWrites"], 162)
        self.assertEqual(low["descriptorResultWrites"], 144)
        for metrics in (high, low):
            self.assertEqual(
                metrics["descriptorSpartaFusedParticlesVisited"], 128
            )
            self.assertEqual(metrics["descriptorSpartaFusedFp64Adds"], 1024)

    def test_stats_audit_requires_retry_conservation(self):
        batch = load_step(0)
        metrics = RUNNER.expected_metrics(batch)
        metrics["descriptorSpartaFusedTallyZeroReads"] = 162
        metrics["activeContextHighWaterMark"] = 8
        metrics["descriptorSpartaFusedPairBankConflictCycles"] = 1
        metrics.update(
            {
                "portSendFailures": 3,
                "portRetryNotifications": 3,
                "retryPacketResubmissions": 3,
                "retryPacketAcceptances": 2,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stats.txt"
            path.write_text(
                "".join(
                    f"system.lanl_maa.{name} {value}\n"
                    for name, value in metrics.items()
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unbalanced retry"):
                RUNNER.read_stats(path, batch)


if __name__ == "__main__":
    unittest.main()
