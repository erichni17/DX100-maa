#!/usr/bin/env python3
"""Unit checks for the live opcode-7 native-batch generator."""

import importlib.util
import json
import pathlib
import re
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
RUNNER_PATH = HERE / "run_sparta_fused_cell_cpu_smoke.py"
BATCH = pathlib.Path(
    "/data1/nier/dx100-runs/2026-07-28-lanl-maa-sparta-native-record-v2/"
    "raw-series/thermal_grid_step_000000000056.json"
)
SPEC = importlib.util.spec_from_file_location("sparta_fused_live", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class SpartaFusedCellCpuSmokeTest(unittest.TestCase):
    def test_native_header_binds_the_representative_geometry(self):
        document = json.loads(BATCH.read_text(encoding="utf-8"))
        header, writes = RUNNER.build_header(document)
        self.assertEqual(writes, 156)
        self.assertIn("SPARTA_FUSED_CELLS UINT64_C(27)", header)
        self.assertIn("SPARTA_FUSED_PARTICLES UINT64_C(64)", header)
        self.assertIn("SPARTA_FUSED_EXPECTED_WRITES UINT64_C(156)", header)
        self.assertEqual(
            len(re.findall(r"UINT64_C\(0x[0-9a-f]{16}\)", header)),
            27 * 6 + 64 * 3 + 1,
        )

    def test_stats_audit_rejects_missing_transactional_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "stats.txt"
            path.write_text("system.lanl_maa.descriptorFetches 4\n")
            with self.assertRaisesRegex(ValueError, "descriptorErrors"):
                RUNNER.read_stats(path)


if __name__ == "__main__":
    unittest.main()
