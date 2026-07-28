import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/scripts/analyze_virtual_index_partitions.py"
SPEC = importlib.util.spec_from_file_location("index_partitions", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VirtualIndexPartitionTest(unittest.TestCase):
    def test_range_partitions_preserve_global_coalescing(self):
        rows = MODULE.analyze(
            [0, 1, 8, 9, 16, 17, 24, 25],
            source_elements=32,
            partitions=[1, 2, 4],
            word_bytes=8,
            index_bytes=4,
            line_bytes=64,
            word_capacity=2,
            row_descriptor_capacity=1,
            policies=["range"],
        )
        by_partitions = {row["partitions"]: row for row in rows}
        self.assertEqual(by_partitions["4"]["source_line_requests_oracle"], "4")
        self.assertEqual(by_partitions["4"]["max_words_per_partition"], "2")
        self.assertEqual(by_partitions["4"]["fits_word_capacity"], "true")
        self.assertEqual(
            by_partitions["4"]["fits_row_descriptor_capacity"], "true"
        )

    def test_affine_xrage_microbenchmark_partition_tradeoff(self):
        indices = MODULE.affine_indices(16384, 97, 13, 131072)
        rows = MODULE.analyze(
            indices,
            source_elements=131072,
            partitions=[2, 4],
            word_bytes=8,
            index_bytes=4,
            line_bytes=64,
            word_capacity=4096,
            row_descriptor_capacity=4096,
            source_base_offset=16,
            policies=["range", "modulo"],
        )
        by_point = {(row["policy"], row["partitions"]): row for row in rows}
        self.assertEqual(by_point[("range", "2")]["fits_word_capacity"], "false")
        self.assertEqual(by_point[("range", "4")]["fits_word_capacity"], "false")
        self.assertEqual(by_point[("modulo", "4")]["fits_word_capacity"], "true")
        self.assertEqual(by_point[("modulo", "4")]["unique_source_lines"], "9523")
        self.assertEqual(
            by_point[("modulo", "4")]["source_line_requests_oracle"], "9523"
        )
        self.assertEqual(by_point[("modulo", "4")]["extra_index_bytes"], "196608")

    def test_rejects_out_of_range_index(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            MODULE.analyze(
                [0, 8],
                source_elements=8,
                partitions=[1],
                word_bytes=8,
                index_bytes=4,
                line_bytes=64,
                word_capacity=8,
                row_descriptor_capacity=8,
                policies=["range"],
            )

    def test_index_alignment_changes_line_lower_bound(self):
        self.assertEqual(MODULE.lines_touched(64, 64, 0), 1)
        self.assertEqual(MODULE.lines_touched(64, 64, 4), 2)


if __name__ == "__main__":
    unittest.main()
