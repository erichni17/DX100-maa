import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "experiments" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "summarize_flag_offset_capacity",
    SCRIPT_DIR / "summarize_flag_offset_capacity.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FlagOffsetCapacityTest(unittest.TestCase):
    def write_config(
        self, path: Path, offset: str | None, epoch: str | None
    ) -> None:
        lines = [
            "[system.maa]",
            "num_tile_elements=16384",
            "num_row_table_rows_per_slice=16",
        ]
        if offset is not None:
            lines.append(f"num_offset_table_entries={offset}")
        if epoch is not None:
            lines.append(f"num_offset_table_epoch_entries={epoch}")
        path.write_text("\n".join(lines) + "\n", encoding="ascii")

    def test_zero_and_missing_capacity_resolve_before_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = root / "missing.ini"
            zeros = root / "zeros.ini"
            bounded = root / "bounded.ini"
            self.write_config(missing, None, None)
            self.write_config(zeros, "0", "0")
            self.write_config(bounded, "4096", "2048")

            self.assertEqual(
                MODULE.normalized_config(missing),
                MODULE.normalized_config(zeros),
            )
            values = MODULE.normalized_config(bounded)
            self.assertEqual(
                values[("system.maa", "num_offset_table_entries")], "4096"
            )
            self.assertEqual(
                values[("system.maa", "num_offset_table_epoch_entries")],
                "2048",
            )

    def test_source_snapshot_allows_only_named_analysis_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            allowed = "experiments/scripts/report_maa_storage.py"
            (case / "source_status.txt").write_text(
                f" M {allowed}\n", encoding="ascii"
            )
            (case / "source.diff").write_text(
                f"diff --git a/{allowed} b/{allowed}\n", encoding="ascii"
            )
            self.assertEqual(MODULE.validate_source_snapshot(case), [allowed])

            disallowed = "src/mem/MAA/MAA.cc"
            (case / "source_status.txt").write_text(
                f" M {disallowed}\n", encoding="ascii"
            )
            with self.assertRaisesRegex(SystemExit, "execution-relevant"):
                MODULE.validate_source_snapshot(case)


if __name__ == "__main__":
    unittest.main()
