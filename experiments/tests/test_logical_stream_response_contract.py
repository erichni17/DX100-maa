#!/usr/bin/env python3
"""Behavioral host checks for bounded logical stream response handling."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "tests/maa/logical_stream_response_test.cc"


class LogicalStreamResponseContractTest(unittest.TestCase):
    def compile_replay(self, output: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                os.environ.get("CXX", "g++"),
                f"-I{ROOT / 'src'}",
                "-std=c++17",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pedantic",
                str(TEST),
                "-o",
                str(output),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_bounded_response_replay_is_strict_cxx17(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "logical_stream_response_test"
            compilation = self.compile_replay(binary)
            self.assertEqual(
                compilation.returncode,
                0,
                compilation.stdout + compilation.stderr,
            )
            replay = subprocess.run(
                [str(binary)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
        self.assertEqual(replay.stdout, "logical_stream_response_test: PASS\n")


if __name__ == "__main__":
    unittest.main()
