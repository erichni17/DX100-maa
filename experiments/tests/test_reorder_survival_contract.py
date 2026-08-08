import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ReorderSurvivalContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sconscript = (ROOT / "src/mem/MAA/SConscript").read_text()
        cls.header = (
            ROOT / "src/mem/MAA/ReorderSurvivalTracker.hh"
        ).read_text()
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.analyzer = (
            ROOT / "experiments/analysis/analyze_reorder_survival.py"
        ).read_text()

    def test_opt_in_flag_is_not_in_default_compound_trace(self):
        self.assertIn("DebugFlag('MAAReorderTrace')", self.sconscript)
        compound = re.search(
            r"CompoundFlag\('MAAAll'.*?\]\)", self.sconscript, re.DOTALL
        )
        self.assertIsNotNone(compound)
        self.assertNotIn("MAAReorderTrace", compound.group(0))

    def test_disabled_path_is_gated_and_has_no_timing_actions(self):
        self.assertGreaterEqual(
            self.indirect.count("debug::MAAReorderTrace"), 6
        )
        helper = re.search(
            r"IndirectAccessUnit::recordReorderSurvivalIssue.*?\n}",
            self.indirect,
            re.DOTALL,
        )
        self.assertIsNotNone(helper)
        self.assertNotIn("schedule", helper.group(0))
        self.assertNotIn("updateLatency", helper.group(0))
        self.assertNotIn("Cycles(", helper.group(0))

    def test_state_is_constant_size_and_trace_is_per_epoch(self):
        for forbidden in ("std::vector", "std::map", "new ", "push_back"):
            self.assertNotIn(forbidden, self.header)
        self.assertEqual(self.indirect.count("event=reorder_epoch"), 1)
        self.assertEqual(self.indirect.count("event=reorder_summary"), 1)
        self.assertNotIn("event=reorder_admission", self.indirect)
        self.assertNotIn("event=reorder_issue", self.indirect)

    def test_all_required_counters_and_identity_are_emitted(self):
        for token in (
            "instruction_id=%lu",
            "operation_tick=%lu",
            "epoch_id=%lu",
            "admissions=%lu",
            "max_joint_admissions=%lu",
            "rt_full_drains=%lu",
            "issued_lines=%lu",
            "issued_entries=%lu",
            "row_transitions=%lu",
            "total_admitted=%lu",
            "total_issued_entries=%lu",
            "reconciled=1",
            "classification=%s",
        ):
            self.assertIn(token, self.indirect)

    def test_issue_lines_cannot_credit_response_entries(self):
        self.assertIn("issueLine(uint64_t row_key)", self.header)
        self.assertNotIn("issueLine(uint64_t row_key,", self.header)
        self.assertIn("totalSelectedDescriptors", self.header)
        self.assertIn("selected/admitted descriptors", self.indirect)
        self.assertIn("RT-full drains %lu != pressure events", self.indirect)

    def test_analyzer_fails_closed_and_states_claim_boundary(self):
        for token in (
            "no reorder-survival records",
            "epoch/summary instruction sets differ",
            "admitted/issued mismatch",
            "exactly 16384 admitted",
            "mid-instruction drains",
            "inherited/partitioned",
        ):
            self.assertIn(token, self.analyzer)


if __name__ == "__main__":
    unittest.main()
