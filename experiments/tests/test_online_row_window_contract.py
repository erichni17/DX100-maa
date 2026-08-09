import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class OnlineRowWindowContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = (ROOT / "src/mem/MAA/OnlineRowWindow.hh").read_text()
        cls.indirect_hh = (ROOT / "src/mem/MAA/IndirectAccess.hh").read_text()
        cls.indirect = (ROOT / "src/mem/MAA/IndirectAccess.cc").read_text()
        cls.tables_hh = (ROOT / "src/mem/MAA/Tables.hh").read_text()
        cls.tables = (ROOT / "src/mem/MAA/Tables.cc").read_text()
        cls.maa = (ROOT / "src/mem/MAA/MAA.cc").read_text()
        cls.runner = (
            ROOT / "experiments/scripts/run_virtual_tile_consumer_case.sh"
        ).read_text()
        cls.matrix = (
            ROOT / "experiments/scripts/run_true_4k_online_matrix.sh"
        ).read_text()

    def test_policy_has_hard_finite_bounds(self) -> None:
        for token in (
            "MaxTrackedGrows = 512",
            "MaxDescriptors = 4096",
            "MaxLineSlots = 4096",
            "MaxRowDirectories = 512",
            "HistoryOverflow",
            "DescriptorOverflow",
            "LineOverflow",
            "RowOverflow",
        ):
            self.assertIn(token, self.policy)

    def test_policy_stores_no_exact_descriptor_or_payload_vector(self) -> None:
        self.assertNotIn("std::vector", self.policy)
        for forbidden in (
            "line_paddr",
            "source_addr",
            "index_value",
            "result_payload",
            "host_oracle",
            "fallback_records",
        ):
            self.assertNotIn(forbidden, self.policy)
        self.assertIn("std::array<Entry, MaxTrackedGrows>", self.policy)
        self.assertIn(
            "if (!usesOnlineRowWindow()) {\n"
            "                        my_unique_WORD_addrs.insert(vaddr);",
            self.indirect,
        )
        self.assertIn(
            "if (usesOnlineRowWindow()) {\n"
            "                my_force_cache = true;",
            self.indirect,
        )

    def test_oldest_selection_is_online_and_deterministic(self) -> None:
        select = re.search(
            r"Selection selectOldest\(\) const.*?return selected;",
            self.policy,
            re.DOTALL,
        )
        self.assertIsNotNone(select)
        self.assertIn("entry.birth < oldest", select.group(0))
        self.assertIn("entry.grow < selected.grow", select.group(0))
        self.assertIn("recordVictim", self.policy)
        self.assertIn("reopenedGrows++", self.policy)

    def test_one_scan_path_is_separate_from_replay(self) -> None:
        for token in (
            "usesOnlineRowWindow",
            "selectOnlineRowVictim",
            "online_row_window.recordAdmission",
            "online_row_window.recordRetirement",
            "online_row_window.finish",
            "b_passes=1",
            "fallback=none overflow=none placement=iteration",
        ):
            self.assertIn(token, self.indirect_hh + self.indirect)
        self.assertNotIn("beginReplay", self.policy)
        self.assertNotIn("direct_index_next_prefetch_itr = 0", self.policy)

    def test_only_selected_grow_is_claimed_during_pressure(self) -> None:
        self.assertIn("claim_entry_send_for_grow", self.tables_hh)
        self.assertIn(
            "entries[row_id].grow_addr != selected_grow", self.tables
        )
        self.assertIn(
            ".claim_entry_send_for_grow(\n"
            "                                online_row_victim.grow",
            self.indirect,
        )

    def test_constructor_rejects_illegal_capacity_or_fallback(self) -> None:
        for token in (
            "Online row window requires exactly 16384 logical entries",
            "Online row window requires exactly 4096 physical payload",
            "Online row window requires exactly 4096 Word/Offset slots",
            "Online row window exceeds RowTable bounds",
            "Online row window requires one non-replay index pass",
            "Online row window requires timing-visible coherent B ",
            "Online row window requires one RowTable organization",
        ):
            self.assertIn(token, self.maa)

    def test_policy_scan_is_timing_charged(self) -> None:
        self.assertIn(
            "num_rowtable_accesses += online_row_victim.visits", self.indirect
        )
        self.assertIn("IND_OnlineWindowSelectionVisits", self.indirect)
        self.assertIn("policy_bytes=%lu", self.indirect)

    def test_runner_requires_exact_online_closure(self) -> None:
        for token in (
            "MAA_VIRTUAL_ONLINE_ROW_WINDOW",
            "--maa_virtual_online_row_window",
            "online_admissions -eq 16384",
            "online_retirements -eq 16384",
            "online_max_descriptors -le 4096",
            "online_max_lines -le 4096",
            "online_max_rows -le 512",
            "online_policy_bytes -eq 12416",
            "uncached_index_responses -eq 0",
        ):
            self.assertIn(token, self.runner)

    def test_evidence_snapshots_spd_and_treatment_sources(self) -> None:
        for name in (
            "SPD.cc",
            "SPD.hh",
            "OnlineRowWindow.hh",
            "online_row_window_test.cc",
            "test_online_row_window_contract.py",
            "run_true_4k_online_matrix.sh",
        ):
            self.assertGreaterEqual(self.runner.count(name), 2)

    def test_matrix_is_matched_and_uses_one_checkpoint_lineage(self) -> None:
        for arm in ("native16", "native4", "replay9dd", "online_oldest"):
            self.assertIn(arm, self.matrix)
        self.assertEqual(self.matrix.count("create_checkpoint"), 2)
        self.assertIn('DX100_SHARED_CHECKPOINT_DIR="$checkpoint"', self.matrix)
        self.assertIn(
            'cmp -s "$out/$label/shared_checkpoint_files.sha256"', self.matrix
        )
        self.assertIn("DX100_BINARY_SOURCE_COMMIT", self.runner)
        self.assertIn("gem5.replay9dd.opt", self.matrix)
        self.assertIn("replay_expected_sha=64980714", self.matrix)
        self.assertIn('"$replay_gem5" "$replay_commit"', self.matrix)
        self.assertIn("replay_source_snapshot/SPD.cc", self.matrix)
        self.assertIn("replay_source_snapshot/SPD.hh", self.matrix)
        self.assertNotRegex(
            self.matrix,
            r"run_arm (native16|native4|replay9dd|online_oldest).*?\s&",
        )
        self.assertIn("9ddf1ad3", self.matrix)


if __name__ == "__main__":
    unittest.main()
