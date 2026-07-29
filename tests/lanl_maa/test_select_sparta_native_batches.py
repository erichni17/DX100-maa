#!/usr/bin/env python3
"""Tests for deterministic native SPARTA series selection."""

import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SELECTOR_PATH = HERE / "select_sparta_native_batches.py"
SPEC = importlib.util.spec_from_file_location(
    "sparta_series_selector", SELECTOR_PATH
)
SELECTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SELECTOR)


def summary(timestep, atomics, maximum, sum_squares):
    return {
        "timestep": timestep,
        "path": f"/batch/{timestep}.json",
        "artifact_sha256": f"{timestep:064x}",
        "predicted_cell_group_physical_atomics": atomics,
        "maximum_occupancy": maximum,
        "occupancy_sum_squares": sum_squares,
    }


class SpartaSeriesSelectorTest(unittest.TestCase):
    def test_counts_four_item_cell_segments(self):
        batch = {
            "timestep": 4,
            "cell_count": 4,
            "indices": [0, 0, 1, 1, 1, 2, 2, 3],
            "contribution_bits": ["0000000000000000"] * 48,
        }
        original = SELECTOR.RUNNER.file_sha256
        SELECTOR.RUNNER.file_sha256 = lambda path: "a" * 64
        try:
            result = SELECTOR.summarize_batch(
                pathlib.Path("batch.json"), batch
            )
        finally:
            SELECTOR.RUNNER.file_sha256 = original
        self.assertEqual(result["four_item_cell_segments"], 5)
        self.assertEqual(result["predicted_cell_group_physical_atomics"], 30)
        self.assertEqual(result["predicted_cell_group_combiner_hits"], 354)
        self.assertEqual(result["occupancies"], [2, 3, 2, 1])

    def test_selection_covers_extremes_without_result_peeking(self):
        summaries = [
            summary(0, 210, 3, 160),
            summary(4, 180, 4, 180),
            summary(8, 240, 3, 170),
            summary(12, 198, 8, 220),
            summary(16, 204, 4, 190),
        ]
        selected = SELECTOR.select_representatives(summaries, 5)
        reasons = {reason for item in selected for reason in item["reasons"]}
        self.assertEqual(
            [item["timestep"] for item in selected], [0, 4, 8, 12, 16]
        )
        self.assertIn("first-timestep", reasons)
        self.assertIn("last-timestep", reasons)
        self.assertIn("minimum-predicted-group-atomics", reasons)
        self.assertIn("maximum-predicted-group-atomics", reasons)
        self.assertIn("maximum-native-occupancy-skew", reasons)

    def test_temporal_fill_is_deterministic(self):
        summaries = [summary(step, 192, 3, 160) for step in range(0, 36, 4)]
        selected = SELECTOR.select_representatives(summaries, 5)
        self.assertEqual(
            [item["timestep"] for item in selected], [0, 8, 16, 24, 32]
        )


if __name__ == "__main__":
    unittest.main()
