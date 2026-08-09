#!/usr/bin/env python3
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class XRAGEZeroPayloadContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        read = lambda path: (ROOT / path).read_text()
        cls.api = read("benchmarks/API/MAA_gem5.hpp")
        cls.functional = read("benchmarks/API/MAA_functional.hpp")
        cls.if_header = read("src/mem/MAA/IF.hh")
        cls.if_source = read("src/mem/MAA/IF.cc")
        cls.cpu = read("src/mem/MAA/CpuSidePort.cc")
        cls.indirect_header = read("src/mem/MAA/IndirectAccess.hh")
        cls.indirect = read("src/mem/MAA/IndirectAccess.cc")
        cls.alu_header = read("src/mem/MAA/ALU.hh")
        cls.alu = read("src/mem/MAA/ALU.cc")
        cls.invalidator = read("src/mem/MAA/Invalidator.cc")
        cls.maa = read("src/mem/MAA/MAA.cc")
        cls.contract = read("src/mem/MAA/XRAGEZeroPayload.hh")
        cls.tracker_test = read("tests/maa/multi_range_access_tracker_test.cc")
        cls.spatter = read("benchmarks/spatter/src/Spatter/Configuration.cc")
        cls.smoke = read("experiments/scripts/run_xrage_direct_index_smoke.sh")
        cls.matched = read("experiments/scripts/run_xrage_matched.sh")

    def test_opcode_18_and_descriptor_source_four_are_consistent(self):
        for source in (self.api, self.if_header):
            self.assertRegex(source, r"INDIR_LD_VIRTUAL_INDEX_SCALAR\s*=\s*18")
        self.assertIn("src4RegID", self.if_header)
        body = self.api[
            self.api.index(
                "maa_indirect_load_virtual_index_scalar"
            ) : self.api.index(
                "maa_indirect_load_virtual_index(",
                self.api.index("maa_indirect_load_virtual_index_scalar"),
            )
        ]
        self.assertIn("((uint64_t)scalar_reg << 40)", body)
        self.assertIn("((uint64_t)NA_UINT8 << 56)", body)
        self.assertIn("((uint64_t)NA_UINT8 << 48)", body)
        self.assertIn("*INSTR_baseaddr", body)
        self.assertIn("*INSTR_backingaddr", body)
        self.assertIn("*INSTR_indexaddr", body)
        remap = self.cpu[
            self.cpu.index(
                "Opcode 18 reuses the raw rdst1 byte"
            ) : self.cpu.index("break;", self.cpu.index("Opcode 18 reuses"))
        ]
        self.assertIn("src4RegID", remap)
        self.assertIn("dst1RegID = -1", remap)

    def test_no_index_or_result_spd_payload_is_named(self):
        body = self.api[
            self.api.index(
                "maa_indirect_load_virtual_index_scalar"
            ) : self.api.index(
                "maa_indirect_load_virtual_index(",
                self.api.index("maa_indirect_load_virtual_index_scalar"),
            )
        ]
        self.assertNotRegex(body, r"\bidx_tile\b")
        self.assertNotRegex(body, r"\bresult_tile\b")
        self.assertGreaterEqual(body.count("NA_UINT8"), 5)
        legality = self.if_source[
            self.if_source.index(
                "if (zero_payload_scalar)"
            ) : self.if_source.index(
                "} else {", self.if_source.index("if (zero_payload_scalar)")
            )
        ]
        self.assertIn("src1SpdID != -1", legality)
        self.assertIn("src2SpdID != -1", legality)
        self.assertIn("condSpdID != -1", legality)
        self.assertIn("dst2SpdID != -1", legality)
        self.assertIn("dst1RegID != -1", legality)
        self.assertIn("spdPayload = 0", self.contract)

    def test_direct_index_and_fused_predicates_select_one_terminal_path(self):
        predicates = self.indirect[
            self.indirect.index(
                "bool IndirectAccessUnit::isVirtualLoad"
            ) : self.indirect.index(
                "bool IndirectAccessUnit::usesBoundedSourceResponses"
            )
        ]
        self.assertGreaterEqual(
            predicates.count("INDIR_LD_VIRTUAL_INDEX_SCALAR"), 3
        )
        self.assertIn("isZeroPayloadXRAGE()", predicates)
        self.assertIn("isFusedDirectTransform", predicates)
        self.assertIn("isDirectIndexLoad", predicates)
        self.assertIn("my_instruction->src4RegID", self.indirect)
        self.assertIn(
            "my_fused_scalar = maa->rf->getData<double>", self.indirect
        )
        self.assertIn("fillDirectIndexWindow", self.indirect)
        self.assertIn("startDirectTransform", self.indirect)
        self.assertIn("insertVirtualCombineWord", self.indirect)

    def test_strict_4k_contract_is_opcode_local_and_fails_closed(self):
        for token in (
            "MaxLogicalEntries = 4096",
            "MaxIndexLines",
            "InvalidOffsetCapacity",
            "InvalidRowCapacity",
            "InvalidResponseCapacity",
            "InvalidCombinerCapacity",
            "InvalidWriteCapacity",
            "InvalidALUCapacity",
            "GenericRangePassEnabled",
        ):
            self.assertIn(token, self.contract)
        decode = self.indirect[
            self.indirect.index(
                "if (isZeroPayloadXRAGE())"
            ) : self.indirect.index(
                "my_idx_tile_ready = true",
                self.indirect.index("if (isZeroPayloadXRAGE())"),
            )
        ]
        self.assertIn("XRAGEZeroPayloadContract::validate", decode)
        self.assertRegex(decode, r"panic_if\s*\(\s*validation\s*!=")
        self.assertIn("virtual_index_range_passes", decode)
        self.assertIn("direct_index_partitions", decode)
        self.assertNotIn("bounded_range_pass.begin", decode)

    def test_exact_alias_contract_and_a_b_read_sharing(self):
        legality = self.if_source[
            self.if_source.index(
                "const bool zero_payload_scalar"
            ) : self.if_source.index("switch (_instruction.opcode)", 350)
        ]
        self.assertIn("source_destination_overlap", legality)
        self.assertIn("addrRangeID ==", legality)
        decode = self.indirect[
            self.indirect.index(
                "const uint64_t first_index"
            ) : self.indirect.index(
                "const auto storage",
                self.indirect.index("const uint64_t first_index"),
            )
        ]
        self.assertLess(
            self.indirect.index(
                "my_index_min_addr = my_instruction->indexMinAddr"
            ),
            self.indirect.index("const uint64_t first_index"),
        )
        self.assertIn("consumed B span", decode)
        self.assertIn("spanOverlaps", decode)
        self.assertIn("forbids consumed B/C", decode)
        self.assertIn('"overlap\\n"', decode)
        self.assertIn(
            "A/B may be the same immutable region", self.tracker_test
        )
        self.assertIn("duplicate reads collapse", self.tracker_test)

    def test_global_and_same_maa_hazards_cover_a_b_c(self):
        accesses = self.invalidator[
            self.invalidator.index(
                "Invalidator::instructionAccesses"
            ) : self.invalidator.index("Invalidator::getAddrRegionPermit")
        ]
        self.assertIn("addrRangeID, Mode::Read", accesses)
        self.assertIn("indexAddrRangeID, Mode::Read", accesses)
        self.assertIn("backingAddrRangeID, Mode::Write", accesses)
        self.assertIn("regionAccessTracker.tryAcquire", self.invalidator)
        self.assertIn("regionAccessTracker.release", self.invalidator)
        hazard = self.if_source[
            self.if_source.index(
                "const bool other_fused_direct_scalar"
            ) : self.if_source.index(
                "} else if (_instruction.addrRangeID",
                self.if_source.index("const bool other_fused_direct_scalar"),
            )
        ]
        self.assertIn("indexAddrRangeID", hazard)
        self.assertIn("backingAddrRangeID", hazard)
        self.assertIn("memory_hazard", hazard)

    def test_all_payload_and_metadata_queues_have_explicit_bounds(self):
        self.assertIn("DirectIndexPendingLine", self.indirect_header)
        self.assertIn("IndexWordsPerLine", self.indirect_header)
        self.assertNotIn(
            "std::map<Addr, std::vector<std::pair<int, uint16_t>>>",
            self.indirect_header,
        )
        self.assertIn("packed_word_capacity", self.indirect_header)
        self.assertIn(
            "std::unique_ptr<std::array<uint8_t, 8>[]>", self.indirect_header
        )
        self.assertIn("preallocated packed words", self.indirect)
        self.assertIn(
            "std::unique_ptr<DirectTransformEntry[]>", self.alu_header
        )
        self.assertIn("direct_transform_count", self.alu_header)
        self.assertIn("MaxALULanes", self.indirect)
        self.assertIn("fused_result_transfer_words_per_cycle", self.indirect)
        self.assertIn("fused_result_transfer_banks", self.indirect)
        self.assertIn("virtual_combine_words_limit", self.indirect)
        self.assertIn("virtual_max_outstanding_writes_limit", self.indirect)

    def test_completion_is_token_only_and_after_write_ack_closure(self):
        span = self.if_source[
            self.if_source.index(
                "int Instruction::getTileSpanWordSize"
            ) : self.if_source.index("int Instruction::WordSize")
        ]
        self.assertIn("INDIR_LD_VIRTUAL_INDEX_SCALAR", span)
        self.assertIn("return sizeof(uint32_t)", span)
        closure = self.indirect[
            self.indirect.index(
                "bool IndirectAccessUnit::boundedRetirementComplete"
            ) : self.indirect.index("classifyVirtualRequestReason")
        ]
        self.assertIn("fusedDirectTransformPending", closure)
        self.assertIn("virtualCombinerEmpty", closure)
        self.assertIn("virtual_outstanding_writes == 0", closure)
        self.assertIn("completeVirtualRetirementWrite", self.indirect)
        self.assertIn("attribution_write_completions++", self.indirect)

    def test_live_drain_and_stats_reset_recognize_opcode_18(self):
        self.assertIn("INDIR_LD_VIRTUAL_INDEX_SCALAR", self.maa)
        reset = self.maa[
            self.maa.index("void MAA::resetStats()") : self.maa.index(
                "#define MAKE_INDIRECT_STAT_NAME"
            )
        ]
        self.assertIn("INDIR_LD_VIRTUAL_INDEX_SCALAR", reset)
        self.assertIn("hasFusedDirectInstruction", reset)
        self.assertIn(
            "INDIR_LD_VIRTUAL_INDEX_SCALAR",
            self.if_source[
                self.if_source.index("bool IF::hasFusedDirectInstruction") :
            ],
        )
        drain = self.maa[
            self.maa.index("MAA::drain()") : self.maa.index(
                "MAA::drainResume()"
            )
        ]
        for token in (
            "allFuncUnitsIdle",
            "ifile->empty",
            "hasLiveRegionAccesses",
        ):
            self.assertIn(token, drain)

    def test_byte_ledger_and_removed_spd_traffic_are_executable_contracts(
        self,
    ):
        for token in (
            "metadataTotal",
            "internalPayloadTotal",
            "responseAllocatedWords",
            "spdPayload = 0",
            "indexSPDRemoved",
            "resultSPDRemoved",
            "nativeX3Control",
            "zeroPayloadX3Control",
        ):
            self.assertIn(token, self.contract)
        trace = self.indirect[
            self.indirect.index("event=xrage_zero_payload_begin")
            - 200 : self.indirect.index("event=xrage_zero_payload_begin")
            + 1000
        ]
        for token in (
            "metadata_bytes",
            "internal_payload_bytes",
            "spd_payload_bytes",
            "removed_index_spd_bytes",
            "removed_result_spd_bytes",
        ):
            self.assertIn(token, trace)
        accounting_test = (
            ROOT / "tests/maa/xrage_zero_payload_accounting_test.cc"
        ).read_text()
        self.assertIn("removed_spd_bytes_20k", accounting_test)
        self.assertIn("800000", accounting_test)

    def test_functional_reference_has_the_same_strict_shape(self):
        body = self.functional[
            self.functional.index(
                "maa_indirect_load_virtual_index_scalar"
            ) : self.functional.index(
                "maa_indirect_load_virtual_index(",
                self.functional.index(
                    "maa_indirect_load_virtual_index_scalar"
                ),
            )
        ]
        self.assertIn("logical <= 4096", body)
        self.assertIn("op == Operation_t::MUL_OP", body)
        self.assertIn("source_region != backing_region", body)
        self.assertIn("a_region_last <= c_region_first", body)
        self.assertIn("b_last <= c_first || c_last <= b_first", body)
        self.assertIn("data[indices[src]] * scalar", body)

    def test_exact_matched_x3_arms_and_fail_closed_stats_gate(self):
        self.assertIn('maa_arm == "zeropayload4x3"', self.spatter)
        self.assertIn(
            "maa_indirect_load_virtual_index_scalar<double>", self.spatter
        )
        self.assertIn("native16x3", self.matched)
        self.assertIn("zeropayload4x3", self.matched)
        self.assertIn("parallel_arms=1", self.matched)
        self.assertEqual(self.matched.count("XRAGE_GUEST_DATA_SEED=1"), 2)
        self.assertIn('native_hash == "$zero_hash"', self.matched)
        for token in (
            "$index_words -eq $verified_length",
            "$fused_alu_words -eq $verified_length",
            "$fused_result_words -eq $verified_length",
            "$indirect_spd_reads -eq 0",
            "$indirect_spd_writes -eq 0",
            "$write_issues -eq $write_completions",
        ):
            self.assertIn(token, self.smoke)


if __name__ == "__main__":
    unittest.main()
