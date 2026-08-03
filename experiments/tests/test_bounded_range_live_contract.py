import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class BoundedRangeLiveContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tracker = (ROOT / "src/mem/MAA/BoundedRangePass.hh").read_text()
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()

    def test_candidate_is_opt_in_and_wired_end_to_end(self) -> None:
        sources = [
            ROOT / "src/mem/MAA/MAA.py",
            ROOT / "configs/common/Options.py",
            ROOT / "configs/common/MAAConfig.py",
            ROOT / "src/mem/MAA/MAA.hh",
            ROOT / "src/mem/MAA/MAA.cc",
            ROOT / "src/mem/MAA/IndirectAccess.cc",
        ]
        for source in sources:
            self.assertIn("virtual_index_range_passes", source.read_text())
        self.assertRegex(
            (ROOT / "src/mem/MAA/MAA.py").read_text(),
            r"virtual_index_range_passes\s*=\s*Param\.Bool\(\s*False,",
        )

    def test_candidate_caps_native_active_tables(self) -> None:
        self.assertIn("MaxActiveEntries = 4096", self.tracker)
        self.assertIn("offset_table->capacity()", self.indirect)
        self.assertIn("active_row_line_slots", self.indirect)
        self.assertIn("num_RT_slice_columns[initial_RT_config]", self.indirect)
        self.assertIn("Bounded range passes allow at most 4096", self.maa)

    def test_range_selection_is_contiguous_not_modulo(self) -> None:
        helper = re.search(
            r"uint32_t IndirectAccessUnit::directIndexPassForGrow.*?\n}",
            self.indirect,
            re.DOTALL,
        )
        self.assertIsNotNone(helper)
        self.assertIn("bounded_range_pass.passForGrow", helper.group(0))
        self.assertIn("grow_addr % direct_index_partitions", helper.group(0))
        self.assertIn(
            "directIndexPassForGrow(grow_addr)",
            self.indirect,
        )
        self.assertIn("ceilDiv", self.tracker)

    def test_no_descriptor_payload_is_hidden_in_tracker(self) -> None:
        for forbidden in (
            "line_paddr",
            "source_addr",
            "index_value",
            "result_payload",
        ):
            self.assertNotIn(forbidden, self.tracker)
        self.assertIn("std::vector<uint64_t> admitted", self.tracker)
        self.assertIn("std::vector<uint64_t> retired", self.tracker)
        self.assertIn("chargedBytes", self.tracker)

    def test_rescans_are_llc_visible_and_finite_rate(self) -> None:
        self.assertIn(
            "Bounded range passes require LLC-visible index rescans",
            self.maa,
        )
        self.assertIn(
            "Bounded range passes require a finite index-filter rate",
            self.maa,
        )
        self.assertIn("--maa_virtual_index_force_cache", self.runner)
        self.assertIn("uncached_index_responses -eq 0", self.runner)

    def test_exact_once_closure_is_fail_closed(self) -> None:
        for token in (
            "recordAdmission",
            "recordRetirement",
            "DuplicateAdmission",
            "DuplicateRetirement",
            "RetirementBeforeAdmission",
            "finishBoundedRangePass",
            "bounded range exact-once closure failed",
        ):
            self.assertIn(token, self.tracker + self.indirect)
        self.assertIn("event=bounded_range_begin schema=1", self.indirect)
        self.assertIn(
            "event=bounded_range_pass_complete schema=1", self.indirect
        )
        self.assertIn("event=bounded_range_complete schema=1", self.indirect)
        self.assertIn("duplicate_admissions=0", self.indirect)
        self.assertIn("missing=0", self.indirect)

    def test_capacity_drain_gate_is_range_only(self) -> None:
        legacy_gate = re.search(
            r"const bool legacy_refill_allowed\s*=\s*(.*?);",
            self.indirect,
            re.DOTALL,
        )
        self.assertIsNotNone(legacy_gate)
        self.assertRegex(
            legacy_gate.group(1),
            r"!maa->virtual_native_issue_order\s*\|\|\s*"
            r"\(!virtual_build_incomplete\s*&&\s*"
            r"boundedSourceResponsesComplete\(\)\)",
        )
        refill_gate = re.search(
            r"const bool refill_allowed\s*=\s*(.*?);",
            self.indirect,
            re.DOTALL,
        )
        self.assertIsNotNone(refill_gate)
        self.assertRegex(
            refill_gate.group(1),
            r"maa->virtual_index_range_passes\s*\?\s*"
            r"!virtual_build_incomplete\s*:\s*legacy_refill_allowed",
        )

    def test_runner_closes_exact_candidate_signature(self) -> None:
        for token in (
            "index_words -eq $expected_index_words",
            "feeder_descriptor_discards -eq 16384",
            "feeder_partition_discards -eq $expected_partition_discards",
            "range_pass_count -eq $index_partitions",
            "range_complete_count -eq 1",
            "row_slices * row_rows * row_entries",
        ):
            self.assertIn(token, self.runner)

    def test_runner_does_not_forward_zero_offset_sentinels(self) -> None:
        self.assertIn("offset_args=()", self.runner)
        self.assertIn("if [[ $offset_entries -ne 0 ]]; then", self.runner)
        self.assertIn(
            "if [[ $offset_epoch_entries -ne 0 ]]; then", self.runner
        )
        self.assertIn('"${offset_args[@]}"', self.runner)
        self.assertNotIn(
            '--maa_num_offset_table_entries="$offset_entries" \\\n',
            self.runner,
        )
        self.assertNotIn(
            '--maa_num_offset_table_epoch_entries="$offset_epoch_entries" \\\n',
            self.runner,
        )


if __name__ == "__main__":
    unittest.main()
