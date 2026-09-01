import json
import pathlib
import subprocess
import tempfile
import unittest

from experiments.scripts import compare_gzz_shared_payload_phases as compare

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "experiments/scripts/compare_gzz_shared_payload_phases.py"


class GzzSharedPayloadPhaseComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def stats(
        self, name: str, multiplier: int, response_slots: int
    ) -> pathlib.Path:
        path = self.root / f"{name}.stats"
        values = {
            "simTicks": 100 * multiplier,
            "IND_StrictTwoPhaseAIssueCycles": 10 * multiplier,
            "IND_StrictTwoPhaseBackingCycles": 8 * multiplier,
            "IND_StrictTwoPhaseBFetchCycles": 7,
            "IND_StrictTwoPhaseConsumerCycles": 20,
            "IND_VirtResponseSlotHighWater": response_slots,
            "IND_VirtResponseWordHighWater": response_slots * 16,
            "IND_VirtSharedPayloadHighWater": 64,
            "IND_VirtBuildRounds": multiplier,
            "IND_VirtFanoutScanCycles": 4 if multiplier > 1 else 0,
            "IND_VirtFanoutScanWaitCycles": 4 if multiplier > 1 else 0,
        }
        lines = ["---------- Begin Simulation Statistics ----------"]
        for key, value in values.items():
            prefix = "" if key == "simTicks" else "system.maa.I0_"
            lines.append(f"{prefix}{key} {value}")
        lines.append("---------- End Simulation Statistics ----------")
        lines.append("---------- Begin Simulation Statistics ----------")
        lines.append("simTicks 999")
        lines.append("---------- End Simulation Statistics ----------")
        path.write_text("\n".join(lines) + "\n")
        return path

    def trace(self, name: str, high_water: int) -> pathlib.Path:
        path = self.root / f"{name}.trace"
        path.write_text(
            "1: system.maa: event=strict_two_phase_timing schema=2 "
            "A_ISSUE_TICKS=10 BACKING_TICKS=8 B_FETCH_TICKS=7 "
            "CONSUMER_TICKS=20 terminal=1\n"
            "2: global: event=shared_result_payload_complete schema=1 "
            f"capacity=4096 high_water={high_water} transfers=16 "
            "rollbacks=0 line_shadow_bytes=0\n"
        )
        return path

    def test_detects_source_mlp_collapse(self):
        result = compare.compare(
            self.stats("reference", 1, 128),
            self.trace("reference", 4096),
            self.stats("candidate", 8, 1),
            self.trace("candidate", 3484),
        )
        self.assertEqual(
            result["diagnosis"]["classification"], "SOURCE_MLP_COLLAPSE"
        )
        self.assertTrue(result["historical_cross_binary"])
        self.assertFalse(result["performance_attribution"])
        self.assertEqual(len(result["artifacts"]["analyzer"]["sha256"]), 64)
        self.assertEqual(
            result["comparisons"]["IND_VirtResponseSlotHighWater"]["ratio"],
            1 / 128,
        )

    def test_requires_one_event(self):
        trace = self.trace("duplicate", 1)
        trace.write_text(trace.read_text() * 2)
        with self.assertRaisesRegex(ValueError, "expected one"):
            compare.parse_event(trace, "strict_two_phase_timing")

    def test_sums_per_unit_stats(self):
        path = self.stats("per-unit", 1, 64)
        text = path.read_text().replace(
            "system.maa.I0_IND_VirtResponseSlotHighWater 64",
            "system.maa.I0_IND_VirtResponseSlotHighWater 64\n"
            "system.maa.I1_IND_VirtResponseSlotHighWater 32",
        )
        path.write_text(text)
        self.assertEqual(
            compare.parse_first_stats(path)["IND_VirtResponseSlotHighWater"],
            96,
        )

    def test_cli_writes_machine_readable_result(self):
        output = self.root / "result.json"
        completed = subprocess.run(
            [
                str(SCRIPT),
                "--reference-stats",
                str(self.stats("reference-cli", 1, 128)),
                "--reference-trace",
                str(self.trace("reference-cli", 4096)),
                "--candidate-stats",
                str(self.stats("candidate-cli", 8, 1)),
                "--candidate-trace",
                str(self.trace("candidate-cli", 3484)),
                "--output",
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(output.read_text())["diagnosis"]["classification"],
            "SOURCE_MLP_COLLAPSE",
        )

    def test_output_is_json_serializable(self):
        result = compare.compare(
            self.stats("reference-json", 1, 128),
            self.trace("reference-json", 4096),
            self.stats("candidate-json", 8, 1),
            self.trace("candidate-json", 3484),
        )
        self.assertIn("SOURCE_MLP_COLLAPSE", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
