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
CACHE_PORT_SOURCE = ROOT / "src/mem/MAA/CacheSidePort.cc"
MEM_PORT_SOURCE = ROOT / "src/mem/MAA/MemSidePort.cc"
MAA_HEADER = ROOT / "src/mem/MAA/MAA.hh"
RESPONSE_HEADER = ROOT / "src/mem/MAA/LogicalStreamResponse.hh"
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

    def test_port_owns_exact_pointer_and_command_specific_counters(
        self,
    ) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        self.assertIn("decideLogicalStreamCounterUpdate(", port)
        self.assertIn("LogicalStreamCounterEvent::Enqueued", port)
        self.assertIn("LogicalStreamCounterEvent::SendAccepted", port)
        self.assertIn("LogicalStreamCounterEvent::ResponseAccepted", port)
        self.assertIn("LogicalStreamCounterEvent::ResponseAborted", port)
        self.assertIn("entry.second.packet == pkt", port)
        self.assertIn("classifyLogicalStreamResponseDisposition(", port)
        self.assertIn("MaxResponseSenderStateDepth = 64", port)
        self.assertIn(
            "senderStateChainExcludes(entry.second.packet->senderState,",
            port,
        )

        recv_path = port.index("MAA::recvTimingResp(PacketPtr pkt")
        logical_path = port.index(
            "if (tmp.logicalResponseManaged) {", recv_path
        )
        response_path = port[logical_path:]
        self.assertIn(
            "findLogicalSenderStateBounded(pkt->senderState)",
            port[recv_path:logical_path],
        )
        self.assertNotIn(
            "findNextSenderState<LogicalSPDTransactionState>",
            port[recv_path:],
        )
        self.assertIn("decideLogicalStreamCounterUpdate(", response_path)
        self.assertNotIn(
            "my_num_outstanding_stream_pkts[tmp.maaIDs[0]]--;",
            response_path,
        )

        accepted_path = response_path[
            response_path.index(
                "const LogicalStreamResponseResult accepted ="
            ) : response_path.index(
                "return TimingResponseDisposition::Retired;"
            )
        ]
        accepted_pop = accepted_path.rindex("releaseLogicalState();")
        accepted_erase = accepted_path.index(
            "my_outstanding_pkt_map.erase(exact);", accepted_pop
        )
        accepted_promote = accepted_path.index(
            "sendNextDeferredPacket(owned_address);", accepted_erase
        )
        self.assertLess(accepted_pop, accepted_erase)
        self.assertLess(accepted_erase, accepted_promote)

    def test_both_real_wrappers_invoke_shared_terminal_contract(self) -> None:
        cache = CACHE_PORT_SOURCE.read_text(encoding="utf-8")
        memory = MEM_PORT_SOURCE.read_text(encoding="utf-8")
        maa = MAA_HEADER.read_text(encoding="utf-8")
        response = RESPONSE_HEADER.read_text(encoding="utf-8")

        self.assertIn(
            "TimingResponseDisposition recvTimingResp(PacketPtr pkt, "
            "bool cached);",
            maa,
        )
        self.assertIn("return invokeTimingResponseWrapper(", cache)
        self.assertIn("&outstandingCacheSidePackets", cache)
        self.assertIn("return maa->recvTimingResp(pkt, true);", cache)
        self.assertIn("return invokeTimingResponseWrapper(", memory)
        self.assertIn("nullptr,", memory)
        self.assertIn("return maa->recvTimingResp(pkt, false);", memory)
        for wrapper in (cache, memory):
            self.assertIn("pkt->deleteData();", wrapper)
            self.assertIn("delete pkt;", wrapper)
            self.assertIn("fail-closed response disposition", wrapper)

        self.assertIn(
            "const TimingResponseDisposition disposition = receive();",
            response,
        )
        self.assertIn("deletePacket();", response)
        self.assertIn("failClosed(disposition, decision.valid);", response)
        self.assertIn("return true;", response)
        self.assertIn("bool sendRetry = false;", response)
        self.assertNotIn(
            "return false;",
            response[
                response.index(
                    "invokeTimingResponseWrapper("
                ) : response.index(
                    "/**\n * Immutable input",
                    response.index("invokeTimingResponseWrapper("),
                )
            ],
        )

    def test_extra_and_exact_corruption_paths_are_terminal(self) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        recv = port[port.index("MAA::recvTimingResp(PacketPtr pkt") :]
        foreign = recv[
            recv.index(
                "if (exact == my_outstanding_pkt_map.end()) {"
            ) : recv.index("const Addr owned_address = exact->first;")
        ]
        self.assertIn("releaseLogicalState();", foreign)
        self.assertIn("return decision.disposition;", foreign)
        self.assertNotIn("my_outstanding_pkt_map.erase", foreign)
        self.assertNotIn("sendNextDeferredPacket", foreign)
        self.assertNotIn("applyLogicalStreamCounterEvent", foreign)

        fatal_start = recv.index("if (!decision.accepts()) {")
        fatal = recv[
            fatal_start : recv.index(
                "\n\n        my_num_outstanding_stream_pkts", fatal_start
            )
        ]
        self.assertIn("ResponseAborted", fatal)
        self.assertIn("releaseLogicalState();", fatal)
        self.assertIn("my_outstanding_pkt_map.erase(exact);", fatal)
        self.assertIn("TimingResponseDisposition::FatalOwnedCorruption", fatal)
        self.assertNotIn("sendNextDeferredPacket", fatal)
        self.assertIn(
            "delivery; removed ownership before fatal destruction",
            recv,
        )
        self.assertIn("removed map ownership before fatal destruction", recv)

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
            "Dropped extra stale, duplicate",
        ):
            self.assertIn(ownership, notes)


if __name__ == "__main__":
    unittest.main()
