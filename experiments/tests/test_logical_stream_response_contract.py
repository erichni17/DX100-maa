#!/usr/bin/env python3
"""Source and host-contract checks for logical stream response retention."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "src/mem/MAA/LogicalStreamResponse.hh"
STREAM_HEADER = ROOT / "src/mem/MAA/StreamAccess.hh"
STREAM_SOURCE = ROOT / "src/mem/MAA/StreamAccess.cc"
PORT_SOURCE = ROOT / "src/mem/MAA/Port.cc"
MAA_HEADER = ROOT / "src/mem/MAA/MAA.hh"
IF_HEADER = ROOT / "src/mem/MAA/IF.hh"
TEST = ROOT / "tests/maa/logical_stream_response_test.cc"
RUNNER = ROOT / "experiments/scripts/run_logical_stream_response_unit.sh"
NOTES = (
    ROOT / "experiments/analysis/logical_stream_response_path_2026-08-02.md"
)


class LogicalStreamResponseContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ledger = LEDGER.read_text(encoding="utf-8")
        cls.stream_header = STREAM_HEADER.read_text(encoding="utf-8")
        cls.stream_source = STREAM_SOURCE.read_text(encoding="utf-8")
        cls.port_source = PORT_SOURCE.read_text(encoding="utf-8")
        cls.maa_header = MAA_HEADER.read_text(encoding="utf-8")
        cls.if_header = IF_HEADER.read_text(encoding="utf-8")
        cls.test = TEST.read_text(encoding="utf-8")

    def test_identity_is_explicit_and_address_is_not_identity(self) -> None:
        tag = self.ledger[
            self.ledger.index(
                "struct LogicalStreamTransactionTag"
            ) : self.ledger.index("enum class LogicalStreamResponseKind")
        ]
        for field in (
            "uint16_t maaID",
            "uint64_t transactionID",
            "LogicalStreamAction action",
            "uint16_t logicalID",
            "uint16_t page",
            "uint64_t generation",
            "int16_t slot",
            "bool valid() const",
        ):
            self.assertIn(field, tag)
        self.assertNotIn("Addr address", tag)
        self.assertIn("not part of the transaction identity", self.ledger)
        self.assertIn("line address is instead checked", self.ledger)

    def test_line_ledger_is_fixed_and_finite(self) -> None:
        for evidence in (
            "PageElements = 4096",
            "CacheLineBytes = 64",
            "MaxLinesPerPage",
            "std::array<LineState, MaxLinesPerPage>",
            "lineCount > MaxLinesPerPage",
            "issuedLines == expectedLines",
        ):
            self.assertIn(evidence, self.ledger)
        ledger_body = self.ledger[
            self.ledger.index(
                "class LogicalStreamResponseLedger"
            ) : self.ledger.index("} // namespace gem5")
        ]
        for forbidden in ("std::vector", "std::deque", "std::map", "new "):
            self.assertNotIn(forbidden, ledger_body)

    def test_rejection_classes_are_counted_without_response_mutation(
        self,
    ) -> None:
        for result in (
            "Stale",
            "Duplicate",
            "WrongKind",
            "WrongTransaction",
            "WrongPage",
            "WrongSlot",
            "WrongMAA",
            "WrongAddress",
        ):
            self.assertIn(
                f"LogicalStreamResponseResult::{result}", self.ledger
            )
        for counter in (
            "responseCounters.stale",
            "responseCounters.duplicate",
            "responseCounters.wrongKind",
            "responseCounters.wrongTransaction",
            "responseCounters.wrongPage",
            "responseCounters.wrongSlot",
            "responseCounters.wrongMAA",
            "responseCounters.wrongAddress",
        ):
            self.assertIn(counter, self.ledger)
        self.assertIn("validateResponse(", self.ledger)
        self.assertIn("acceptResponse(", self.ledger)
        self.assertIn("before retiring its outstanding entry", self.ledger)

    def test_normal_stream_store_path_is_preserved(self) -> None:
        self.assertIn("MemCmd::WritebackDirty", self.stream_source)
        self.assertIn(
            "response_managed ? MemCmd::WriteReq", self.stream_source
        )
        self.assertIn("logicalResponseManaged()", self.stream_source)
        write_sent = self.stream_source[
            self.stream_source.index(
                "void StreamAccessUnit::writePacketSent"
            ) : self.stream_source.index("bool StreamAccessUnit::recvData")
        ]
        self.assertIn("if (logicalResponseManaged())", write_sent)
        self.assertIn("return;", write_sent)
        self.assertIn("my_received_responses++;", write_sent)
        self.assertIn("Legacy transparent", self.if_header)
        self.assertIn("bool logicalResponseManaged;", self.if_header)

    def test_controller_writeback_is_retained_through_writeresp(self) -> None:
        for evidence in (
            "LogicalSPDTransactionState : public Packet::SenderState",
            "pkt->pushSenderState(new LogicalSPDTransactionState",
            "MemCmd::WriteReq",
            "true, true",
            "writeResponseReceived",
            "response_managed && !logicalResponseLedger.isComplete()",
        ):
            self.assertIn(evidence, self.stream_header + self.stream_source)
        for evidence in (
            "bool logicalResponseManaged;",
            "LogicalStreamTransactionTag logicalTransaction;",
            "deferred.logicalResponseManaged",
            "sendPacketRetirementCache(it->packet)",
            "my_outstanding_pkt_map[paddr].sent = true",
            "tmp.cmd != MemCmd::WriteReq",
        ):
            self.assertIn(evidence, self.maa_header + self.port_source)

    def test_port_cross_checks_full_tuple_before_erasing(self) -> None:
        response = self.port_source[
            self.port_source.index(
                "void MAA::recvTimingResp"
            ) : self.port_source.index("void MAA::scheduleSendCacheEvent")
        ]
        for evidence in (
            "classify_tag_mismatch",
            "received.transactionID != expected.transactionID",
            "received.logicalID != expected.logicalID",
            "received.page != expected.page",
            "received.generation != expected.generation",
            "received.slot != expected.slot",
            "logical_state->lineAddress != paddr",
            "stream.rejectLogicalResponse(result);",
            "my_outstanding_pkt_map.erase(paddr);",
            "pkt->popSenderState()",
        ):
            self.assertIn(evidence, response)
        rejected = response.index("stream.rejectLogicalResponse(result);")
        erased = response.index("my_outstanding_pkt_map.erase(paddr);")
        self.assertLess(rejected, erased)

    def test_host_replay_covers_reorder_duplicates_and_old_address_reuse(
        self,
    ) -> None:
        for evidence in (
            "testFillDelayedReorderedDuplicateCallbacks",
            "testWritebackRejectsWrongIdentityWithoutMutation",
            "testOldResponseCannotCompleteReusedAddressTransaction",
            "testStaleResponseAfterTransactionReset",
            "testFixedLedgerCapacityAndExactIssueCount",
            "Result::Duplicate",
            "Result::Stale",
            "Result::WrongTransaction",
            "Result::WrongPage",
            "Result::WrongSlot",
            "0x3000",
        ):
            self.assertIn(evidence, self.test)

    def test_host_replay_compiles_and_executes_without_gem5(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "logical_stream_response_test"
            compilation = subprocess.run(
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
                    str(binary),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
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
        self.assertIn("logical_stream_response_test: PASS", replay.stdout)

    def test_runner_and_notes_are_non_simulation_and_base_neutral(
        self,
    ) -> None:
        runner = RUNNER.read_text(encoding="utf-8")
        notes = NOTES.read_text(encoding="utf-8")
        self.assertIn("logical_stream_response_test.cc", runner)
        self.assertIn("test_logical_stream_response_contract.py", runner)
        self.assertNotIn("gem5.opt", runner)
        self.assertNotIn("scons", runner.lower())
        for caveat in (
            "not wired to the logical",
            "does not validate the rejected ABI base",
            "does not run a gem5",
            "Ordinary stream stores retain",
        ):
            self.assertIn(caveat, notes)


if __name__ == "__main__":
    unittest.main()
