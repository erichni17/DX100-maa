import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
GATE = ROOT / "experiments/scripts/run_sssp_tail_repair_gate.sh"
FULL = ROOT / "experiments/scripts/run_sssp_old_result_hybrid_full.sh"


class SsspTailRepairGateContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = GATE.read_text()
        cls.full = FULL.read_text()

    def test_shells_parse(self):
        subprocess.run(["bash", "-n", str(GATE)], check=True)
        subprocess.run(["bash", "-n", str(FULL)], check=True)

    def test_prepares_and_hashes_a_frozen_candidate(self):
        for exact in (
            "--prepare GATE_ROOT",
            "candidate_guest_sha256",
            "route_header_sha256",
            "frozen/identity.sha256",
            "state=frozen",
            "chmod 0555",
            "logical_elements=16384",
            "physical_tile_elements=4096",
            "graph_sha256",
        ):
            self.assertIn(exact, self.gate)
        self.assertIn("prebuilt_frozen", self.full)
        self.assertIn("SSSP_PREBUILT_GUEST", self.full)
        self.assertIn("SSSP_PREBUILT_GUEST_SHA256", self.full)

    def test_launch_is_exactly_once_candidate_only_and_unbounded(self):
        self.assertEqual(self.gate.count("systemd-run --user"), 1)
        intent = self.gate.index('>"$gate/launch.intent.tmp"')
        launch = self.gate.index("systemd-run --user")
        self.assertLess(intent, launch)
        self.assertIn("launch_count=1", self.gate)
        self.assertIn("[[ ! -e $gate/launch.intent", self.gate)
        self.assertIn("[[ ! -e $gate/full ]]", self.gate)
        self.assertIn("native_arms=0", self.gate)
        self.assertIn("wall_timeout=none", self.gate)
        self.assertNotIn("RuntimeMaxSec", self.gate)
        self.assertNotRegex(self.gate, r"(^|[;&|]\s*)timeout\s")
        self.assertNotIn("run_native", self.gate)

    def test_full_gate_requires_exact_tail_and_closed_ledgers(self):
        for exact in (
            "cpu_4133_batches > 0",
            "cpu_words >= 4133",
            "legacy_words == bounded_words + cpu_words",
            "max_host_spd_element < 4096",
            "out_of_range_spd_ids == 0",
            "instructions == routed && terminals == routed",
            "old_issues == old_responses",
            "a_read_issues == a_write_issues",
            "checkpoint.before.identity.sha256",
            "checkpoint.callback.files.sha256",
            "candidate_guest_origin",
        ):
            self.assertIn(exact, self.full)

    def test_validate_fails_closed_without_launch_ledgers(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [str(GATE), "--validate", tmp, "missing-unit"],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
