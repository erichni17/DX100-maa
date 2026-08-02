import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments/analysis/corrected_hybrid_scheduler_model.py"
SPEC = importlib.util.spec_from_file_location(
    "corrected_hybrid_scheduler", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CorrectedHybridSchedulerModelTest(unittest.TestCase):
    def small_config(self, **overrides):
        values = {
            "logical_elements": 32,
            "page_elements": 8,
            "word_bytes": 8,
            "cache_line_bytes": 64,
            "combine_slots": 4,
            "combine_ways": 1,
            "owner_lines": 4,
            "owner_ways": 1,
            "source_request_slots": 2,
            "source_response_slots": 2,
            "write_request_slots": 2,
            "write_ack_slots": 2,
            "new_focus_owner_lines_per_request": 4,
            "new_future_owner_lines_per_request": 1,
            "row_burst": 4,
        }
        values.update(overrides)
        return MODULE.ReplayConfig(**values)

    def test_bank_row_mapping_matches_archived_geometry(self):
        self.assertEqual(MODULE.bank_row_key(0), (0, 0, 0, 0))
        self.assertEqual(MODULE.bank_row_key(1), (1, 0, 0, 0))
        self.assertEqual(MODULE.bank_row_key(256), (0, 1, 0, 0))
        self.assertEqual(MODULE.bank_row_key(1024), (0, 0, 1, 0))
        self.assertEqual(MODULE.bank_row_key(4096), (0, 0, 0, 1))

    def test_line_owner_survives_focus_change_and_promotes_missing_word(self):
        # Source line 1 supplies the last focus-page word and the first word of
        # the next destination line.  That future line remains singly owned
        # across the focus change, then source line 2 is promoted to finish it.
        pattern = list(range(16))
        pattern[0] = 0
        pattern[1] = 8
        pattern[8] = 8
        pattern[9] = 16
        live = [False] * 16
        for destination in (0, 1, 8, 9):
            live[destination] = True
        scheduler = MODULE.CorrectedHybridScheduler(
            pattern,
            live,
            self.small_config(logical_elements=16),
            generation=7,
        )
        metrics = scheduler.run()
        self.assertTrue(scheduler.done())
        self.assertGreaterEqual(metrics["owner_promotions"], 1)
        self.assertEqual(metrics["owner_allocations"], 2)
        self.assertEqual(metrics["c_write_requests"], 2)
        self.assertEqual(metrics["c_write_acceptances"], 2)
        self.assertEqual(metrics["c_write_completions"], 2)
        self.assertEqual(metrics["partial_live_mask_writes"], 0)
        self.assertEqual(metrics["focus_switches"], 2)

    def test_predicated_holes_define_the_exact_expected_mask(self):
        pattern = list(range(8))
        live = [True, False, True, False, False, False, False, False]
        scheduler = MODULE.CorrectedHybridScheduler(
            pattern,
            live,
            self.small_config(logical_elements=8, page_elements=8),
            generation=3,
        )
        self.assertEqual(scheduler.expected_masks, {0: 0b00000101})
        metrics = scheduler.run()
        self.assertEqual(metrics["live_words"], 2)
        self.assertEqual(metrics["predicated_false_words"], 6)
        self.assertEqual(metrics["exact_live_mask_writes"], 1)
        self.assertEqual(metrics["full_physical_mask_writes"], 0)
        self.assertTrue(
            all(
                state == "predicated_false"
                for index, state in enumerate(scheduler.token_state)
                if not live[index]
            )
        )

    def test_queue_full_holds_owner_until_true_write_completion(self):
        pattern = [0] * 8 + [8] * 8
        config = self.small_config(
            logical_elements=16,
            owner_lines=1,
            owner_ways=1,
            source_request_slots=1,
            source_response_slots=1,
            write_request_slots=1,
            write_ack_slots=1,
            new_focus_owner_lines_per_request=1,
        )
        scheduler = MODULE.CorrectedHybridScheduler(
            pattern, config=config, generation=11
        )
        for _ in range(32):
            scheduler.step(auto_complete_writes=False)
            if scheduler.write_acks:
                break
        self.assertEqual(len(scheduler.write_acks), 1)
        accepted = scheduler.write_acks[0]
        self.assertIn(accepted.line, scheduler.owners)
        self.assertEqual(scheduler.write_request_acceptances, 1)
        self.assertEqual(scheduler.write_completions, 0)
        for _ in range(3):
            scheduler.step(auto_complete_writes=False)
        self.assertLessEqual(len(scheduler.owners), 1)
        self.assertEqual(len(scheduler.write_acks), 1)
        self.assertEqual(scheduler.write_completions, 0)
        self.assertTrue(scheduler.complete_oldest_write())
        metrics = scheduler.run()
        self.assertEqual(metrics["owner_high_water"], 1)
        self.assertEqual(metrics["source_request_queue_high_water"], 1)
        self.assertEqual(metrics["source_response_queue_high_water"], 1)
        self.assertEqual(metrics["write_request_queue_high_water"], 1)
        self.assertEqual(metrics["write_ack_queue_high_water"], 1)

    def test_stale_source_and_write_responses_do_not_release_current_owner(
        self,
    ):
        config = self.small_config(logical_elements=8, page_elements=8)
        scheduler = MODULE.CorrectedHybridScheduler(
            list(range(8)), config=config, generation=9
        )
        stale = MODULE.SourceResponse(99, 8, 0, ())
        self.assertTrue(scheduler.inject_source_response(stale))
        self.assertTrue(scheduler.deliver_source_response())
        self.assertEqual(scheduler.stale_source_responses, 1)
        for _ in range(32):
            scheduler.step(auto_complete_writes=False)
            if scheduler.write_acks:
                break
        request = scheduler.write_acks[0]
        self.assertFalse(
            scheduler.complete_external_write(
                request.generation - 1, request.line, request.request_id
            )
        )
        self.assertIn(request.line, scheduler.owners)
        self.assertEqual(scheduler.stale_write_responses, 1)
        self.assertTrue(
            scheduler.complete_external_write(
                request.generation, request.line, request.request_id
            )
        )
        self.assertTrue(scheduler.done())

    def test_adversarial_reuse_is_live_under_fair_acceptance_and_ack(self):
        pattern = [(destination % 4) * 8 for destination in range(32)]
        scheduler = MODULE.CorrectedHybridScheduler(
            pattern, config=self.small_config(), generation=5
        )
        metrics = scheduler.run(max_steps=1024)
        self.assertTrue(scheduler.done())
        self.assertLess(metrics["transition_steps"], 1024)
        self.assertLessEqual(metrics["source_request_issues"], 32)
        self.assertEqual(
            metrics["source_request_issues"],
            metrics["source_response_completions"],
        )
        self.assertEqual(metrics["c_write_completions"], 4)

    def test_replay_executes_all_three_policies_and_charges_scan_barrier(self):
        config = self.small_config()
        result = MODULE.analyze_pattern(list(range(32)), config)
        self.assertEqual(
            set(result["policies"]), {"full_row", "direct4", "corrected"}
        )
        corrected = result["policies"]["corrected"]
        direct = result["policies"]["direct4"]
        self.assertIn("executed", corrected["policy_kind"])
        self.assertEqual(corrected["preissue_barrier_words"], 32)
        self.assertEqual(corrected["index_scan_words"], 32)
        self.assertEqual(direct["preissue_barrier_words"], 8)
        self.assertEqual(
            corrected["c_write_acceptances"],
            corrected["c_write_completions"],
        )

    def test_state_contract_accounts_exact_masks_generations_and_all_queues(
        self,
    ):
        state = MODULE.corrected_state_lower_bound(self.small_config())
        for component in (
            "exact_live_mask_table_bytes",
            "owner_metadata_bytes",
            "source_request_queue_bytes",
            "source_response_queue_bytes",
            "write_request_queue_bytes",
            "write_ack_queue_bytes",
        ):
            self.assertGreater(state[component], 0)
        self.assertEqual(
            state["bit_packed_policy_state_bytes"],
            sum(
                value
                for key, value in state.items()
                if key != "bit_packed_policy_state_bytes"
            ),
        )

    def test_rejects_invalid_predicate_and_generation(self):
        with self.assertRaisesRegex(ValueError, "length"):
            MODULE.CorrectedHybridScheduler([0, 1], [True])
        with self.assertRaisesRegex(ValueError, "generation"):
            MODULE.CorrectedHybridScheduler([0], generation=0)

    def test_frozen_archive_contract(self):
        artifact_path = (
            ROOT / "experiments/analysis/"
            "corrected_hybrid_scheduler_replay_2026-08-02.json"
        )
        if not artifact_path.exists():
            self.skipTest(
                "frozen archive is generated after focused unit tests"
            )
        artifact = json.loads(artifact_path.read_text())
        self.assertEqual(artifact["schema"], MODULE.SCHEMA)
        self.assertFalse(artifact["model_scope"]["timing_prediction"])
        self.assertFalse(artifact["model_scope"]["synthesis_or_area_claim"])
        self.assertEqual(artifact["flag"]["case_count"], 14)
        self.assertEqual(
            set(artifact["flag"]["policies"]),
            {"full_row", "direct4", "corrected"},
        )
        self.assertEqual(
            artifact["xrage"]["sha256"],
            "1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde",
        )
        self.assertEqual(
            set(artifact["xrage"]["policies"]),
            {"full_row", "direct4", "corrected"},
        )


if __name__ == "__main__":
    unittest.main()
