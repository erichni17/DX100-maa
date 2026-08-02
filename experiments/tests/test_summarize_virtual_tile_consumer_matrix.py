import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "experiments/analysis/summarize_virtual_tile_consumer_matrix.py"
SPEC = importlib.util.spec_from_file_location("consumer_matrix_summary", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def make_case(root: Path, name: str, ticks: int, output_hash="42") -> Path:
    case = root / name
    case.mkdir()
    (case / "checkpoint.exit").write_text("0\n")
    (case / "restore.exit").write_text("0\n")
    (case / "manifest.txt").write_text("source_commit=abcdef123456\n")
    (case / "restore.log").write_text(
        "Exiting @ tick 10 because m5_exit instruction encountered\nrestore_wall=999\n"
    )
    row = {"case": name, "output_hash": output_hash, "simTicks": str(ticks)}
    row.update({key: "7" for key in MODULE.COUNTERS})
    with (case / "result.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=row, delimiter="\t")
        writer.writeheader()
        writer.writerow(row)
    (case / "artifact_sha256.txt").write_text(
        "a" * 64
        + "  /build/gem5.opt\n"
        + "b" * 64
        + "  /build/test_virtual_tile_consumer_T16384\n"
    )
    return case


class ConsumerMatrixSummaryTest(unittest.TestCase):
    def test_direction_arithmetic_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref, candidate = make_case(root, "ref", 100), make_case(
                root, "candidate", 125
            )
            summary = MODULE.summarize(
                {"ref": ref, "cand": candidate}, {"baseline": "ref"}
            )
            item = next(
                row
                for row in summary["comparisons"]
                if row["candidate"] == "cand"
            )
            self.assertEqual(item["latency_delta"], 0.25)
            self.assertEqual(item["speedup"], 0.8)
            self.assertIn("+0.250000", MODULE.render_markdown(summary))

    def test_rejects_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref, candidate = make_case(root, "ref", 100), make_case(
                root, "cand", 101
            )
            (candidate / "artifact_sha256.txt").write_text(
                "c" * 64
                + "  /build/gem5.opt\n"
                + "b" * 64
                + "  /build/test_virtual_tile_consumer_T16384\n"
            )
            with self.assertRaisesRegex(ValueError, "gem5 SHA-256 differs"):
                MODULE.summarize(
                    {"ref": ref, "cand": candidate}, {"base": "ref"}
                )

    def test_rejects_output_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = make_case(root, "ref", 100, output_hash="41")
            candidate = make_case(root, "cand", 101, output_hash="42")
            with self.assertRaisesRegex(ValueError, "output hashes differ"):
                MODULE.summarize(
                    {"ref": ref, "cand": candidate}, {"base": "ref"}
                )

    def test_rejects_bad_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = make_case(root, "ref", 100)
            (case / "restore.exit").write_text("1\n")
            with self.assertRaisesRegex(
                ValueError, "restore.exit must be exactly 0"
            ):
                MODULE.summarize({"ref": case}, {"base": "ref"})

    def test_rejects_incomplete_log_and_multiple_result_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = make_case(root, "ref", 100)
            (case / "restore.log").write_text("no exit\n")
            with self.assertRaisesRegex(ValueError, "m5_exit terminal marker"):
                MODULE.summarize({"ref": case}, {"base": "ref"})
            make_case(root, "other", 100)
            result = root / "other/result.tsv"
            result.write_text(
                result.read_text() + result.read_text().split("\n", 1)[1]
            )
            with self.assertRaisesRegex(ValueError, "exactly one valid row"):
                MODULE.summarize({"other": root / "other"}, {"base": "other"})


if __name__ == "__main__":
    unittest.main()
