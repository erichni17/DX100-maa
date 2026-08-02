import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/analysis/dual_locality_scheduler_model.py"
SPEC = importlib.util.spec_from_file_location(
    "dual_locality_scheduler", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DualLocalitySchedulerModelTest(unittest.TestCase):
    def setUp(self):
        self.config = MODULE.ReplayConfig(
            logical_elements=32,
            page_elements=8,
            combine_slots=4,
            combine_ways=1,
            row_burst=4,
            nonfocus_row_bonus=1,
        )

    def test_bank_row_mapping_matches_archived_robaracoch_geometry(self):
        self.assertEqual(MODULE.bank_row_key(0), (0, 0, 0, 0))
        self.assertEqual(MODULE.bank_row_key(1), (1, 0, 0, 0))
        self.assertEqual(MODULE.bank_row_key(256), (0, 1, 0, 0))
        self.assertEqual(MODULE.bank_row_key(1024), (0, 0, 1, 0))
        self.assertEqual(MODULE.bank_row_key(4096), (0, 0, 0, 1))

    def test_hybrid_claims_each_source_once_while_bounded_can_refetch(self):
        pattern = [0, 8, 16, 24, 32, 40, 48, 56] * 4
        full, _ = MODULE.schedule_full_row(pattern, self.config, 0)
        bounded, _ = MODULE.schedule_bounded_pages(pattern, self.config, 0)
        hybrid, mechanism = MODULE.schedule_page_focus_r1(
            pattern, self.config, 0
        )
        MODULE.validate_schedule(full, len(pattern))
        MODULE.validate_schedule(bounded, len(pattern))
        MODULE.validate_schedule(hybrid, len(pattern))
        self.assertEqual(len(full), 8)
        self.assertEqual(len(hybrid), 8)
        self.assertEqual(len(bounded), 32)
        self.assertEqual(mechanism["focus_claims"], 8)
        self.assertEqual(mechanism["nonfocus_row_bonus_claims"], 0)

    def test_page_focus_is_between_full_row_and_bounded_write_geometry(self):
        # Four destination pages share A rows.  Full-row ordering mixes their C
        # lines, while page focus permits one bounded row-retention claim.
        pattern = []
        for word in range(8):
            for page in range(4):
                pattern.append(page * 64 + word * 8)
        result = MODULE.analyze_pattern(pattern, self.config, 0)["policies"]
        full = result["full_row"]
        bounded = result["bounded4"]
        hybrid = result["page_focus_r1"]
        self.assertLessEqual(bounded["c_writes"], hybrid["c_writes"])
        self.assertLessEqual(hybrid["c_writes"], full["c_writes"])
        self.assertLessEqual(
            hybrid["mean_page_issue_complete_ordinal"][0],
            full["mean_page_issue_complete_ordinal"][0],
        )

    def test_combiner_full_line_is_one_write(self):
        schedule = [MODULE.SourceDescriptor(7, tuple(range(8)))]
        result = MODULE.simulate_combiner(schedule, 8, self.config)
        self.assertEqual(result["c_writes"], 1)
        self.assertEqual(result["full_c_writes"], 1)
        self.assertEqual(result["partial_c_writes"], 0)

    def test_policy_state_is_finite_and_explicit(self):
        state = MODULE.policy_state_lower_bound()
        self.assertEqual(state["descriptor_page_mask_bytes"], 8192)
        self.assertEqual(state["row_page_descriptor_counter_bytes"], 4096)
        self.assertEqual(state["slice_control_bytes"], 68)
        self.assertEqual(state["focus_control_bytes"], 1)
        self.assertEqual(state["page_remaining_counter_bytes"], 8)
        self.assertEqual(state["incremental_policy_bytes"], 12365)

        pfcc = MODULE.pfcc64_state_lower_bound()
        self.assertEqual(pfcc["page_subchain_pointer_delta_bytes"], 184320)
        self.assertEqual(pfcc["slice_control_bytes"], 84)
        self.assertEqual(pfcc["carry_payload_bytes"], 4096)
        self.assertEqual(pfcc["carry_offset_token_bytes"], 960)
        self.assertEqual(pfcc["tentative_offset_bitmap_bytes"], 2048)
        self.assertEqual(pfcc["incremental_pfcc64_bytes"], 204049)

    def test_mode_gate_selects_only_cross_page_pressure(self):
        low_pressure = list(range(32))
        low = MODULE.analyze_pattern(low_pressure, self.config, 0)
        self.assertEqual(low["mode_gate"]["selected_tiles"], 0)

        high_pressure = [0, 8, 16, 24, 32, 40, 48, 56] * 4
        high = MODULE.analyze_pattern(high_pressure, self.config, 0)
        self.assertEqual(high["mode_gate"]["selected_tiles"], 1)

    def test_rejects_invalid_pattern_and_unbounded_page_count(self):
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            MODULE.analyze_pattern([0, -1], self.config, 0)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            MODULE.schedule_page_focus_r1(list(range(33)), self.config, 0)

    def test_frozen_replay_artifact_contract(self):
        artifact_path = (
            ROOT
            / "experiments/analysis/hybrid_dual_locality_replay_2026-08-02.json"
        )
        artifact = json.loads(artifact_path.read_text())
        self.assertEqual(artifact["schema"], MODULE.SCHEMA)
        self.assertFalse(artifact["model_scope"]["timing_prediction"])
        self.assertEqual(artifact["flag"]["case_count"], 14)
        self.assertEqual(
            artifact["xrage"]["sha256"],
            "1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde",
        )
        self.assertEqual(artifact["xrage"]["mode_gate"]["selected_tiles"], 71)
        self.assertEqual(
            artifact["pfcc64_state_lower_bound"]["incremental_pfcc64_bytes"],
            204049,
        )
        selected_flag_tiles = sum(
            case["mode_gate"]["selected_tiles"]
            for case in artifact["flag"]["cases"]
        )
        self.assertEqual(selected_flag_tiles, 6)


if __name__ == "__main__":
    unittest.main()
