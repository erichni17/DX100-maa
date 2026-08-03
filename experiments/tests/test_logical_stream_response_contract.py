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
MAA_SOURCE = ROOT / "src/mem/MAA/MAA.cc"
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
        self.assertIn("LogicalStreamCounterEvent::UnsentPacketAborted", port)
        self.assertIn("entry.second.packet == pkt", port)
        self.assertIn("classifyLogicalStreamResponseDisposition(", port)
        self.assertIn(
            "MaxResponseSenderStateDepth = 64", RESPONSE_HEADER.read_text()
        )
        self.assertIn("MAA::countPacketAliases(PacketPtr packet) const", port)
        self.assertIn("MAA::erasePacketAliases(PacketPtr packet)", port)
        self.assertIn("my_deferred_pkt_map", port)
        for queue in (
            "my_outstanding_indirect_cache_read_pkts",
            "my_outstanding_indirect_cache_write_pkts",
            "my_outstanding_stream_cache_read_pkts",
            "my_outstanding_stream_cache_write_pkts",
            "my_outstanding_stream_mem_read_pkts",
            "my_outstanding_stream_mem_write_pkts",
            "my_outstanding_indirect_mem_read_pkts",
            "my_outstanding_indirect_mem_write_pkts",
        ):
            self.assertIn(queue, port)
        self.assertIn("sendCacheEvent/sendMemEvent", port)
        self.assertIn("sendTimingReq directly", port)

        recv_path = port.index("MAA::recvTimingResp(PacketPtr pkt")
        logical_path = port.index(
            "if (has_authoritative_owner || tmp.logicalResponseManaged) {",
            recv_path,
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

        accepted_start = response_path.index(
            "erasePacketAliases(pkt);\n"
            "        my_num_outstanding_stream_pkts[ledger_owner.maaID]"
        )
        accepted_path = response_path[accepted_start:]
        accepted_pop = accepted_path.index("releaseLogicalState(true);")
        accepted_delivery = accepted_path.index(
            "const LogicalStreamResponseResult accepted ="
        )
        accepted_promote = accepted_path.index(
            "AfterDeleteCompletionKind::PromoteDeferred"
        )
        self.assertLess(accepted_pop, accepted_delivery)
        self.assertLess(accepted_delivery, accepted_promote)
        self.assertIn("senderStateMatchesSnapshot(", port[recv_path:])
        self.assertIn("senderStateAliasedByOtherPacket(", port)

    def test_both_real_wrappers_invoke_shared_terminal_contract(self) -> None:
        cache = CACHE_PORT_SOURCE.read_text(encoding="utf-8")
        memory = MEM_PORT_SOURCE.read_text(encoding="utf-8")
        maa = MAA_HEADER.read_text(encoding="utf-8")
        response = RESPONSE_HEADER.read_text(encoding="utf-8")

        self.assertIn(
            "TimingResponseDisposition recvTimingResp(PacketPtr pkt,\n"
            "                                             CacheSidePort "
            "*responsePort);",
            maa,
        )
        self.assertIn("return invokeTimingResponseWrapper(", cache)
        self.assertIn("&outstandingCacheSidePackets", cache)
        self.assertIn("return maa->recvTimingResp(pkt, this);", cache)
        self.assertIn("settleOwnedResponseCredit", cache)
        self.assertIn("return invokeTimingResponseWrapper(", memory)
        self.assertIn("nullptr,", memory)
        self.assertIn("return maa->recvTimingResp(pkt, nullptr);", memory)
        for wrapper in (cache, memory):
            self.assertIn("pkt->deleteData();", wrapper)
            self.assertIn("delete pkt;", wrapper)
            self.assertIn("completeTimingResponseAfterDelete", wrapper)
            self.assertIn("fail-closed response disposition", wrapper)

        self.assertIn(
            "const TimingResponseDisposition disposition = receive();",
            response,
        )
        self.assertIn("deletePacket();", response)
        self.assertIn("afterDelete(disposition", response)
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
        self.assertIn("releaseLogicalState(true);", foreign)
        self.assertIn("classifyResponsePacketAliases(", foreign)
        self.assertIn("erasePacketAliases(pkt);", foreign)
        self.assertIn("return decision.disposition;", foreign)
        self.assertNotIn("my_outstanding_pkt_map.erase", foreign)
        self.assertNotIn("sendNextDeferredPacket", foreign)
        self.assertNotIn("applyLogicalStreamCounterEvent", foreign)

        fatal_start = recv.index("if (!decision.accepts()) {")
        fatal_end = recv.index(
            "\n\n        erasePacketAliases(pkt);", fatal_start
        )
        fatal = recv[fatal_start:fatal_end]
        self.assertIn("ResponseAborted", fatal)
        self.assertIn("UnsentPacketAborted", fatal)
        self.assertIn("abortOwnedLogicalResponse", fatal)
        self.assertIn("erasePacketAliases(pkt);", fatal)
        self.assertIn("releaseLogicalState(true);", fatal)
        self.assertNotIn("sendNextDeferredPacket", fatal)
        self.assertIn(
            "delivery after complete ownership detachment",
            recv,
        )
        self.assertIn("removed all production aliases", recv)

    def test_orphaned_queue_owner_settles_before_fatal_destruction(
        self,
    ) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        recv = port[port.index("MAA::recvTimingResp(PacketPtr pkt") :]
        foreign = recv[
            recv.index(
                "if (exact == my_outstanding_pkt_map.end()) {"
            ) : recv.index("const Addr owned_address = exact->first;")
        ]
        for evidence in (
            "findLogicalPacketProvenance(pkt)",
            "findUniqueLogicalLedgerOwner(provenance.transaction",
            "classifyOrphanedLogicalPacket(",
            "abortOwnedLogicalResponse(",
            "LogicalStreamCounterEvent::UnsentPacketAborted",
            "erasePacketAliases(pkt);",
            "return TimingResponseDisposition::FatalUnownedExtra;",
        ):
            self.assertIn(evidence, foreign)
        counter = foreign.index("decideLogicalStreamCounterUpdate(")
        abort = foreign.index("abortOwnedLogicalResponse(", counter)
        detach = foreign.index("erasePacketAliases(pkt);", abort)
        terminal = foreign.index(
            "return TimingResponseDisposition::FatalUnownedExtra;", detach
        )
        self.assertLess(counter, abort)
        self.assertLess(abort, detach)
        self.assertLess(detach, terminal)

    def test_deferred_validation_is_fail_atomic_before_pop(self) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        begin = port.index("MAA::sendNextDeferredPacket(Addr paddr)")
        end = port.index("bool MAA::scheduleNextSendMem()", begin)
        promotion = port[begin:end]
        for evidence in (
            "const DeferredPacket &queued",
            "queued.packet->req->getPaddr() == paddr",
            "queued.packet->cmd == queued.cmd",
            "canPromoteDeferredPacket(promotion_shape)",
            "senderStateMatchesSnapshot(",
            "exact_port_valid",
            "counter_headroom",
            "unique_deferred_owner",
            "terminally cleaned malformed/saturated deferred packet",
        ):
            self.assertIn(evidence, promotion)
        validate = promotion.index("canPromoteDeferredPacket(promotion_shape)")
        copy = promotion.index("DeferredPacket deferred = queued;")
        pop = promotion.index("pop_front();")
        self.assertLess(validate, copy)
        self.assertLess(copy, pop)
        self.assertNotIn("pop_front();", promotion[:validate])

    def test_actual_sending_port_provenance_survives_address_corruption(
        self,
    ) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        cache = CACHE_PORT_SOURCE.read_text(encoding="utf-8")
        header = MAA_HEADER.read_text(encoding="utf-8")
        recv = port[port.index("const Addr owned_address = exact->first;") :]
        self.assertIn("CacheSidePort *responseCreditPort", header)
        self.assertIn("CacheSidePort **sendingPort", header)
        self.assertIn("*sendingPort = port;", cache)
        self.assertGreaterEqual(
            port.count("responseCreditPort ="),
            5,
        )
        self.assertIn("? recorded_owner.responseCreditPort", recv)
        provenance = recv[: recv.index("if (has_authoritative_owner ||")]
        self.assertNotIn("core_addr(owned_address)", provenance)
        self.assertIn(
            "expected_response_port->settleOwnedResponseCredit()", recv
        )

    def test_normal_fatal_cleanup_aborts_without_false_success(self) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        start = port.index("if (!normal_valid) {")
        end = port.index("return fatalOwnedDisposition();", start)
        fatal = port[start:end]
        preflight = port[
            port.rfind("const NormalFatalOwnerDecision", 0, start) : start
        ]
        self.assertIn("decideNormalFatalOwnerSettlement(", preflight)
        self.assertIn("settlement_counters_valid", preflight)
        for evidence in (
            "settleOutstandingCounter",
            "abortReadOwner",
            "abortRetirementWrite",
            "abortReadResponse(",
            "abortRetirementWrite(",
            "erasePacketAliases(pkt);",
        ):
            self.assertIn(evidence, fatal)
        self.assertNotIn("retirementWriteComplete(", fatal)

    def test_same_line_logical_continuation_bypasses_only_deferred_fifo(
        self,
    ) -> None:
        stream = STREAM_SOURCE.read_text(encoding="utf-8")
        port = PORT_SOURCE.read_text(encoding="utf-8")
        response = RESPONSE_HEADER.read_text(encoding="utf-8")
        self.assertIn("mustBypassDeferredForContinuation", response)
        self.assertIn(
            "maa->getClockEdge(total_latency), true, true,\n"
            "                            true);",
            stream,
        )
        self.assertIn("logical same-line RMW continuation", port)
        self.assertIn("push_back(", port)
        self.assertNotIn("push_front(", port)

    def test_packetptr_owner_record_survives_corrupt_callback_metadata(
        self,
    ) -> None:
        header = MAA_HEADER.read_text(encoding="utf-8")
        port = PORT_SOURCE.read_text(encoding="utf-8")
        self.assertIn("struct LogicalPacketOwner", header)
        self.assertIn("my_logical_packet_owners", header)
        self.assertIn("authoritative_owner", port)
        self.assertIn("releaseRecordedLogicalSenderState", port)
        orphan = port[
            port.index(
                "if (exact == my_outstanding_pkt_map.end()) {"
            ) : port.index("const LogicalPacketProvenance provenance")
        ]
        self.assertIn("abortOwnedLogicalResponse", orphan)
        self.assertIn("abortReadResponse", orphan)
        self.assertIn("erasePacketAliases(pkt);", orphan)
        self.assertIn("my_logical_packet_owners.erase(pkt);", orphan)

    def test_credit_and_counter_preflight_precedes_accepted_mutation(
        self,
    ) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        accepted = port[port.index("if (has_authoritative_owner ||") :]
        credit = accepted.index("response_credit_valid")
        preflight = accepted.index("ResponseMutationPreflight")
        detach = accepted.index("erasePacketAliases(pkt);", preflight)
        self.assertLess(credit, preflight)
        self.assertLess(preflight, detach)
        self.assertIn("canSettleOwnedResponseCredit", accepted[:detach])
        self.assertIn("continuation_counter_valid", accepted[:detach])

    def test_invalid_map_owner_and_cross_stream_fallback_are_bounded(
        self,
    ) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        recv = port[port.index("MAA::recvTimingResp(PacketPtr pkt") :]
        self.assertIn(
            "findUniqueLogicalLedgerOwner(tmp.logicalTransaction",
            recv,
        )
        self.assertIn("tmp.maaIDs[0] == ledger_owner.maaID", recv)
        self.assertIn("abortOwnedLogicalResponse(terminal_address)", recv)
        self.assertIn("? recorded_owner.address : owned_address", recv)
        self.assertIn(
            "my_num_outstanding_stream_pkts[ledger_owner.maaID]",
            recv,
        )
        self.assertNotIn("&streamAccessUnits[received_tag.maaID]", recv)
        self.assertIn(
            "findUniqueLogicalLedgerOwner(received_tag,",
            recv,
        )

    def test_teardown_requires_zero_unique_owner_substrate(self) -> None:
        maa = MAA_SOURCE.read_text(encoding="utf-8")
        destructor = maa[
            maa.index("MAA::~MAA()") : maa.index("void MAA::addAddrRegion")
        ]
        port = PORT_SOURCE.read_text(encoding="utf-8")
        self.assertIn("responseTeardownShape() const", port)
        for owner in (
            "shape.mapOwners",
            "shape.deferredOwners",
            "shape.cacheSendOwners",
            "shape.memorySendOwners",
            "shape.activeLogicalLedgers",
            "shape.logicalOwnerRecords",
            "shape.outstandingCounters",
            "shape.cacheResponseCredits",
            "shape.pendingPostDeleteCompletion",
        ):
            self.assertIn(owner, port)
        self.assertIn("canDestroyResponseSubstrate(teardown)", destructor)
        self.assertIn("live response ownership at teardown", destructor)
        self.assertNotIn("delete deferred.packet", destructor)
        self.assertGreaterEqual(
            destructor.count("delete[] my_outstanding_"), 8
        )

    def test_size_sender_shape_and_post_delete_retirement_contract(
        self,
    ) -> None:
        port = PORT_SOURCE.read_text(encoding="utf-8")
        response = RESPONSE_HEADER.read_text(encoding="utf-8")
        maa = MAA_HEADER.read_text(encoding="utf-8")
        self.assertIn("ExpectedLogicalStreamResponseBytes = 64", response)
        self.assertIn("responseBytes == expectedBytes", response)
        self.assertIn("classifyTimingResponseCreditOwner", response)
        self.assertGreaterEqual(
            port.count("hasExpectedLogicalStreamResponseSize("), 2
        )
        self.assertNotIn(
            "response_kind == LogicalStreamResponseKind::Write ||\n"
            "            pkt->getSize() == 64",
            port,
        )
        self.assertIn("SenderStateOwnership senderStateOwnership", maa)
        self.assertIn("expectedResponseBytes", maa)
        self.assertIn("classifyResponseSenderState(", port)
        self.assertIn("expected_response_port", port)
        self.assertIn("ExpectedCachePort", port)
        self.assertIn("settleOwnedResponseCredit", port)
        self.assertIn("AfterDeleteCompletionKind::RetirementWrite", port)
        retirement = port.index("AfterDeleteCompletionKind::RetirementWrite")
        arm = port.rfind("afterDeleteCompletion = {", 0, retirement)
        retire = port.index("retirementWriteComplete(", arm)
        complete = port.index("MAA::completeTimingResponseAfterDelete")
        self.assertLess(arm, complete)
        self.assertGreater(retire, complete)
        pre_arm = port[port.rfind("erasePacketAliases(pkt);", 0, arm) : arm]
        self.assertIn("--my_num_outstanding_indirect_pkts", pre_arm)

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
