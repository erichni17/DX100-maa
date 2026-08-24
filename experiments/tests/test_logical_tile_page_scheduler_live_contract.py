#!/usr/bin/env python3
"""Source contract for the exact single-arm logical-page live guest."""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUEST = ROOT / "benchmarks/API/test_logical_tile_page_scheduler_live.cpp"
RUNNER = ROOT / "experiments/scripts/run_logical_tile_page_scheduler_live.sh"
MAA = ROOT / "src/mem/MAA/MAA.cc"
STREAM = ROOT / "src/mem/MAA/StreamAccess.cc"


class LogicalTilePageSchedulerLiveContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guest = GUEST.read_text()
        cls.runner = RUNNER.read_text()
        cls.maa = MAA.read_text()
        cls.stream = STREAM.read_text()

    def test_guest_covers_required_fp32_operations(self):
        for call in (
            "maa_alu_scalar_logical<float>(0, 2",
            "maa_alu_vector_logical<float>(0, 1, 3",
            "maa_alu_vector_logical<float>(3, 3, 4",
            "maa_stream_store_logical<float>(denseStore, 4",
        ):
            self.assertIn(call, self.guest)
        self.assertIn("Operation_t::MUL_OP", self.guest)
        self.assertIn("Operation_t::ADD_OP", self.guest)

    def test_guest_exercises_two_exact_generations(self):
        self.assertEqual(self.guest.count("maa_stream_load_logical<float>"), 3)
        self.assertIn("maa_stream_load_logical<float>(c, 0", self.guest)
        self.assertIn("maa_alu_scalar_logical<float>(0, 2, c", self.guest)
        self.assertIn("operations=9 generations=2", self.guest)

    def test_guest_has_fixed_input_and_output_hash_guards(self):
        for value in (
            "5238007371172236237ULL",
            "4619008359347519206ULL",
            "8757546768500349369ULL",
            "1468879162217515462ULL",
            "9332068828147211593ULL",
            "12485598873299661541ULL",
            "16675341876698374373ULL",
        ):
            self.assertIn(value, self.guest)
        self.assertIn("hashBacking(a)", self.guest)
        self.assertIn("hashBacking(denseStoreGeneration2)", self.guest)

    def test_runner_is_one_production_arm(self):
        self.assertEqual(self.runner.count("restore_cmd=("), 1)
        self.assertIn("--maa_logical_tile_page_scheduler", self.runner)
        self.assertIn("--maa_num_tiles_per_core=4", self.runner)
        self.assertIn("comparison_arms=0", self.runner)
        self.assertNotIn("native4", self.runner.lower())
        self.assertNotIn("native16_cmd", self.runner.lower())
        self.assertNotIn("baseline", self.runner.lower())
        self.assertNotIn("timeout ", self.runner.lower())

    def test_runner_binds_exact_existing_lane_accounting(self):
        for field in (
            "total_lanes=16",
            "reserved_lanes=8",
            "guest_visible_lanes=8",
            "additional_payload_bytes=0",
            "payload_reduction_vs_same_16-lane_native16=75%%",
        ):
            self.assertIn(field, self.runner)
        self.assertIn("'num_tiles_per_core=4'", self.runner)
        self.assertNotIn("--maa_num_tiles_per_core=8", self.runner)

    def test_runner_requires_exact_architectural_and_native_closure(self):
        for clause in (
            'logical_page_admit \' "$trace" || true) -eq 9',
            'logical_page_begin \' "$trace" || true) -eq 36',
            'logical_page_native_dispatch \' "$trace" || true) -eq 80',
            'logical_page_native_complete \' "$trace" || true) -eq 80',
            'logical_page_retire \' "$trace" || true) -eq 9',
        ):
            self.assertIn(clause, self.runner)
        self.assertIn("0:12 1:24 2:4 3:8 4:8 5:8 6:16", self.runner)

    def test_runner_requires_all_response_bearing_writes(self):
        for stat in (
            "STR_PublishIssues",
            "STR_PublishAccepts",
            "STR_PublishWriteResponses",
        ):
            self.assertIn(f"[[ $(sum_stat '{stat}') -eq 6144 ]]", self.runner)
        self.assertIn("STR_PublishTerminals') -eq 24", self.runner)
        for event in (
            "spd_publish_issue",
            "spd_publish_accept",
            "spd_publish_response",
        ):
            self.assertIn(
                f'event={event} \' "$trace" || true) -eq 6144', self.runner
            )
        self.assertIn(
            'event=spd_publish_terminal \' "$trace" || true) -eq 24',
            self.runner,
        )

    def test_lifecycle_dispatches_only_native_units(self):
        for opcode in (
            "Instruction::OpcodeType::STREAM_LD",
            "Instruction::OpcodeType::ALU_SCALAR",
            "Instruction::OpcodeType::ALU_VECTOR",
            "Instruction::OpcodeType::STREAM_ST",
        ):
            self.assertIn(opcode, self.maa)
        self.assertIn("logical_page_native_dispatch", self.maa)
        self.assertIn("logical_page_native_complete", self.maa)
        self.assertIn("MemCmd::WriteReq", self.stream)
        self.assertIn("MemCmd::WriteResp", self.stream)

    def test_runner_shell_is_valid(self):
        subprocess.run(["bash", "-n", str(RUNNER)], check=True)


if __name__ == "__main__":
    unittest.main()
