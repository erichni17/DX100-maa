#!/usr/bin/env python3
"""Source contracts for the provisional logical SPD-cache vertical slice."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class LogicalSpdCacheVerticalSliceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.slice = (ROOT / "src/mem/MAA/LogicalSPDCacheSlice.hh").read_text()
        cls.maa_hh = (ROOT / "src/mem/MAA/MAA.hh").read_text()
        cls.maa_cc = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        cls.stream = (ROOT / "src/mem/MAA/StreamAccess.cc").read_text()
        cls.alu = (ROOT / "src/mem/MAA/ALU.cc").read_text()
        cls.port = (ROOT / "src/mem/MAA/Port.cc").read_text()
        cls.spd_hh = (ROOT / "src/mem/MAA/SPD.hh").read_text()
        cls.cpu = (ROOT / "src/mem/MAA/CpuSidePort.cc").read_text()

    def test_controller_contract_is_fixed_and_narrow(self) -> None:
        for evidence in (
            "LogicalDescriptors = 2",
            "Pages = 4",
            "Slots = 2",
            "PageElements = 4096",
            "LineWindow = 8",
            "OperationMemorySerials = Pages * 3",
            "uint64_t producerTransaction = 0",
            "std::array<DescriptorRecord, LogicalDescriptors>",
            "std::array<LogicalSPDCacheSlice, MaxLogicalSPDCacheMAAs>",
            "static_assert(sizeof(Counters) == 20 * sizeof(uint64_t))",
        ):
            self.assertIn(evidence, self.slice + self.maa_hh)
        self.assertNotIn("std::vector", self.slice)
        self.assertNotIn("std::deque", self.slice)
        self.assertNotIn("new ", self.slice)

    def test_admission_prevalidates_span_shape_overlap_and_serials(
        self,
    ) -> None:
        begin = self.slice.index("AdmitResult admit(")
        allocate = self.slice.index("controller.allocate(", begin)
        before_allocate = self.slice[begin:allocate]
        for evidence in (
            "request.sourceLogical == request.destinationLogical",
            "request.dataType != Float64DataType",
            "request.operation > MaxScalarOperation",
            "!validSpan(request.destination)",
            "!allPagesReady(source.handle)",
            "spansOverlap(source.backing, request.destination)",
            "!controller.canAllocateMemorySerials(OperationMemorySerials)",
        ):
            self.assertIn(evidence, before_allocate)

    def test_hidden_payload_stays_private_and_micro_op_only(self) -> None:
        private = self.spd_hh.index("private:")
        hidden = self.spd_hh.index("getLogicalSpdData", private)
        public = self.spd_hh.index("public:", hidden)
        self.assertLess(private, hidden)
        self.assertLess(hidden, public)
        self.assertIn(
            "check_tile_id(tile_id, word_size)", self.spd_hh[public:]
        )
        self.assertIn("logicalSpdHiddenSlotBaseTileID", self.maa_cc)
        self.assertIn("micro.logicalResponseManaged = true", self.maa_cc)

    def test_writeback_is_direct_and_response_completed(self) -> None:
        begin = self.stream.index("StreamAccessUnit::createLogicalPacket")
        end = self.stream.index(
            "void StreamAccessUnit::createReadPacket", begin
        )
        direct = self.stream[begin:end]
        self.assertIn("MemCmd::WriteReq", direct)
        self.assertNotIn("MemCmd::ReadExReq", direct)
        self.assertNotIn("MemCmd::WritebackDirty", direct)
        erase = self.port.index("my_outstanding_pkt_map.erase(exact)")
        callback = self.port.index("logicalStreamResponseReceived(", erase)
        self.assertLess(erase, callback)

    def test_alu_uses_captured_scalar_and_distinct_hidden_slots(self) -> None:
        begin = self.alu.index("ALUUnit::executeLogicalInstruction")
        logical = self.alu[begin:]
        for evidence in (
            "my_instruction->backingAddr",
            "getLogicalSpdData<double>",
            "setLogicalSpdData<double>",
            "src1SpdID ==",
            "dst1SpdID",
            "Instruction::OPType::MAX_OP",
        ):
            self.assertIn(evidence, logical)
        self.assertNotIn("rf->getData", logical)
        self.assertIn("micro.backingAddr = compute.scalarBits", self.maa_cc)
        compute = self.maa_cc.index(
            "const auto compute = slice.pendingCompute()"
        )
        finish = self.maa_cc.index("MAA::finishLogicalSPDMicroOp", compute)
        generated = self.maa_cc[compute:finish]
        self.assertIn("micro.src1SpdID", generated)
        self.assertIn("micro.dst1SpdID", generated)
        self.assertNotIn("micro.src1LogicalID", generated)
        self.assertNotIn("micro.dst1LogicalID", generated)

    def test_high_level_response_waits_for_final_ack_path(self) -> None:
        begin = self.maa_cc.index("MAA::finishLogicalSPDMicroOp")
        end = self.maa_cc.index("void MAA::finishInstructionCompute", begin)
        finish = self.maa_cc[begin:end]
        completion = finish.index("slice.operationComplete()")
        response = finish.index("packet->makeTimingResponse()")
        retire = finish.index("slice.retireCompletedOperation()")
        self.assertLess(completion, response)
        self.assertLess(retire, response)
        submit_begin = self.maa_cc.index("submitLogicalSPDDescriptor")
        submit_end = self.maa_cc.index(
            "bool MAA::submitTransparentDescriptor", submit_begin
        )
        self.assertNotIn(
            "makeTimingResponse", self.maa_cc[submit_begin:submit_end]
        )

    def test_cpu_keeps_accepted_high_byte_abi_and_dispatches_after_validation(
        self,
    ) -> None:
        for evidence in (
            "LogicalSPDCacheABI::decodeWord0(data)",
            "validateLogicalALUScalar",
            "validateDestinationSpan",
            "my_instruction_recvs[instruction_id] = true",
        ):
            self.assertIn(evidence, self.cpu)
        self.assertNotIn(
            "logical SPD-cache controller integration is not implemented",
            self.cpu,
        )
        self.assertIn(
            "submitLogicalSPDDescriptor(instruction, pkt)", self.maa_cc
        )


if __name__ == "__main__":
    unittest.main()
