import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class FusedDirectTransformContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = (ROOT / "benchmarks/API/MAA_gem5.hpp").read_text()
        cls.if_header = (ROOT / "src/mem/MAA/IF.hh").read_text()
        cls.if_source = (ROOT / "src/mem/MAA/IF.cc").read_text()
        cls.cpu_port = (ROOT / "src/mem/MAA/CpuSidePort.cc").read_text()
        cls.alu_header = (ROOT / "src/mem/MAA/ALU.hh").read_text()
        cls.alu_source = (ROOT / "src/mem/MAA/ALU.cc").read_text()
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.spatter = (
            ROOT / "benchmarks/spatter/src/Spatter/Configuration.cc"
        ).read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_xrage_direct_index_smoke.sh"
        ).read_text()

    def test_opcode_and_api_encoding_match(self):
        for source in (self.api, self.if_header):
            self.assertRegex(source, r"INDIR_LD_VIRTUAL_SCALAR\s*=\s*17")
        body = self.api[
            self.api.index(
                "maa_indirect_load_virtual_scalar"
            ) : self.api.index(
                "maa_indirect_load_virtual_index",
                self.api.index("maa_indirect_load_virtual_scalar"),
            )
        ]
        self.assertIn("OpcodeType::INDIR_LD_VIRTUAL_SCALAR", body)
        self.assertIn("((uint64_t)scalar_reg << 24)", body)
        self.assertIn("*INSTR_backingaddr", body)
        self.assertNotIn("maa_alu_scalar", body)

    def test_decoder_waits_for_backing_and_marks_completion_only(self):
        self.assertGreaterEqual(
            self.cpu_port.count("INDIR_LD_VIRTUAL_SCALAR"), 3
        )
        self.assertIn("INDIR_LD_VIRTUAL_SCALAR", self.if_source)
        completion = self.if_source[
            self.if_source.index("completion_only_tiles") :
        ]
        self.assertIn("INDIR_LD_VIRTUAL_SCALAR", completion)

    def test_legality_is_fail_closed(self):
        legality = self.if_source[
            self.if_source.index(
                "const bool fused_direct_scalar"
            ) : self.if_source.index("switch (_instruction.opcode)", 350)
        ]
        self.assertIn("DataType::FLOAT64_TYPE", legality)
        self.assertIn("OPType::MUL_OP", legality)
        self.assertIn("condSpdID != -1", legality)
        self.assertIn("addrRangeID ==", legality)
        self.assertIn("backingAddrRangeID", legality)
        self.assertIn("source_destination_overlap", legality)
        self.assertIn("minAddr < _instruction.backingMaxAddr", legality)
        self.assertIn("src1MustBeFinished = true", legality)
        self.assertIn("fused direct memory hazard", self.if_source)

    def test_shared_alu_has_timed_finite_retained_batch(self):
        self.assertIn(
            "direct_transform_entries.reserve(num_ALU_lanes)", self.alu_source
        )
        self.assertIn("iterations.size() >", self.alu_source)
        self.assertIn("ALU_lane_latency", self.alu_source)
        self.assertIn(
            "scheduleExecuteInstructionEvent(latency)", self.alu_source
        )
        self.assertIn("claimALUForDirectTransform", self.alu_source)
        self.assertIn("direct_transform_ready = true", self.alu_source)
        consume = self.alu_source[
            self.alu_source.index(
                "consumeDirectTransformWord"
            ) : self.alu_source.index("void ALUUnit::updateLatency")
        ]
        self.assertIn("directTransformReady(owner)", consume)
        self.assertIn("releaseALUFromDirectTransform", consume)
        completion = self.alu_source[
            self.alu_source.index(
                "void ALUUnit::executeInstruction()"
            ) : self.alu_source.index(
                "switch (state)",
                self.alu_source.index("void ALUUnit::executeInstruction()"),
            )
        ]
        self.assertIn("direct_transform_ready = true", completion)
        self.assertIn("scheduleExecuteInstructionEvent(0)", completion)
        self.assertIn("IND_FusedALUResultHighWater", self.indirect)

    def test_transform_feeds_existing_combiner_and_ack_gate(self):
        drain = self.indirect.index(
            "bool IndirectAccessUnit::drainVirtualResponses()"
        )
        fused = self.indirect[
            self.indirect.index(
                "if (isFusedDirectTransform())", drain
            ) : self.indirect.index("bool bank_stalled", drain)
        ]
        self.assertIn("insertVirtualCombineWord", fused)
        self.assertIn("startDirectTransform", fused)
        self.assertNotIn("createRetirementWrite", fused)
        retirement = self.indirect[
            self.indirect.index(
                "boundedRetirementComplete"
            ) : self.indirect.index("classifyVirtualRequestReason")
        ]
        self.assertIn("fusedDirectTransformPending", retirement)
        self.assertIn("virtual_outstanding_writes == 0", retirement)
        self.assertIn("retirementWriteComplete", self.indirect)

    def test_xrage_fused_arm_has_no_host_post_transform(self):
        fused = self.spatter[
            self.spatter.index(
                'maa_arm == "fuseddirect16x3"'
            ) : self.spatter.index(
                'maa_arm == "fused16"',
                self.spatter.index('maa_arm == "fuseddirect16x3"'),
            )
        ]
        self.assertIn("maa_indirect_load_virtual_scalar<double>", fused)
        self.assertNotRegex(fused, r"dense\s*\[")
        legacy = self.spatter[
            self.spatter.index(
                'if (maa_arm == "compact16x3")'
            ) : self.spatter.index(
                "#endif", self.spatter.index('if (maa_arm == "compact16x3")')
            )
        ]
        self.assertIn("dense[k] *= 3.0", legacy)
        self.assertIn("fuseddirect16x3", self.runner)


if __name__ == "__main__":
    unittest.main()
