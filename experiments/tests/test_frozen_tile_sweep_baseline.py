#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/check_frozen_tile_sweep_baseline.py"
SPEC = importlib.util.spec_from_file_location("frozen_tile_baseline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FrozenTileSweepBaselineTest(unittest.TestCase):
    def test_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample"
            path.write_bytes(b"dx100\n")
            self.assertEqual(
                MODULE.sha256(path),
                "3e0a639ad5db264c7cb4717e118827b804d2f7288c92f666e0266e31458f767b",
            )

    def test_manifest_is_complete_and_hybrid_only(self):
        manifest = json.loads(
            (
                ROOT
                / "experiments/analysis/physical_tile_sweep_baseline_20260822.json"
            ).read_text()
        )
        self.assertEqual(manifest["valid_points"], 77)
        self.assertEqual(manifest["workloads"], 11)
        self.assertEqual(manifest["native_reference_tile"], 16384)
        self.assertEqual(
            manifest["policy"]["development"],
            "reuse these native endpoints and run hybrid arms only",
        )


if __name__ == "__main__":
    unittest.main()
