import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/scripts/analyze_virtual_grow_trace.py"
SPEC = importlib.util.spec_from_file_location("grow_trace", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GrowTraceTest(unittest.TestCase):
    def test_parses_unique_lines_and_rejects_inconsistent_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.log"
            trace.write_text(
                "x fillRowTable: inserting vaddr(0x0), paddr(0x100), "
                "MAP(x), grow(0x3), itr(0), idx(0), wid(0) to T[2]\n"
                "x fillRowTable: inserting vaddr(0x0), paddr(0x108), "
                "MAP(x), grow(0x3), itr(1), idx(1), wid(1) to T[2]\n"
            )
            groups = MODULE.parse_trace(trace)
            self.assertEqual(groups, {(2, 3): {4}})
            trace.write_text(
                trace.read_text()
                + "x fillRowTable: inserting vaddr(0x0), paddr(0x100), "
                "MAP(x), grow(0x4), itr(2), idx(2), wid(2) to T[2]\n"
            )
            with self.assertRaisesRegex(ValueError, "maps to both"):
                MODULE.parse_trace(trace)

    def test_greedy_oracle_exposes_capacity_feasibility(self):
        groups = {
            (0, 0): set(range(0, 17)),
            (0, 1): set(range(20, 29)),
            (0, 2): set(range(40, 49)),
        }
        rows = MODULE.analyze(groups, entries=8, rows_per_slice=4, partitions=[2])
        by_policy = {row["policy"]: row for row in rows}
        self.assertEqual(by_policy["grow_modulo"]["fits_rows_per_slice"], "false")
        self.assertEqual(
            by_policy["greedy_per_slice_oracle"]["fits_rows_per_slice"], "true"
        )

    def test_requires_exact_success_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "restore.exit").write_text("0\n")
            (root / "restore.log").write_text(
                "VIRTUAL_TILE_CONSUMER_RESULT mode=paged_overlap "
                "page_elements=4096 hash=7 errors=0\n"
            )
            MODULE.validate_run(root, "7")
            with self.assertRaisesRegex(ValueError, "lacks one exact"):
                MODULE.validate_run(root, "8")


if __name__ == "__main__":
    unittest.main()
