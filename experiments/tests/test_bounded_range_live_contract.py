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
        cls.matrix = (
            ROOT / "experiments/scripts/run_bounded_row_matched_matrix.sh"
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

    def test_source_relative_policy_is_optional_and_fail_closed(self) -> None:
        param = (ROOT / "src/mem/MAA/MAA.py").read_text()
        self.assertRegex(
            param,
            r"virtual_index_range_policy\s*=\s*Param\.Unsigned\(\s*0,",
        )
        self.assertIn("configureRange", self.tracker + self.indirect)
        self.assertIn("directIndexSourceGrowRange", self.indirect)
        self.assertIn("source-relative grow range", self.indirect)
        self.assertIn("MAA_VIRTUAL_INDEX_RANGE_POLICY=1", self.matrix)

    def test_explicit_ranges_are_labeled_as_oracle_only(self) -> None:
        param = (ROOT / "src/mem/MAA/MAA.py").read_text()
        self.assertIn("explicit oracle", param)
        self.assertIn("configureRanges", self.tracker + self.indirect)
        self.assertIn("provenance=offline_profile", self.indirect)
        self.assertIn("MAA_ORACLE_RANGE_BOUNDARIES", self.matrix)
        self.assertIn("bounded_oracle_range_4k", self.matrix)
        self.assertIn(
            "oracle policy requires partitions+1 boundaries", self.runner
        )

    def test_candidate_caps_native_active_tables(self) -> None:
        self.assertIn("MaxActiveEntries = 4096", self.tracker)
        self.assertIn("offset_table->capacity()", self.indirect)
        self.assertIn("active_row_line_slots", self.indirect)
        self.assertIn("num_RT_slice_columns[initial_RT_config]", self.indirect)
        self.assertIn("Bounded range passes allow at most 4096", self.maa)

    def test_single_row_table_reset_skips_unallocated_configs(self) -> None:
        reset = re.search(
            r"void IndirectAccessUnit::check_reset\(\).*?offset_table->check_reset",
            self.indirect,
            re.DOTALL,
        )
        self.assertIsNotNone(reset)
        self.assertRegex(
            reset.group(0),
            r"if \(RT\[i\] == nullptr\)\s+continue;\s+"
            r"for \(int j = 0; j < num_RT_slices\[i\]; j\+\+\)",
        )

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
        self.assertNotIn("std::vector<uint64_t> admitted", self.tracker)
        self.assertNotIn("std::vector<uint64_t> retired", self.tracker)
        self.assertIn("bytes.identityBitmaps = 0", self.tracker)
        self.assertIn("passAdmissions", self.tracker)
        self.assertIn("passRetirements", self.tracker)
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
        self.assertEqual(self.indirect.count("checker_bytes=%lu"), 3)
        self.assertNotIn("checker_bytes=%zu", self.indirect)

    def test_policy3_uses_translated_grow_plan_and_bounded_quotas(
        self,
    ) -> None:
        planner = (ROOT / "src/mem/MAA/BoundedQuantileRanges.hh").read_text()
        for token in (
            "class BoundedGrowPassPlan",
            "MaxRecords = 64",
            "passQuotas",
            "RequiresIterationFallback",
        ):
            self.assertIn(token, planner)
        self.assertIn("key=translated_dram_grow", self.indirect)
        self.assertIn("recordSelectedAdmission", self.indirect)
        self.assertIn("IND_BoundedSummaryPlanBytes", self.indirect)
        self.assertIn("commitReplayOrdinal", planner)
        self.assertIn("peekReplayOrdinal", planner)
        self.assertIn("StaleReplayOrdinal", planner)
        self.assertIn("modeledReductionVisits", planner)
        self.assertNotIn(
            "2 * static_cast<uint64_t>(offset_table->capacity())",
            self.indirect,
        )
        self.assertRegex(
            self.indirect,
            r"discardDirectIndex\(\s*my_i,[\s\S]*?\);\s*"
            r"if \(commit_grow_ordinal\)[\s\S]*?commitReplayOrdinal",
        )

    def test_evidence_snapshot_covers_bounded_treatment_sources(self) -> None:
        for name in (
            "BoundedRangePass.hh",
            "BoundedQuantileRanges.hh",
            "BoundedMetadataLedger.hh",
            "Tables.cc",
            "Tables.hh",
            "bounded_range_pass_test.cc",
            "bounded_quantile_ranges_test.cc",
            "bounded_metadata_ledger_test.cc",
            "test_bounded_range_live_contract.py",
            "run_bounded_range_pass_unit.sh",
            "run_true_4k_reorder_matrix.sh",
        ):
            self.assertGreaterEqual(self.runner.count(name), 2)

    def test_true_matrix_separates_requests_from_unique_lines(self) -> None:
        matrix = (
            ROOT / "experiments/scripts/run_true_4k_reorder_matrix.sh"
        ).read_text()
        self.assertIn("field row_table_cache_lines", matrix)
        self.assertIn("field row_table_unique_cache_lines", matrix)
        self.assertIn("a_line_requests\\ta_unique_lines", matrix)
        self.assertIn("row_insertions\\ttranslated_unique_rows", matrix)

    def test_physical_grow_arm_fails_closed_on_fallback(self) -> None:
        for token in (
            "fallback=none plan_result=accepted",
            "iteration_fallbacks -eq 0",
            "physical_records -eq 16384",
            "translated_grow_histogram.tsv",
            "bounded_summary_histogram_sha256",
        ):
            self.assertIn(token, self.runner)
        matrix = (
            ROOT / "experiments/scripts/run_true_4k_reorder_matrix.sh"
        ).read_text()
        self.assertIn("MAA_REQUIRE_PHYSICAL_RECORD_TRACE=1", matrix)
        self.assertIn("MAAPhysicalRecordTrace", matrix)

    def test_true_matrix_isolates_selectors_before_parallel_restore(
        self,
    ) -> None:
        matrix = (
            ROOT / "experiments/scripts/run_true_4k_reorder_matrix.sh"
        ).read_text()
        self.assertIn(
            'DX100_SHARED_TREATMENT_FILE="$out/${label}.treatment.txt"',
            matrix,
        )
        self.assertIn(
            'DX100_SHARED_CHECKPOINT_DIR="$out/checkpoints/$label"',
            matrix,
        )
        self.assertIn("DX100_SHARED_CHECKPOINT_LOG=", matrix)
        self.assertEqual(matrix.count('arm_pids+=("$!")'), 3)
        self.assertIn('wait_all "${arm_pids[@]}"', matrix)
        self.assertIn(
            "shared_checkpoint_log=${DX100_SHARED_CHECKPOINT_LOG:-}",
            self.runner,
        )

    def test_capacity_drain_gate_covers_finite_direct_index_passes(
        self,
    ) -> None:
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
            r"finite_direct_index_pass\s*\?\s*"
            r"!virtual_build_incomplete\s*:\s*legacy_refill_allowed",
        )
        self.assertRegex(
            self.indirect,
            r"const bool finite_direct_index_pass\s*=\s*"
            r"isDirectIndexLoad\(\)\s*&&\s*"
            r"direct_index_partitions\s*>\s*1",
        )

    def test_runner_closes_exact_candidate_signature(self) -> None:
        for token in (
            "index_words -eq $expected_index_words",
            "feeder_descriptor_discards -eq $expected_descriptor_discards",
            "feeder_partition_discards -eq $expected_partition_discards",
            "range_pass_count -eq $actual_index_partitions",
            "range_complete_count -eq 1",
            "row_slices * row_rows * row_entries",
        ):
            self.assertIn(token, self.runner)

    def test_index_high_water_uses_configured_finite_capacity(self) -> None:
        self.assertIn("index_buffer_lines=4", self.runner)
        self.assertIn(
            "index_hwm_capacity=$((index_buffer_lines * 4 * 16))",
            self.runner,
        )
        self.assertEqual(
            self.runner.count("index_hwm -le $index_hwm_capacity"), 2
        )
        self.assertNotIn("index_hwm -le 64", self.runner)

    def test_filter_retry_accounting_includes_offset_epoch_drains(
        self,
    ) -> None:
        self.assertIn("IND_NumOTEpochDrain", self.runner)
        self.assertRegex(
            self.runner,
            r"expected_filter_words \+ rt_full \+ \\\n\s+offset_epoch_drains",
        )

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
