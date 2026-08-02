#!/usr/bin/env python3
"""Behavioral host checks for bounded logical stream response handling."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEST = ROOT / "tests/maa/logical_stream_response_test.cc"
PORT_SOURCE = ROOT / "src/mem/MAA/Port.cc"
STREAM_SOURCE = ROOT / "src/mem/MAA/StreamAccess.cc"
NOTES = (
    ROOT / "experiments/analysis/logical_stream_response_path_2026-08-02.md"
)


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

    def test_port_invokes_command_specific_counter_decision(self) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        self.assertIn("decideLogicalStreamCounterUpdate(", port)
        self.assertIn("LogicalStreamCounterEvent::Enqueued", port)
        self.assertIn("LogicalStreamCounterEvent::SendAccepted", port)
        self.assertIn("LogicalStreamCounterEvent::ResponseAccepted", port)
        self.assertIn("LogicalStreamCounterEvent::ResponseRejected", port)

        recv_path = port.index("void MAA::recvTimingResp")
        logical_path = port.index(
            "if (tmp.logicalResponseManaged) {", recv_path
        )
        response_path = port[
            logical_path : port.index(
                "if (logical_state != nullptr) {", logical_path
            )
        ]
        self.assertIn("applyLogicalStreamCounterEvent(", response_path)
        self.assertNotIn(
            "my_num_outstanding_stream_pkts[tmp.maaIDs[0]]--;",
            response_path,
        )

    def test_ordinary_writebackdirty_and_documented_ownership_survive(
        self,
    ) -> None:
        stream = STREAM_SOURCE.read_text(encoding="utf-8")
        notes = NOTES.read_text(encoding="utf-8")
        self.assertIn(
            "response_managed ? MemCmd::WriteReq\n"
            "                                        : MemCmd::WritebackDirty",
            stream,
        )
        for ownership in (
            "ReadReq` and `ReadExReq` relinquish",
            "response-bearing logical `WriteReq` relinquishes",
            "Rejected send attempts retain",
            "Rejected, stale, duplicate",
        ):
            self.assertIn(ownership, notes)


if __name__ == "__main__":
    unittest.main()
