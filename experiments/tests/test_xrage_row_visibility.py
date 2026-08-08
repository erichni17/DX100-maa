#!/usr/bin/env python3

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "analysis"))

import analyze_xrage_row_visibility as visibility  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class XRAGERowVisibilityTest(unittest.TestCase):
    def test_semantic_rowtable_delta(self) -> None:
        result = visibility.semantic_storage_delta()
        self.assertEqual(result["added_rows"], 1920)
        self.assertEqual(result["added_entry_slots"], 32768)
        self.assertEqual(result["semantic_core_array_delta_bytes"], 616704)
        self.assertEqual(
            result["active_16_slice_organization_delta_bytes"], 161792
        )

    def test_distribution_is_exact_and_keeps_histogram(self) -> None:
        result = visibility.distribution([0, 1, 1, 9])
        self.assertEqual(result["sum"], 11)
        self.assertEqual(result["median"], 1.0)
        self.assertEqual(result["p90_nearest_rank"], 9)
        self.assertEqual(result["histogram"], {"0": 1, "1": 2, "9": 1})

    def test_dram_parser_uses_final_channel_totals(self) -> None:
        text = "\n".join(
            [
                "CH0_num_RD_commands_T: 1",
                "CH0_num_WR_commands_T: 2",
                "CH0_num_ACT_commands_T: 3",
                "CH0_num_PRE_commands_T: 4",
                "CH0_num_RD_commands_T: 10",
                "CH0_num_WR_commands_T: 20",
                "CH0_num_ACT_commands_T: 30",
                "CH0_num_PRE_commands_T: 40",
                "CH1_num_RD_commands_T: 11",
                "CH1_num_WR_commands_T: 21",
                "CH1_num_ACT_commands_T: 31",
                "CH1_num_PRE_commands_T: 41",
            ]
        )
        result = visibility.dram_commands(text)
        self.assertEqual(
            result["aggregate"], {"rd": 21, "wr": 41, "act": 61, "pre": 81}
        )

    def make_rep(self, root: Path, label: str = "row64") -> tuple[Path, dict]:
        rep = root / label / "rep1"
        run = rep / "run"
        run.mkdir(parents=True)
        rows = visibility.EXPECTED_ROWS[label]
        manifest = {
            "source_commit": visibility.EXPECTED_SOURCE,
            "runner_source_commit": visibility.EXPECTED_SOURCE,
            "checkpoint_run": "/checkpoint",
            "checkpoint_manifest_sha256": "0" * 64,
            "checkpoint_provenance": "attested",
            "checkpoint_retargeted": "0",
            "checkpoint_original_arm": "direct_index_4k",
            "checkpoint_original_physical": "4096",
            "arm": "direct_index_4k",
            "guest_arm": "direct4",
            "physical_tile_elements": "4096",
            "maa_logical_tile_elements": "16384",
            "workload_chunk_elements": "16384",
            "virtual_grow_order": "1",
            "virtual_native_issue_order": "0",
            "virtual_index_buffer_lines": "128",
            "virtual_index_force_cache": "1",
            "virtual_index_partitions": "1",
            "virtual_index_filter_words_per_cycle": "0",
            "virtual_partition_keep_combiner": "0",
            "retirement_cache_size": "1kB",
            "virtual_combine_slots": "384",
            "virtual_combine_words": "4096",
            "virtual_combine_ways": "4",
            "initial_row_table_slices": "16",
            "row_table_rows_per_slice": str(rows),
            "offset_table_entries": "16384",
            "offset_table_epoch_entries": "16384",
            "num_indirect_units_per_maa": "1",
            "virtual_response_slots": "128",
            "virtual_response_word_pool": "480",
            "virtual_words_per_cycle": "4",
            "debug_flags": "MAAReorderTrace,MAAIssueDigest",
            "input": "/input",
            "created_utc": "now",
            "timeout": "none",
        }
        (rep / "manifest.txt").write_text(
            "".join(f"{key}={value}\n" for key, value in manifest.items())
        )
        trace = []
        for instruction in range(visibility.EXPECTED_INDIRECT_INSTRUCTIONS):
            tick = 1000 + instruction
            identity = (
                f"unit=0 instruction_id={instruction} operation_tick={tick} "
                "pc=0x1 cid=0 if_id=0 opcode=14"
            )
            trace.append(
                "schema=dx100.reorder_epoch.v1 event=reorder_epoch "
                f"{identity} epoch_id=0 admissions=16384 issued_lines=1 "
                "issued_entries=16384 max_joint_admissions=16384 "
                "row_transitions=0 rt_full_drains=0 offset_drains=0 "
                "partition_drains=0 final=1"
            )
            trace.append(
                "schema=dx100.reorder_summary.v1 event=reorder_summary "
                f"{identity} predicate_present=0 selected_descriptors=16384 "
                "epochs=1 total_admitted=16384 max_joint_admissions=16384 "
                "rt_full_drains=0 offset_drains=0 partition_drains=0 "
                "mid_instruction_drains=0 total_issued_lines=1 "
                "total_issued_entries=16384 row_transitions=0 reconciled=1 "
                "classification=preserved"
            )
            trace.append(
                f"unit=0 instruction_tick={tick} count=1 "
                "fnv=0x1111111111111111 mix=0x2222222222222222"
            )
        (run / "xrage-debug.log").write_text("\n".join(trace) + "\n")
        stats = (
            "---------- Begin Simulation Statistics ----------\n"
            "simTicks 12345\n"
            f"system.maa.numInst_INDRD {visibility.EXPECTED_INDIRECT_INSTRUCTIONS}\n"
            "system.maa.I0_IND_CyclesFill 10\n"
            "system.maa.I0_IND_CyclesRequest 20\n"
            "---------- End Simulation Statistics ----------\n"
            "---------- Begin Simulation Statistics ----------\n"
            "simTicks 99999\n"
            "---------- End Simulation Statistics ----------\n"
        )
        (run / "stats.txt").write_text(stats)
        log = (
            f"MAA_GATHER_VERIFY_PASS length={visibility.EXPECTED_ELEMENTS} "
            f"hash={visibility.EXPECTED_HASH}\n"
            "CH0_num_RD_commands_T: 10\n"
            "CH0_num_WR_commands_T: 20\n"
            "CH0_num_ACT_commands_T: 30\n"
            "CH0_num_PRE_commands_T: 40\n"
            "Exiting @ tick 999 because m5_exit instruction encountered\n"
        )
        (rep / "restore.log").write_text(log)
        (rep / "restore.exit").write_text("0\n")
        (rep / "checkpoint_sha256.txt").write_text("abc  checkpoint\n")
        (rep / "result.tsv").write_text(
            "output_hash\troi_simTicks\n"
            f"{visibility.EXPECTED_HASH}\t12345\n"
        )
        frozen = {}
        artifact_lines = []
        for name in ("gem5", "workload", "input"):
            path = root / name
            path.write_text(name)
            frozen[name] = {"path": str(path), "sha256": digest(path)}
            artifact_lines.append(f"{digest(path)}  {path}\n")
        (rep / "artifact_sha256.txt").write_text("".join(artifact_lines))
        return rep, frozen

    def test_reconciles_every_xrage_indirect_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rep, frozen = self.make_rep(Path(directory))
            result = visibility.analyze_rep(rep, "row64", frozen)
            self.assertEqual(
                result["indirect_instruction_count"],
                visibility.EXPECTED_INDIRECT_INSTRUCTIONS,
            )
            self.assertEqual(result["issue_digest_count"], 128)
            self.assertTrue(result["all_indirect_instructions_reconciled"])
            self.assertEqual(
                result["distributions"]["issued_a_lines"]["sum"], 128
            )

    def test_rejects_issue_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rep, frozen = self.make_rep(Path(directory))
            trace = rep / "run" / "xrage-debug.log"
            text = trace.read_text().replace("count=1", "count=2", 1)
            trace.write_text(text)
            with self.assertRaisesRegex(
                visibility.VisibilityError, "issued-line mismatch"
            ):
                visibility.analyze_rep(rep, "row64", frozen)

    def test_runner_is_serial_and_keeps_only_rows_as_treatment(self) -> None:
        runner = (
            ROOT
            / "experiments"
            / "scripts"
            / "run_xrage_row_visibility_pair.sh"
        ).read_text()
        self.assertIn("run_rep row64 64 1\nrun_rep row128 128 1", runner)
        self.assertNotIn("run_rep row64 64 1 &", runner)
        self.assertIn(
            "XRAGE_DEBUG_FLAGS=MAAReorderTrace,MAAIssueDigest", runner
        )
        self.assertIn("MAA_NUM_OFFSET_TABLE_ENTRIES=16384", runner)
        self.assertIn("MAA_NUM_OFFSET_TABLE_EPOCH_ENTRIES=16384", runner)
        self.assertIn("MAA_VIRTUAL_INDEX_PARTITIONS=1", runner)
        self.assertIn("high-cost diagnostic; never baseline", runner)


if __name__ == "__main__":
    unittest.main()
