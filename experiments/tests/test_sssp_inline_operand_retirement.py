import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class InlineOperandRetirementContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "benchmarks/gapbs/src/sssp.cc").read_text()
        cls.api = (ROOT / "benchmarks/API/MAA_gem5.hpp").read_text()
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.tables = (ROOT / "src/mem/MAA/Tables.cc").read_text()
        cls.runner = (
            ROOT
            / "experiments/scripts/run_sssp_inline_retirement_candidate.sh"
        ).read_text()

    def test_compile_time_arm_preserves_old_result_control(self):
        self.assertIn("SSSP_INLINE_OPERAND_RETIREMENT", self.source)
        self.assertIn("SSSP_OLD_RESULT_HYBRID_TERMINAL", self.source)
        self.assertIn("SSSP_INLINE_OPERAND_RETIREMENT_TERMINAL", self.source)

    def test_paired_pages_and_no_completion_tile(self):
        self.assertIn("encodeAdmitPair", self.api)
        self.assertIn("(uint64_t)NA_UINT8;", self.api)
        self.assertIn("getData<uint32_t>(value_tile, lane)", self.indirect)
        self.assertIn(
            "insertPageFedSoaJitIndex(index, ordinal, operand, true)",
            self.indirect,
        )

    def test_aux_lifetime_and_mutual_exclusion(self):
        self.assertIn("beginInlineOperandMode", self.tables)
        self.assertIn(
            "summary conflicts with inline aux ownership", self.tables
        )
        self.assertIn("entries[i].pass = -1", self.tables)
        self.assertIn("endInlineOperandMode", self.indirect)

    def test_write_response_gates_dense_retirement(self):
        response = self.indirect.index("soa_jit_a_write_response")
        issue = self.indirect.index("issueInlineRetirementWrites(context)")
        self.assertLess(response, issue)
        self.assertIn("markWriteResponse", self.indirect)
        self.assertIn("ackInlineRetirementLine", self.indirect)

    def test_candidate_only_frozen_acceptance(self):
        self.assertNotIn("native4 checkpoint", self.runner)
        self.assertNotIn("native16 checkpoint", self.runner)
        for token in (
            "902d3b2dfceddc44a354ce2f7a9a3d572327c2c2fc7ff99190baff74d059c3e3",
            "candidate_only=true",
            "native_control_reruns=0",
            "full_s22_runs=0",
            "ticks -le 840612362",
            "retirement_records=65536",
            "retirement_acked_lines=8192",
        ):
            self.assertIn(token, self.runner)


if __name__ == "__main__":
    unittest.main()
