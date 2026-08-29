import csv
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/audit_flag_complete_line_campaign.py"
SOURCE = "1" * 40
BINARY = "2" * 64


def write_tsv(
    path: Path, fields: tuple[str, ...], rows: list[dict[str, object]]
) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


class FlagCompleteLineCampaignAuditTest(unittest.TestCase):
    def create_campaign(self, root: Path) -> None:
        campaign = root / "campaign"
        (campaign / "cases").mkdir(parents=True)
        cases = []
        results = []
        for index in range(14):
            case_id = f"case{index}"
            length = 16 + index * 8
            input_path = root / f"input{index}.json"
            input_path.write_text("{}\n")
            cases.append(
                {"id": case_id, "input": input_path, "length": length}
            )
            results.append(
                {
                    "id": case_id,
                    "length": length,
                    "ticks": 100 + index,
                    "writes": length // 8,
                    "full": length // 8,
                    "partial": 0,
                    "hash": 1000 + index,
                }
            )
            case_root = campaign / "cases" / case_id
            (case_root / "run").mkdir(parents=True)
            manifest = {
                "source_commit": SOURCE,
                "runner_source_commit": SOURCE,
                "arm": "direct_index_4k",
                "guest_arm": "direct4",
                "result_scale": "1",
                "direct_retirement_line_handoff": "0",
                "virtual_complete_line_only": "1",
                "physical_tile_elements": "4096",
                "maa_logical_tile_elements": "16384",
                "virtual_combine_slots": "2048",
                "virtual_combine_words": "3072",
                "virtual_combine_ways": "16",
                "virtual_response_word_pool": "1024",
                "num_indirect_units_per_maa": "1",
                "timeout": "none",
                "input": str(input_path),
            }
            (case_root / "manifest.txt").write_text(
                "".join(f"{key}={value}\n" for key, value in manifest.items())
            )
            for name in ("checkpoint.exit", "restore.exit"):
                (case_root / name).write_text("0\n")
            (case_root / "source_status.txt").write_text("")
            (case_root / "source.diff").write_text("")
            (case_root / "xrage_attribution_smoke.pass").touch()
            (case_root / "restore.log").write_text(
                "m5_exit instruction encountered\n"
            )
            (case_root / "artifact_sha256.txt").write_text(
                f"{BINARY}  gem5.opt\n"
            )
            write_tsv(
                case_root / "result.tsv",
                (
                    "output_hash",
                    "roi_simTicks",
                    "final_simTicks",
                    "virtual_write_issues",
                    "virtual_write_completions",
                ),
                [
                    {
                        "output_hash": 1000 + index,
                        "roi_simTicks": 100 + index,
                        "final_simTicks": 200 + index,
                        "virtual_write_issues": length // 8,
                        "virtual_write_completions": length // 8,
                    }
                ],
            )
            (case_root / "run/stats.txt").write_text(
                f"simTicks {100 + index} # roi\nsimTicks {200 + index} # final\n"
            )
        with (campaign / "cases.list").open("w", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t")
            for case in cases:
                writer.writerow((case["id"], case["input"], case["length"]))
        write_tsv(
            campaign / "results.tsv",
            ("id", "length", "ticks", "writes", "full", "partial", "hash"),
            results,
        )

    def test_accepts_exact_campaign_and_rejects_missing_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.create_campaign(root)
            command = [
                "python3",
                str(SCRIPT),
                str(root / "campaign"),
                str(root / "audit"),
                "--source-commit",
                SOURCE,
                "--binary-sha256",
                BINARY,
            ]
            accepted = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(
                accepted.returncode, 0, accepted.stdout + accepted.stderr
            )
            self.assertTrue((root / "audit/audit.pass").is_file())

            (root / "audit/audit.pass").unlink()
            (root / "audit/audit.json").unlink()
            (root / "audit").rmdir()
            (root / "campaign/cases/case7/restore.exit").write_text("1\n")
            rejected = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)


if __name__ == "__main__":
    unittest.main()
