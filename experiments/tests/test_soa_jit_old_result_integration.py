import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class SoaJitOldResultIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = (ROOT / "benchmarks/API/MAA_gem5.hpp").read_text()
        cls.cpu = (ROOT / "src/mem/MAA/CpuSidePort.cc").read_text()
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.header = (ROOT / "src/mem/MAA/IndirectAccess.hh").read_text()
        cls.maa_header = (ROOT / "src/mem/MAA/MAA.hh").read_text()
        cls.maa_source = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        cls.maa_params = (ROOT / "src/mem/MAA/MAA.py").read_text()
        cls.options = (ROOT / "configs/common/Options.py").read_text()
        cls.maa_config = (ROOT / "configs/common/MAAConfig.py").read_text()
        cls.buffer = (
            ROOT / "src/mem/MAA/SoaJitOldResultBuffer.hh"
        ).read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_hybrid_rmw_old_result_smoke.sh"
        ).read_text()

    def test_wire_form_is_opt_in_and_seven_words(self):
        self.assertIn("MAA_SOA_JIT_OLD_RESULT_MODE_TAG", self.api)
        self.assertIn("*INSTR_resultaddr = (uint64_t)old_values", self.api)
        self.assertIn("case 6:", self.cpu)
        self.assertIn("hasSoaJitOldResult()", self.cpu)

    def test_capture_precedes_ordered_update(self):
        capture = self.indirect.index("soa_jit_old_result_buffer.capture(")
        scalar_apply = self.indirect.index(
            "soa_jit_scalar_broadcast.apply(destination)", capture
        )
        vector_apply = self.indirect.index("lhs += rhs", capture)
        self.assertLess(capture, scalar_apply)
        self.assertLess(capture, vector_apply)
        self.assertIn("slot->logicalItr", self.indirect)

    def test_publication_is_bounded_and_response_bearing(self):
        self.assertIn("static constexpr size_t Credits = 8", self.buffer)
        self.assertNotIn("std::vector", self.buffer)
        self.assertIn("issueForPressure", self.buffer)
        self.assertIn("partialAwaitingResponses()", self.buffer)
        self.assertIn(
            "partialAwaitingResponses() >= partialCredits", self.buffer
        )
        self.assertIn("validWordCount(candidate.validWords)", self.buffer)
        self.assertIn("words == chosenWords", self.buffer)
        self.assertIn("PressurePolicy::OriginalOldest", self.buffer)
        self.assertIn("PressurePolicy::Densest", self.buffer)
        self.assertIn("sizeof(SoaJitOldResultBuffer) == 1128", self.buffer)
        self.assertIn("SoaJitOldResultWriteMode::Pressure", self.indirect)
        self.assertIn("SoaJitOldResultWriteMode::Drain", self.indirect)
        self.assertIn("SoaJitOldResultSenderState", self.header)
        self.assertIn("MemCmd::WriteReq", self.indirect)
        self.assertIn("req->setByteEnable(byte_enable)", self.indirect)
        self.assertIn("completeSoaJitOldResultWrite(identity)", self.indirect)
        self.assertIn(
            "bypass_deferred_queue at its default false", self.indirect
        )

    def test_pressure_policy_and_credits_are_bounded_runtime_controls(self):
        self.assertIn(
            "soa_jit_old_result_partial_credits = Param.Unsigned(\n        8,",
            self.maa_params,
        )
        self.assertIn(
            "soa_jit_old_result_pressure_policy = Param.String(\n"
            '        "original_oldest",',
            self.maa_params,
        )
        self.assertIn(
            '"--maa_soa_jit_old_result_partial_credits"', self.options
        )
        self.assertIn("choices=(1, 2, 4, 8)", self.options)
        self.assertIn(
            '"--maa_soa_jit_old_result_pressure_policy"', self.options
        )
        self.assertIn('choices=("original_oldest", "densest")', self.options)
        self.assertIn(
            'opts["soa_jit_old_result_partial_credits"]', self.maa_config
        )
        self.assertIn(
            'opts["soa_jit_old_result_pressure_policy"]', self.maa_config
        )
        self.assertIn("soa_jit_old_result_partial_credits", self.maa_header)
        self.assertIn("soa_jit_old_result_dense_pressure", self.maa_header)
        self.assertIn("PressureControlBits", self.indirect)
        self.assertIn("PressureControlBits =", self.buffer)
        self.assertIn("DenseScanSlots = Credits", self.buffer)
        self.assertIn("popcount_scan_slots=%u", self.indirect)
        self.assertIn("IND_SoaJitOldResultPressureIssues", self.maa_source)
        self.assertIn(
            "IND_SoaJitOldResultPartialCreditHighWater", self.maa_source
        )

    def test_receive_staging_and_aliases_fail_closed(self):
        self.assertIn("soaJitPredicateWordReceived", self.cpu)
        self.assertIn("soaJitResultWordReceived", self.cpu)
        self.assertIn("must not alias an input or target", self.cpu)
        guest = (
            ROOT / "benchmarks/API/test_hybrid_rmw_old_result.cpp"
        ).read_text()
        self.assertIn("RejectedSentinelBits", guest)
        self.assertIn(
            "bits(oldActual[logical]) != RejectedSentinelBits", guest
        )

    def test_candidate_smoke_forces_row_pressure_with_exact_predicates(self):
        guest = (
            ROOT / "benchmarks/API/test_hybrid_rmw_old_result.cpp"
        ).read_text()
        self.assertIn("constexpr int TargetRows = 128", guest)
        self.assertIn("constexpr int RowStrideWords = 65536", guest)
        self.assertIn(
            "indices[logical] = (logical % TargetRows) * RowStrideWords", guest
        )
        self.assertIn("predicates[logical] = 1", guest)
        self.assertIn("result_hash=", guest)
        self.assertIn("ExpectedResultHash = 16970917775049394563ULL", guest)
        self.assertIn("candidate_only=1", self.runner)
        self.assertIn("row_stride_bytes=262144", self.runner)
        self.assertIn("$selected -eq 32768 && $rejected -eq 0", self.runner)
        self.assertIn(
            "$predicate_hits -eq 32768 && $predicate_uses -eq 32768",
            self.runner,
        )
        self.assertIn("$epoch_drains -gt 0", self.runner)
        self.assertIn("expected_result_hash=16970917775049394563", self.runner)

    def test_terminal_requires_exact_result_closure(self):
        self.assertIn(
            "soa_jit_old_result_write_issues !=\n"
            "                             soa_jit_old_result_write_responses",
            self.indirect,
        )
        self.assertIn("old_result_write_issues=", self.runner)
        self.assertIn("native_arms=0", self.runner)
        self.assertIn("wall_timeout=none", self.runner)

    def test_smoke_runner_uses_full_indirect_geometry(self):
        self.assertIn("--maa_num_indirect_units_per_maa=4", self.runner)
        self.assertNotIn("--maa_num_indirect_units_per_maa=1", self.runner)
        self.assertIn("num_indirect_units_per_maa=4", self.runner)
        self.assertIn("row_table_slices=32", self.runner)
        self.assertIn("--maa_num_initial_row_table_slices=32", self.runner)
        self.assertIn("--mem-channels=2", self.runner)
        self.assertIn("memory_channels=2", self.runner)
        self.assertIn("system\\.mem_ctrls[01]", self.runner)
        self.assertIn("grep -Fxc 'num_indirect_units_per_maa=4'", self.runner)
        self.assertIn(
            "grep -Fxc 'num_initial_row_table_slices=32'", self.runner
        )


if __name__ == "__main__":
    unittest.main()
