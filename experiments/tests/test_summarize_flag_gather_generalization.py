import csv
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUMMARIZER = (
    ROOT / "experiments/scripts/summarize_flag_gather_generalization.py"
)


class FlagGatherGeneralizationTest(unittest.TestCase):
    def test_aggregates_fourteen_validated_gathers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "manifest.json"
            configs = []
            campaign = root / "campaign"
            for index in range(14):
                input_path = root / f"input-{index}.json"
                input_path.write_text("[]\n", encoding="ascii")
                input_hash = hashlib.sha256(
                    input_path.read_bytes()
                ).hexdigest()
                config_id = f"gather-{index:02d}"
                configs.append(
                    {
                        "id": config_id,
                        "kernel": "gather",
                        "pattern_length": 100 + index,
                        "pattern_max": 1000,
                        "input": input_path.name,
                        "input_sha256": input_hash,
                    }
                )
                case = campaign / "cases" / config_id
                (case / "comparison").mkdir(parents=True)
                (case / "issue-comparison").mkdir()
                for marker in (
                    case / "flag_gather_case.pass",
                    case / "comparison/xrage_comparison.pass",
                ):
                    marker.touch()
                digest_marker = (
                    "maa_issue_digest_per_instruction.pass"
                    if index == 0
                    else "maa_issue_digest_comparison.pass"
                )
                (case / "issue-comparison" / digest_marker).touch()
                with (case / "comparison/xrage_comparison.tsv").open(
                    "w", encoding="utf-8", newline=""
                ) as stream:
                    writer = csv.DictWriter(
                        stream,
                        fieldnames=(
                            "label",
                            "input_sha256",
                            "roi_simTicks",
                        ),
                        delimiter="\t",
                    )
                    writer.writeheader()
                    writer.writerows(
                        (
                            {
                                "label": "fused16",
                                "input_sha256": input_hash,
                                "roi_simTicks": 100,
                            },
                            {
                                "label": "compact16",
                                "input_sha256": input_hash,
                                "roi_simTicks": 90,
                            },
                            {
                                "label": "direct4",
                                "input_sha256": input_hash,
                                "roi_simTicks": 95,
                            },
                        )
                    )
            manifest.write_text(
                json.dumps({"configurations": configs}), encoding="utf-8"
            )

            output = root / "summary"
            result = subprocess.run(
                [str(SUMMARIZER), str(manifest), str(campaign), str(output)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(
                (output / "flag_gather_generalization.json").read_text()
            )
            self.assertEqual(len(report["configurations"]), 14)
            self.assertAlmostEqual(
                report["summary"]["direct_vs_fused"]["geomean_ratio"],
                0.95,
            )
            self.assertTrue(
                (output / "flag_gather_generalization.pass").is_file()
            )

            (
                campaign
                / "cases/gather-00/issue-comparison/maa_issue_digest_per_instruction.pass"
            ).unlink()
            rejected = subprocess.run(
                [
                    str(SUMMARIZER),
                    str(manifest),
                    str(campaign),
                    str(root / "rejected-summary"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("missing issue-digest validation marker", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
