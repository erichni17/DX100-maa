#!/usr/bin/env python3
"""Source-level contract checks for logical SPD-cache ABI patch 2."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ABI = ROOT / "include/gem5/maa_logical_spd_cache_abi.hh"
API = ROOT / "benchmarks/API/MAA_gem5.hpp"
IF_HEADER = ROOT / "src/mem/MAA/IF.hh"
IF_SOURCE = ROOT / "src/mem/MAA/IF.cc"
CPU_PORT = ROOT / "src/mem/MAA/CpuSidePort.cc"
TRANSPARENT_TEST = ROOT / (
    "experiments/tests/test_transparent_spd_controller_contract.py"
)
NOTE = ROOT / (
    "experiments/analysis/logical_spd_cache_abi_contract_2026-08-02.md"
)


class LogicalSpdCacheAbiContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.abi = ABI.read_text(encoding="utf-8")
        cls.api = API.read_text(encoding="utf-8")
        cls.if_header = IF_HEADER.read_text(encoding="utf-8")
        cls.if_source = IF_SOURCE.read_text(encoding="utf-8")
        cls.cpu_port = CPU_PORT.read_text(encoding="utf-8")

    def test_high_byte_audit_preserves_legacy_physical_zeroes(self) -> None:
        for evidence in (
            "LegacyPhysicalHighByte = 0x00",
            "NoOperand = 0xff",
            "src2 == NoOperand",
            "src1 < LogicalDescriptorCount",
            "dst1 < LogicalDescriptorCount",
        ):
            self.assertIn(evidence, self.abi)
        self.assertIn("encodeLogicalALUScalarHeader", self.api)
        self.assertNotIn("logical bytes to `0xff`", self.abi.lower())

    def test_instruction_keeps_logical_identity_separate_from_physical_ids(
        self,
    ) -> None:
        for field in (
            "src1LogicalID, src2LogicalID, dst1LogicalID",
            "src1LogicalGeneration, dst1LogicalGeneration",
            "controllerTransactionID",
            "controllerSrcSlot, controllerDstSlot",
            "isLogicalALUScalar() const",
        ):
            self.assertIn(field, self.if_header)
        for initializer in (
            "src1LogicalID(-1)",
            "src2LogicalID(-1)",
            "dst1LogicalID(-1)",
            "src1LogicalGeneration(0)",
            "dst1LogicalGeneration(0)",
        ):
            self.assertIn(initializer, self.if_source)

    def test_decoder_rejects_mixed_or_unsupported_forms_before_dispatch(
        self,
    ) -> None:
        header = self.cpu_port.index("const auto logical_header =")
        opcode = self.cpu_port.index("current_instruction->opcode =", header)
        logical_fields = self.cpu_port.index(
            "current_instruction->src1LogicalID =", opcode
        )
        self.assertLess(header, opcode)
        self.assertLess(opcode, logical_fields)
        for evidence in (
            "LogicalSPDCacheABI::decodeWord0(data)",
            "HeaderKind::Unsupported",
            "HeaderKind::LogicalALUScalar",
            "Logical high-byte operands are only supported for ",
            "ALU_SCALAR, got opcode %d",
            "LogicalSPDCacheABI::NoAddress",
            "LogicalSPDCacheABI::ScalarOperandShape shape",
            "validateLogicalALUScalar",
            "Rejected logical ALU_SCALAR ABI shape",
            "controller state mutation",
        ):
            self.assertIn(evidence, self.cpu_port)

    def test_scalar_shape_validation_is_complete_and_payload_free(
        self,
    ) -> None:
        for validation in (
            "WrongOpcode",
            "UnsupportedDataType",
            "UnsupportedOperation",
            "InvalidLogicalID",
            "LogicalSource2Present",
            "AliasedLogicalIDs",
            "MixedPhysicalOperands",
            "MissingScalarRegister",
            "ExtraRegisterOperand",
            "Conditional",
            "UnexpectedBaseAddress",
            "MissingDestinationBacking",
        ):
            self.assertIn(validation, self.abi)
        self.assertNotIn("std::vector", self.abi)
        self.assertNotIn("new ", self.abi)

    def test_api_emits_ordinary_scalar_opcode_and_future_wait_shape(
        self,
    ) -> None:
        begin = self.api.index("inline void maa_alu_scalar_logical")
        end = self.api.index("template <class T1>", begin + 1)
        helper = self.api[begin:end]
        for evidence in (
            "encodeLogicalALUScalarHeader",
            "INSTR_tsrc1_tsrc2_rdst1_rdst2_rsrc1_rsrc2_rsrc3_csrc",
            "*INSTR_baseaddr = NA_UINT64",
            "*INSTR_backingaddr = (uint64_t)destination_backing",
            "scalar_reg",
        ):
            self.assertIn(evidence, helper)
        self.assertIn("void maa_wait_logical_page", self.api)
        self.assertIn("void maa_wait_logical_tile", self.api)
        self.assertIn("LOGICAL_PAGES_PER_DESCRIPTOR 4", self.api)

    def test_transparent_opcode_and_response_paths_remain_out_of_scope(
        self,
    ) -> None:
        self.assertIn("VIRTUAL_TILE_ALU_SCALAR", self.api)
        self.assertIn("maa_virtual_tile_alu_scalar_store", self.api)
        self.assertIn("VIRTUAL_TILE_ALU_SCALAR", self.cpu_port)
        self.assertTrue(TRANSPARENT_TEST.is_file())
        stream = (ROOT / "src/mem/MAA/StreamAccess.cc").read_text(
            encoding="utf-8"
        )
        self.assertIn("WritebackDirty", stream)
        self.assertIn("writePacketSent", stream)

    def test_note_states_the_decoder_only_boundary(self) -> None:
        note = NOTE.read_text(encoding="utf-8")
        for caveat in (
            "not connected to the cache controller",
            "not change StreamAccess or Port response semantics",
            "does not retire opcode 16",
            "No gem5 simulation",
            "no performance claim",
        ):
            self.assertIn(caveat, note)


if __name__ == "__main__":
    unittest.main()
