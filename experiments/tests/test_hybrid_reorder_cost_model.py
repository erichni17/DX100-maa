import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/analysis/hybrid_reorder_cost_model.py"
SPEC = importlib.util.spec_from_file_location(
    "hybrid_reorder_cost", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class HybridReorderCostModelTest(unittest.TestCase):
    def test_4k_active_window_requires_four_selected_scans(self):
        result = MODULE.analyze(MODULE.HybridReorderInput())
        self.assertEqual(result["minimum_selected_passes"], 4)
        self.assertEqual(result["total_b_scans"], 4)
        self.assertEqual(result["total_index_scan_bytes"], 262144)
        self.assertEqual(result["extra_index_scan_bytes_vs_one_pass"], 196608)
        self.assertEqual(result["total_index_cache_line_reads"], 4096)
        self.assertEqual(result["selection_label_bytes"], 4096)
        self.assertEqual(result["completion_bitmap_bytes"], 2048)
        self.assertEqual(result["filter_words"], 65536)

    def test_balanced_selection_needs_another_full_scan(self):
        result = MODULE.analyze(
            MODULE.HybridReorderInput(selection_prepass=True)
        )
        self.assertEqual(result["total_b_scans"], 5)
        self.assertEqual(result["extra_index_scan_bytes_vs_one_pass"], 262144)
        self.assertEqual(result["filter_words"], 81920)

    def test_materialized_spill_is_charged_in_both_directions(self):
        result = MODULE.analyze(
            MODULE.HybridReorderInput(spilled_descriptor_bytes=16)
        )
        self.assertEqual(result["spill_records_if_materialized"], 12288)
        self.assertEqual(result["spill_bytes_one_direction"], 196608)
        self.assertEqual(result["spill_read_write_bytes"], 393216)

    def test_finite_filter_has_a_cycle_lower_bound(self):
        result = MODULE.analyze(
            MODULE.HybridReorderInput(filter_words_per_cycle=16)
        )
        self.assertEqual(result["filter_cycles_if_serialized"], 4096)

    def test_rejects_zero_capacity(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            MODULE.analyze(
                MODULE.HybridReorderInput(active_descriptor_entries=0)
            )


if __name__ == "__main__":
    unittest.main()
