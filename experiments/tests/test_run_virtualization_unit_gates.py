import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments/scripts/run_virtualization_unit_gates.py"
REQUIRED_SCRIPTS = (
    "run_logical_spd_hidden_payload_unit.sh",
    "run_transparent_spd_controller_unit.sh",
    "run_logical_spd_cache_controller_unit.sh",
    "run_logical_spd_cache_abi_unit.sh",
)
RESPONSE_SCRIPT = "run_logical_stream_response_unit.sh"
PASS_MARKER = "virtualization_unit_gates.pass"


class VirtualizationUnitGateRunnerTest(unittest.TestCase):
    def make_repository(
        self,
        directory: Path,
        failing_script: str | None = None,
        response=False,
    ) -> Path:
        scripts = directory / "experiments/scripts"
        tests = directory / "experiments/tests"
        scripts.mkdir(parents=True)
        tests.mkdir(parents=True)
        for name in REQUIRED_SCRIPTS:
            self.write_shell_gate(scripts / name, name, name == failing_script)
        self.write_python_gate(tests / "test_spd_cache_state_model.py")
        if response:
            self.write_shell_gate(
                scripts / RESPONSE_SCRIPT,
                RESPONSE_SCRIPT,
                RESPONSE_SCRIPT == failing_script,
            )
        return directory

    def write_shell_gate(self, path: Path, name: str, fails: bool) -> None:
        exit_code = 9 if fails else 0
        path.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '{name} stdout\\n'\n"
            f"printf '{name} stderr\\n' >&2\n"
            f"exit {exit_code}\n",
            encoding="utf-8",
        )

    def write_python_gate(self, path: Path) -> None:
        path.write_text(
            "import sys\n"
            "print('state model stdout')\n"
            "print('state model stderr', file=sys.stderr)\n",
            encoding="utf-8",
        )

    def run_runner(self, repo: Path, output: Path, *extra: str):
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--repo",
                str(repo),
                "--out",
                str(output),
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def load_summary(self, output: Path) -> dict:
        return json.loads(
            (output / "summary.json").read_text(encoding="utf-8")
        )

    def test_pass_writes_summary_logs_and_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            repo = self.make_repository(temporary_path / "repo", response=True)
            output = temporary_path / "out"

            completed = self.run_runner(repo, output)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = self.load_summary(output)
            self.assertEqual(summary["status"], "passed")
            self.assertEqual(summary["source_commit"], "unavailable")
            self.assertEqual(summary["source_status"], [])
            self.assertEqual(summary["pass_marker"], PASS_MARKER)
            self.assertTrue((output / PASS_MARKER).is_file())
            self.assertEqual(len(summary["gates"]), 6)
            for gate in summary["gates"]:
                self.assertEqual(gate["status"], "passed")
                self.assertEqual(gate["return_code"], 0)
                self.assertIsInstance(gate["command"], list)
                self.assertIn("elapsed_host_validation_seconds", gate)
                for log_path in gate["log_paths"].values():
                    self.assertTrue((output / log_path).is_file())

    def test_failure_records_all_gates_without_publishing_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            repo = self.make_repository(
                temporary_path / "repo",
                failing_script="run_logical_spd_cache_controller_unit.sh",
            )
            output = temporary_path / "out"

            completed = self.run_runner(repo, output)

            self.assertEqual(completed.returncode, 1)
            summary = self.load_summary(output)
            failed = next(
                gate
                for gate in summary["gates"]
                if gate["name"] == "logical_spd_cache_controller"
            )
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(failed["return_code"], 9)
            self.assertIn(
                "run_logical_spd_cache_controller_unit.sh stderr",
                (output / failed["log_paths"]["stderr"]).read_text(),
            )
            self.assertFalse((output / PASS_MARKER).exists())

    def test_require_response_fails_when_response_script_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            repo = self.make_repository(temporary_path / "repo")
            output = temporary_path / "out"

            completed = self.run_runner(repo, output, "--require-response")

            self.assertEqual(completed.returncode, 1)
            summary = self.load_summary(output)
            response = summary["gates"][-1]
            self.assertEqual(response["name"], "logical_stream_response")
            self.assertEqual(response["return_code"], None)
            self.assertEqual(response["status"], "failed")
            self.assertFalse((output / PASS_MARKER).exists())

    def test_missing_required_gate_script_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            repo = self.make_repository(temporary_path / "repo")
            missing = (
                repo / "experiments/scripts/run_logical_spd_cache_abi_unit.sh"
            )
            missing.unlink()
            output = temporary_path / "out"

            completed = self.run_runner(repo, output)

            self.assertEqual(completed.returncode, 1)
            summary = self.load_summary(output)
            abi = next(
                gate
                for gate in summary["gates"]
                if gate["name"] == "logical_spd_cache_abi"
            )
            self.assertEqual(abi["return_code"], None)
            self.assertIn(
                "missing required gate command",
                (output / abi["log_paths"]["stderr"]).read_text(),
            )
            self.assertFalse((output / PASS_MARKER).exists())

    def test_refuses_to_overwrite_nonempty_output_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            repo = self.make_repository(temporary_path / "repo")
            output = temporary_path / "out"
            output.mkdir()
            sentinel = output / "keep-me"
            sentinel.write_text("unchanged", encoding="utf-8")

            completed = self.run_runner(repo, output)

            self.assertEqual(completed.returncode, 2)
            self.assertIn("refusing to overwrite nonempty", completed.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            self.assertFalse((output / "summary.json").exists())

    def test_console_output_is_one_line_per_gate_plus_final_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            repo = self.make_repository(temporary_path / "repo")
            output = temporary_path / "out"

            completed = self.run_runner(repo, output)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout.splitlines(),
                [
                    "PASS logical_spd_hidden_payload",
                    "PASS transparent_spd_controller",
                    "PASS logical_spd_cache_controller",
                    "PASS logical_spd_cache_abi",
                    "PASS spd_cache_state_model",
                    "PASS virtualization_unit_gates",
                ],
            )
            self.assertNotIn("stdout", completed.stdout)
            self.assertEqual(completed.stderr, "")

    def test_malformed_invocation_is_rejected_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"

            completed = subprocess.run(
                [sys.executable, str(RUNNER)],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn(
                "the following arguments are required: --out", completed.stderr
            )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
