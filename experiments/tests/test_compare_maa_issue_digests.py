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
        self,
        root: Path,
        baseline: str,
        candidate: str,
        allow_unit_reassignment: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        baseline_log = root / "baseline.log"
        candidate_log = root / "candidate.log"
        baseline_log.write_text(baseline, encoding="utf-8")
        candidate_log.write_text(candidate, encoding="utf-8")
        command = [
                "python3",
                str(SCRIPT),
                "--baseline",
                "native",
                "--output-dir",
                str(root / "comparison"),
                f"native={baseline_log}",
                f"virtual={candidate_log}",
            ]
        if allow_unit_reassignment:
            command.insert(2, "--allow-per-instruction-unit-reassignment")
        return subprocess.run(
            command,
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

    def test_reports_matching_instruction_streams_across_units(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = (
                "MAAIssueDigest: unit=0 instruction_tick=100 count=3 "
                "fnv=0x1111111111111111 mix=0x2222222222222222\n"
                "MAAIssueDigest: unit=0 instruction_tick=200 count=4 "
                "fnv=0x3333333333333333 mix=0x4444444444444444\n"
            )
            candidate = (
                "MAAIssueDigest: unit=0 instruction_tick=100 count=3 "
                "fnv=0x1111111111111111 mix=0x2222222222222222\n"
                "MAAIssueDigest: unit=1 instruction_tick=110 count=4 "
                "fnv=0x3333333333333333 mix=0x4444444444444444\n"
            )
            result = self.run_comparison(
                root, baseline, candidate, allow_unit_reassignment=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(
                (root / "comparison" / "maa_issue_digest_comparison.json")
                .read_text(encoding="utf-8")
            )
            comparison = report["comparisons"][0]
            self.assertFalse(comparison["match"])
            self.assertTrue(comparison["logical_sequence_match"])
            self.assertEqual(comparison["logical_source_requests"], 7)
            self.assertTrue(
                (root / "comparison/maa_issue_digest_per_instruction.pass")
                .is_file()
            )


if __name__ == "__main__":
    unittest.main()
