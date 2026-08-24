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
GATE = ROOT / "experiments/scripts/run_logical_spd_cache_abi_unit.sh"
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
        cls.gate = GATE.read_text(encoding="utf-8")

    def test_focused_gate_uses_repository_guest_language_mode(self) -> None:
        self.assertIn("-std=c++11", self.gate)
        self.assertNotIn("-std=c++17", self.gate)

    def test_high_byte_audit_preserves_legacy_physical_zeroes(self) -> None:
        for evidence in (
            "LegacyPhysicalHighByte = 0x00",
            "NoOperand = 0xff",
            "src2 == NoOperand",
            "src1 < LogicalDescriptorCount",
            "LogicalVectorIDBias",
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
            "src1LogicalGeneration, src2LogicalGeneration",
            "src2LogicalGeneration",
            "controllerTransactionID",
            "controllerSrcSlot, controllerDstSlot",
            "isLogicalALUScalar() const",
            "isLogicalALUVector() const",
        ):
            self.assertIn(field, self.if_header)
        for initializer in (
            "src1LogicalID(-1)",
            "src2LogicalID(-1)",
            "dst1LogicalID(-1)",
            "src1LogicalGeneration(0)",
            "src2LogicalGeneration(0)",
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
            "HeaderKind::LogicalALUVector",
            "Logical high-byte operands are only supported for ",
            "ALU_SCALAR or ALU_VECTOR, got opcode %d",
            "LogicalSPDCacheABI::NoAddress",
            "LogicalSPDCacheABI::ScalarOperandShape shape",
            "validateLogicalALUScalar",
            "num_regs",
            "getAddrRegion(data)",
            "validateDestinationSpan",
            "validateSourceSpan",
            "Rejected logical ALU_SCALAR ABI shape",
            "Rejected logical ALU_SCALAR source backing",
            "Rejected logical ALU_SCALAR destination ",
            "controller state mutation",
            "VectorOperandShape shape",
            "validateLogicalALUVector",
            "Logical ALU_VECTOR ABI decoded and validated",
            "live execution is unsupported until the ",
        ):
            self.assertIn(evidence, self.cpu_port)

        logical = self.cpu_port.index(
            "if (current_instruction->isLogicalALUScalar())", opcode
        )
        shape_validation = self.cpu_port.index(
            "validateLogicalALUScalar", logical
        )
        range_validation = self.cpu_port.index(
            "const int source_range = getAddrRegion(data)",
            shape_validation,
        )
        source_span_validation = self.cpu_port.index(
            "validateSourceSpan", range_validation
        )
        destination_span_validation = self.cpu_port.index(
            "validateDestinationSpan", source_span_validation
        )
        source_mutation = self.cpu_port.index(
            "current_instruction->logicalSourceBackingAddr = data",
            destination_span_validation,
        )
        if_admission = self.cpu_port.index(
            "my_instruction_recvs[instruction_id] = true", source_mutation
        )
        dispatch = self.cpu_port.index(
            "scheduleDispatchInstructionEvent()", if_admission
        )
        self.assertLess(shape_validation, range_validation)
        self.assertLess(range_validation, source_span_validation)
        self.assertLess(source_span_validation, destination_span_validation)
        self.assertLess(destination_span_validation, source_mutation)
        self.assertLess(source_mutation, if_admission)
        self.assertLess(if_admission, dispatch)
        validation_branch = self.cpu_port[shape_validation:source_mutation]
        for mutation in (
            "my_instruction_recvs[instruction_id] = true",
            "scheduleDispatchInstructionEvent()",
            "ifile->",
            "spd->",
        ):
            self.assertNotIn(mutation, validation_branch)

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
            "ScalarRegisterOutOfRange",
            "ExtraRegisterOperand",
            "Conditional",
            "UnexpectedBaseAddress",
            "MissingSourceBacking",
            "NullSourceBacking",
            "MissingDestinationBacking",
            "NullDestinationBacking",
            "MisalignedDestinationBacking",
            "UnregisteredDestinationRange",
            "DestinationOutsideRange",
            "IncompleteDestinationSpan",
        ):
            self.assertIn(validation, self.abi)
        self.assertNotIn("std::vector", self.abi)
        self.assertNotIn("new ", self.abi)

    def test_vector_shape_requires_two_logical_sources_and_fail_closed_gate(
        self,
    ) -> None:
        for evidence in (
            "ALUVectorOpcode = 9",
            "encodeLogicalALUVectorHeader",
            "VectorValidation",
            "AliasedLogicalDestination",
            "RepeatedLogicalSourceHasDifferentBacking",
            "backingSpansOverlap",
            "logicalSource2BackingAddr",
            "logicalDestinationBackingAddr",
            "Rejected logical ALU_VECTOR overlapping backing",
        ):
            self.assertIn(
                evidence, self.abi if evidence in self.abi else self.cpu_port
            )
        vector = self.cpu_port.index(
            "if (current_instruction->isLogicalALUVector())"
        )
        validation = self.cpu_port.index("validateLogicalALUVector", vector)
        unsupported = self.cpu_port.index(
            "live execution is unsupported", validation
        )
        self.assertLess(vector, validation)
        self.assertLess(validation, unsupported)
        self.assertNotIn(
            "my_instruction_recvs[instruction_id] = true",
            self.cpu_port[validation:unsupported],
        )

    def test_api_emits_ordinary_vector_opcode_with_three_backing_words(
        self,
    ) -> None:
        begin = self.api.index("inline void maa_alu_vector_logical")
        end = self.api.index("template <class T1>", begin + 1)
        helper = self.api[begin:end]
        for evidence in (
            "encodeLogicalALUVectorHeader",
            "*INSTR_baseaddr = NA_UINT64",
            "*INSTR_backingaddr = (uint64_t)destination_backing",
            "*INSTR_indexaddr = (uint64_t)source1_backing",
            "*INSTR_predicateaddr = (uint64_t)source2_backing",
        ):
            self.assertIn(evidence, helper)

    def test_api_emits_ordinary_scalar_opcode_without_logical_wait_alias(
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
            "*INSTR_indexaddr = (uint64_t)source_backing",
            "logical_backing_bytes",
            "scalar_reg",
        ):
            self.assertIn(evidence, helper)
        self.assertNotIn("maa_wait_logical_page", self.api)
        self.assertNotIn("maa_wait_logical_tile", self.api)
        self.assertNotIn("LOGICAL_PAGES_PER_DESCRIPTOR", self.api)
        self.assertIn("void wait_virtual_page", self.api)

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
        normalized = " ".join(note.split())
        for caveat in (
            "not connected to the cache controller",
            "not change StreamAccess or Port response semantics",
            "does not retire opcode 16",
            "No gem5 simulation",
            "no performance claim",
        ):
            self.assertIn(caveat, normalized)


if __name__ == "__main__":
    unittest.main()
