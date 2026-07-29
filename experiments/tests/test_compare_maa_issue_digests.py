#!/usr/bin/env python3

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments" / "scripts" / "compare_maa_issue_digests.py"


class IssueDigestComparisonTest(unittest.TestCase):
    def run_comparison(
        self, root: Path, baseline: str, candidate: str
    ) -> subprocess.CompletedProcess[str]:
        baseline_log = root / "baseline.log"
        candidate_log = root / "candidate.log"
        baseline_log.write_text(baseline, encoding="utf-8")
        candidate_log.write_text(candidate, encoding="utf-8")
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--baseline",
                "native",
                "--output-dir",
                str(root / "comparison"),
                f"native={baseline_log}",
                f"virtual={candidate_log}",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_accepts_matching_digests_with_different_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = (
                "1: MAAIssueDigest: unit=0 instruction_tick=100 count=3 "
                "fnv=0x1111111111111111 mix=0x2222222222222222\n"
            )
            candidate = (
                "9: MAAIssueDigest: unit=0 instruction_tick=900 count=3 "
                "fnv=0x1111111111111111 mix=0x2222222222222222\n"
            )
            result = self.run_comparison(root, baseline, candidate)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(
                (root / "comparison" / "maa_issue_digest_comparison.json")
                .read_text(encoding="utf-8")
            )
            self.assertTrue(report["comparisons"][0]["match"])
            self.assertEqual(
                report["comparisons"][0]["matched_source_requests"], 3
            )

    def test_rejects_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = (
                "MAAIssueDigest: unit=0 instruction_tick=100 count=3 "
                "fnv=0x1111111111111111 mix=0x2222222222222222\n"
            )
            candidate = (
                "MAAIssueDigest: unit=0 instruction_tick=200 count=3 "
                "fnv=0x1111111111111112 mix=0x2222222222222222\n"
            )
            result = self.run_comparison(root, baseline, candidate)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("digest streams differ", result.stderr)


if __name__ == "__main__":
    unittest.main()
