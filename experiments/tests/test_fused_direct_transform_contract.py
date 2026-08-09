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
        cls.invalidator = (ROOT / "src/mem/MAA/Invalidator.cc").read_text()
        cls.maa_header = (ROOT / "src/mem/MAA/MAA.hh").read_text()
        cls.maa_source = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        cls.maa_params = (ROOT / "src/mem/MAA/MAA.py").read_text()
        cls.tracker_test = (
            ROOT / "tests/maa/multi_range_access_tracker_test.cc"
        ).read_text()
        cls.spatter = (
            ROOT / "benchmarks/spatter/src/Spatter/Configuration.cc"
        ).read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_xrage_direct_index_smoke.sh"
        ).read_text()
        cls.correctness_runner = (
            ROOT
            / "experiments/scripts/run_fused_direct_transform_correctness.sh"
        ).read_text()
        cls.validator = (
            ROOT / "experiments/scripts/validate_virtual_gather.sh"
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
        self.assertIn("*INSTR_indexaddr", body)
        self.assertRegex(body, r"uint32_t\s*\*indices")
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
        self.assertIn("indexAddrRangeID", legality)
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
        self.assertIn("scheduleExecuteInstructionEvent(1)", completion)
        self.assertIn("IND_FusedALUResultHighWater", self.indirect)

    def test_global_compound_address_lease_covers_a_b_and_c(self):
        accesses = self.invalidator[
            self.invalidator.index(
                "Invalidator::instructionAccesses"
            ) : self.invalidator.index("Invalidator::getAddrRegionPermit")
        ]
        self.assertIn("instruction->addrRangeID, Mode::Read", accesses)
        self.assertIn("instruction->indexAddrRangeID, Mode::Read", accesses)
        self.assertIn("instruction->backingAddrRangeID, Mode::Write", accesses)
        self.assertIn("regionAccessTracker.tryAcquire", self.invalidator)
        self.assertIn("finishSingleAddrRegion", self.invalidator)
        self.assertIn("regionAccessTracker.release", self.invalidator)
        self.assertIn("Cross-MAA C read/write", self.tracker_test)
        self.assertIn("wholly disjoint write", self.tracker_test)
        self.assertIn("B input retains ordering", self.tracker_test)

    def test_result_handoff_has_explicit_delay_width_banks_and_counters(self):
        self.assertIn("scheduleExecuteInstructionEvent(1)", self.alu_source)
        for token in (
            "fused_result_transfer_words_per_cycle",
            "fused_result_transfer_banks",
        ):
            self.assertIn(token, self.maa_params)
            self.assertIn(token, self.maa_header)
            self.assertIn(token, self.indirect)
        for counter in (
            "IND_FusedResultTransferWords",
            "IND_FusedResultTransferCycles",
            "IND_FusedResultTransferStallCycles",
            "IND_FusedResultTransferWidthStallCycles",
            "IND_FusedResultTransferBankStallCycles",
            "IND_FusedResultTransferBackpressureStallCycles",
        ):
            self.assertIn(counter, self.maa_header)
            self.assertIn(counter, self.maa_source)
            self.assertIn(counter, self.indirect)

    def test_fused_completion_is_one_32_bit_token_without_tile3(self):
        self.assertIn("int Instruction::getTileSpanWordSize", self.if_source)
        span = self.if_source[
            self.if_source.index(
                "int Instruction::getTileSpanWordSize"
            ) : self.if_source.index("int Instruction::WordSize")
        ]
        self.assertIn("INDIR_LD_VIRTUAL_SCALAR", span)
        self.assertIn("tile_id == dst1SpdID", span)
        self.assertIn("return sizeof(uint32_t)", span)
        payload = self.if_source[
            self.if_source.index(
                "int Instruction::getWordSize"
            ) : self.if_source.index("int Instruction::getTileSpanWordSize")
        ]
        self.assertRegex(
            payload,
            r"INDIR_LD_VIRTUAL_SCALAR:[\s\S]*?return WordSize\(\)",
        )
        self.assertIn("getTileSpanWordSize", self.maa_source)
        self.assertIn(
            'tile2s[tid] = maa_arm == "fuseddirect16x3"', self.spatter
        )
        self.assertIn('tile3s[tid] = maa_arm == "native16x3"', self.spatter)

    def test_live_drain_waits_and_mid_operation_reset_fails_closed(self):
        live = self.maa_source[
            self.maa_source.index(
                "MAA::hasLiveState()"
            ) : self.maa_source.index("MAA::hasLiveFusedDirectState()")
        ]
        self.assertIn("allFuncUnitsIdle", live)
        self.assertIn("ifile->empty", live)
        self.assertIn("hasLiveRegionAccesses", live)
        self.assertIn("queued_callbacks", live)
        self.assertIn("outstanding_packets", live)
        drain = self.maa_source[
            self.maa_source.index("MAA::drain()") : self.maa_source.index(
                "MAA::drainResume()"
            )
        ]
        self.assertIn("hasLiveState", drain)
        self.assertIn("scheduleDrainEvent", drain)
        self.assertIn("DrainState::Draining", drain)
        self.assertNotIn("drainQuiescenceObserved", self.maa_header)
        service = self.maa_source[
            self.maa_source.index(
                "MAA::serviceDrain()"
            ) : self.maa_source.index("MAA::drain()")
        ]
        self.assertIn("drainState() != DrainState::Draining", service)
        self.assertIn("hasLiveState", service)
        self.assertIn("signalDrainDone", service)
        fused_live = self.maa_source[
            self.maa_source.index(
                "MAA::hasLiveFusedDirectState()"
            ) : self.maa_source.index("MAA::scheduleDrainEvent()")
        ]
        self.assertIn("hasFusedDirectInstruction", fused_live)
        self.assertIn("fusedDirectTransformLive", fused_live)
        reset = self.maa_source[
            self.maa_source.index(
                "void MAA::resetStats()"
            ) : self.maa_source.index("#define MAKE_INDIRECT_STAT_NAME")
        ]
        self.assertIn("hasLiveFusedDirectState", reset)
        self.assertIn("partial-operation accounting is unsupported", reset)
        self.assertIn(
            "FUSED_DIRECT_LIVE_DRAIN_RETURNED", self.correctness_runner
        )
        guest = (
            ROOT / "benchmarks/API/test_fused_direct_transform.cpp"
        ).read_text()
        self.assertIn("waitForPartialOutput(output, mode)", guest)
        self.assertNotIn(
            "checkpoint/drain requested with live instruction",
            self.correctness_runner,
        )

    def test_two_maa_live_hazard_gate_is_enabled(self):
        self.assertIn("MAA_NUM_MAAS=2", self.correctness_runner)
        self.assertIn("4097 multimaa", self.correctness_runner)
        self.assertIn(
            "fused_direct_global_lease_conflict_deferrals",
            self.correctness_runner,
        )
        self.assertIn(
            "fused_direct_global_lease_high_water", self.correctness_runner
        )
        self.assertIn("num_maas=${MAA_NUM_MAAS:-1}", self.validator)
        self.assertIn('--maa_num_maas="$num_maas"', self.validator)

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
