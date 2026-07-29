import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "experiments" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "summarize_flag_offset_epoch",
    SCRIPT_DIR / "summarize_flag_offset_epoch.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FlagOffsetEpochTest(unittest.TestCase):
    def write_config(self, path: Path, capacity: int, epoch: int) -> None:
        path.write_text(
            "\n".join(
                (
                    "[system.maa]",
                    "num_tile_elements=16384",
                    "num_row_table_rows_per_slice=16",
                    f"num_offset_table_entries={capacity}",
                    f"num_offset_table_epoch_entries={epoch}",
                )
            )
            + "\n",
            encoding="ascii",
        )

    def test_schedule_and_storage_treatments_are_separable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a = root / "a.ini"
            b = root / "b.ini"
            c = root / "c.ini"
            self.write_config(a, 16384, 16384)
            self.write_config(b, 16384, 4096)
            self.write_config(c, 4096, 4096)
            self.assertEqual(
                MODULE.config_differences(a, b),
                [
                    (
                        "system.maa",
                        "num_offset_table_epoch_entries",
                        "16384",
                        "4096",
                    )
                ],
            )
            self.assertEqual(
                MODULE.config_differences(b, c),
                [
                    (
                        "system.maa",
                        "num_offset_table_entries",
                        "16384",
                        "4096",
                    )
                ],
            )

    def test_zero_epoch_resolves_to_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            self.write_config(path, 4096, 0)
            values = MODULE.normalized_config(path)
            self.assertEqual(
                values[("system.maa", "num_offset_table_epoch_entries")],
                "4096",
            )

    def test_relative_rejects_nonpositive_denominator(self) -> None:
        with self.assertRaisesRegex(SystemExit, "positive observations"):
            MODULE.relative(1, 0)


if __name__ == "__main__":
    unittest.main()
