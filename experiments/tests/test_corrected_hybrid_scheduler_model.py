import ast
import hashlib
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

    def test_forged_reordered_and_duplicate_source_responses_are_isolated(
        self,
    ):
        config = self.small_config(
            logical_elements=8,
            page_elements=8,
            source_request_slots=4,
            source_response_slots=4,
        )
        scheduler = MODULE.CorrectedHybridScheduler(
            [0, 8, 16, 24, 32, 40, 48, 56],
            config=config,
            generation=17,
        )
        self.assertTrue(scheduler.issue_source_request())
        self.assertTrue(scheduler.issue_source_request())
        self.assertTrue(scheduler.accept_source_request(auto_respond=False))
        self.assertTrue(scheduler.accept_source_request(auto_respond=False))
        accepted = list(scheduler.accepted_source_requests.values())
        first = accepted[0]
        second = accepted[1]
        reserved_before = {
            line: owner.reserved_mask
            for line, owner in scheduler.owners.items()
        }

        forged_id = MODULE.SourceResponse(
            MODULE.MAX_REQUEST_ID,
            scheduler.generation,
            first.source_line,
            scheduler._source_payload(first.source_line),
        )
        forged_line = MODULE.SourceResponse(
            first.request_id,
            scheduler.generation,
            first.source_line + 1,
            scheduler._source_payload(first.source_line + 1),
        )
        self.assertTrue(scheduler.inject_source_response(forged_id))
        self.assertTrue(scheduler.deliver_source_response())
        self.assertTrue(scheduler.inject_source_response(forged_line))
        self.assertTrue(scheduler.deliver_source_response())
        self.assertEqual(
            reserved_before,
            {
                line: owner.reserved_mask
                for line, owner in scheduler.owners.items()
            },
        )
        self.assertIn(first.request_id, scheduler.accepted_source_requests)

        # Deliver the second legitimate response ahead of the first.
        second_response = MODULE.SourceResponse(
            second.request_id,
            second.generation,
            second.source_line,
            scheduler._source_payload(second.source_line),
        )
        self.assertTrue(scheduler.inject_source_response(second_response))
        self.assertTrue(scheduler.deliver_source_response())
        self.assertNotIn(second.request_id, scheduler.accepted_source_requests)
        duplicate = MODULE.SourceResponse(
            second.request_id,
            second.generation,
            second.source_line,
            scheduler._source_payload(second.source_line),
        )
        self.assertTrue(scheduler.inject_source_response(duplicate))
        self.assertTrue(scheduler.deliver_source_response())
        first_response = MODULE.SourceResponse(
            first.request_id,
            first.generation,
            first.source_line,
            scheduler._source_payload(first.source_line),
        )
        self.assertTrue(scheduler.inject_source_response(first_response))
        self.assertTrue(scheduler.deliver_source_response())
        self.assertEqual(scheduler.stale_source_responses, 2)
        self.assertEqual(scheduler.forged_source_responses, 1)
        metrics = scheduler.run()
        self.assertEqual(metrics["rejected_source_responses"], 3)
        self.assertEqual(metrics["payload_oracle_exact_once_failures"], 0)

    def test_injected_responses_cannot_overbook_combined_source_credits(self):
        config = self.small_config(
            logical_elements=8,
            page_elements=8,
            source_request_slots=4,
            source_response_slots=2,
        )
        scheduler = MODULE.CorrectedHybridScheduler(
            [line * 8 for line in range(8)], config=config, generation=19
        )
        forged = MODULE.SourceResponse(999, 19, 0, (0,) * 8)
        self.assertTrue(scheduler.inject_source_response(forged))
        self.assertTrue(scheduler.inject_source_response(forged))
        self.assertTrue(scheduler.issue_source_request())
        self.assertTrue(scheduler.issue_source_request())
        self.assertFalse(scheduler.issue_source_request())
        self.assertEqual(
            len(scheduler.source_requests)
            + len(scheduler.accepted_source_requests),
            config.source_response_slots,
        )
        self.assertFalse(scheduler.accept_source_request())
        self.assertTrue(scheduler.deliver_source_response())
        self.assertTrue(scheduler.accept_source_request())
        self.assertEqual(
            len(scheduler.source_requests)
            + len(scheduler.accepted_source_requests),
            config.source_response_slots,
        )
        self.assertFalse(scheduler.issue_source_request())
        scheduler.assert_invariants()
        metrics = scheduler.run()
        self.assertEqual(metrics["source_request_issues"], 8)
        self.assertEqual(metrics["payload_oracle_exact_once_failures"], 0)

    def test_row_burst_expiry_rotates_before_same_minimum_row(self):
        config = self.small_config(
            logical_elements=8,
            page_elements=8,
            source_request_slots=4,
            source_response_slots=4,
            row_burst=1,
        )
        pattern = [0, 16, 8, 24, 32, 40, 48, 56]
        scheduler = MODULE.CorrectedHybridScheduler(
            pattern,
            live=[True, True, True, False, False, False, False, False],
            config=config,
        )
        self.assertTrue(scheduler.issue_source_request())
        self.assertTrue(scheduler.issue_source_request())
        self.assertTrue(scheduler.issue_source_request())
        self.assertEqual(
            [request.source_line for request in scheduler.source_requests],
            [0, 1, 2],
        )
        self.assertEqual(scheduler.row_rotations, 2)

    def test_payload_oracle_rejects_corruption_without_partial_mutation(self):
        scheduler = MODULE.CorrectedHybridScheduler(
            [7, 6, 5, 4, 3, 2, 1, 0],
            config=self.small_config(logical_elements=8, page_elements=8),
            generation=23,
        )
        self.assertTrue(scheduler.issue_source_request())
        self.assertTrue(scheduler.accept_source_request(auto_respond=False))
        request = next(iter(scheduler.accepted_source_requests.values()))
        payload = scheduler._source_payload(request.source_line)
        corrupt = MODULE.SourceResponse(
            request.request_id,
            request.generation,
            request.source_line,
            tuple(reversed(payload)),
        )
        reserved_before = next(iter(scheduler.owners.values())).reserved_mask
        self.assertTrue(scheduler.inject_source_response(corrupt))
        self.assertTrue(scheduler.deliver_source_response())
        self.assertEqual(
            next(iter(scheduler.owners.values())).reserved_mask,
            reserved_before,
        )
        self.assertEqual(scheduler.destination_receive_counts, [0] * 8)
        legitimate = MODULE.SourceResponse(
            request.request_id,
            request.generation,
            request.source_line,
            payload,
        )
        self.assertTrue(scheduler.inject_source_response(legitimate))
        self.assertTrue(scheduler.deliver_source_response())
        metrics = scheduler.run()
        self.assertEqual(scheduler.destination_receive_counts, [1] * 8)
        self.assertEqual(
            scheduler.destination_values,
            scheduler.expected_destination_values,
        )
        self.assertEqual(metrics["forged_source_responses"], 1)
        self.assertEqual(metrics["payload_oracle_live_words_verified"], 8)
        self.assertEqual(metrics["payload_oracle_exact_once_failures"], 0)

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
        components = state["components_bits"]
        for component in (
            "configuration_image_bits",
            "live_predicate_bits",
            "exact_live_mask_table_bits",
            "destination_line_directory_bits",
            "source_target_directory_bits",
            "source_pending_directory_bits",
            "owner_metadata_bits",
            "owner_payload_bits",
            "source_request_queue_bits",
            "accepted_source_ledger_bits",
            "source_response_event_queue_bits",
            "write_request_queue_bits",
            "write_ack_queue_bits",
            "focus_row_structure_bits",
            "focus_membership_bits",
            "selector_state_bits",
            "identity_state_bits",
            "ordering_observer_state_bits",
            "functional_work_accounting_bits",
            "execution_event_counter_bits",
            "high_water_observer_bits",
            "payload_oracle_observer_bits",
        ):
            self.assertGreater(components[component], 0)
        self.assertEqual(state["widths"]["source_line_bits"], 18)
        self.assertEqual(state["widths"]["generation_bits"], 64)
        self.assertEqual(state["widths"]["request_id_bits"], 64)
        self.assertEqual(
            state["bit_packed_finite_ledger_bits"], sum(components.values())
        )
        self.assertEqual(
            state["bit_packed_finite_ledger_bits"],
            state["bit_packed_policy_state_bits"]
            + state["bit_packed_replay_observer_state_bits"],
        )
        self.assertEqual(
            state["finite_replay_model_bits"],
            state["hardware_policy_state_bits"]
            + state["replay_evidence_observer_state_bits"],
        )
        self.assertEqual(
            state["bit_packed_finite_ledger_bytes"],
            MODULE.ceil_div(state["bit_packed_finite_ledger_bits"], 8),
        )

    def test_persistent_field_inventory_matches_scheduler_and_record_schemas(
        self,
    ):
        inventory = MODULE.persistent_field_inventory()
        fields = [entry["field"] for entry in inventory]
        self.assertEqual(len(fields), len(set(fields)))

        tree = ast.parse(MODULE_PATH.read_text())
        scheduler_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == "CorrectedHybridScheduler"
        )
        assigned = set()
        for node in ast.walk(scheduler_class):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            elif isinstance(node, ast.AugAssign):
                targets = [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    assigned.add(target.attr)
        prefix = "CorrectedHybridScheduler."
        inventory_roots = {
            entry["field"]
            .removeprefix(prefix)
            .split(".", 1)[0]
            .split("[", 1)[0]
            for entry in inventory
        }
        self.assertEqual(assigned, inventory_roots)

        schemas = (
            ("config", MODULE.ReplayConfig, "ReplayConfig"),
            ("owners[]", MODULE.LineOwner, "LineOwner"),
            ("source_requests[]", MODULE.SourceRequest, "SourceRequest"),
            (
                "accepted_source_requests[]",
                MODULE.SourceRequest,
                "SourceRequest",
            ),
            ("source_responses[]", MODULE.SourceResponse, "SourceResponse"),
            ("write_requests[]", MODULE.WriteRequest, "WriteRequest"),
            ("write_acks[]", MODULE.WriteRequest, "WriteRequest"),
        )
        for root, record_type, type_name in schemas:
            schema_prefix = f"{prefix}{root}.{type_name}."
            if root == "config":
                schema_prefix = f"{prefix}{root}."
            accounted = {
                field.removeprefix(schema_prefix)
                for field in fields
                if field.startswith(schema_prefix)
            }
            self.assertEqual(accounted, set(record_type.__dataclass_fields__))

        scheduler = MODULE.CorrectedHybridScheduler(
            list(range(8)), config=self.small_config(logical_elements=8)
        )
        self.assertEqual(
            set(scheduler.work_counts), set(MODULE.WORK_COUNTER_NAMES)
        )
        self.assertEqual(
            set(scheduler.high_water), set(MODULE._HIGH_WATER_FIELDS)
        )

    def test_persistent_field_inventory_classifies_counters_and_subtotals(
        self,
    ):
        state = MODULE.corrected_state_lower_bound(self.small_config())
        inventory = state["persistent_field_inventory"]
        by_field = {entry["field"]: entry for entry in inventory}
        self.assertEqual(len(by_field), len(inventory))
        self.assertEqual(
            state["persistent_field_inventory_count"], len(inventory)
        )
        self.assertEqual(
            state["hardware_policy_state_field_count"]
            + state["replay_evidence_observer_state_field_count"],
            len(inventory),
        )
        self.assertEqual(
            set(state["components_bits"]),
            set(state["component_classifications"]),
        )
        for entry in inventory:
            self.assertEqual(
                entry["classification"],
                state["component_classifications"][entry["component"]],
            )

        for counter in MODULE._EVENT_COUNTER_FIELDS:
            self.assertEqual(
                by_field[f"CorrectedHybridScheduler.{counter}"][
                    "classification"
                ],
                MODULE.REPLAY_EVIDENCE_OBSERVER_STATE,
            )
        for high_water in MODULE._HIGH_WATER_FIELDS:
            self.assertEqual(
                by_field[f"CorrectedHybridScheduler.high_water.{high_water}"][
                    "classification"
                ],
                MODULE.REPLAY_EVIDENCE_OBSERVER_STATE,
            )
        self.assertEqual(
            by_field["CorrectedHybridScheduler.owners[].LineOwner.payload"][
                "classification"
            ],
            MODULE.HARDWARE_POLICY_STATE,
        )

    def test_archived_source_generation_and_id_widths_fail_closed(self):
        config = self.small_config(logical_elements=8, page_elements=8)
        edge_pattern = [MODULE.MAX_SOURCE_LINE * config.words_per_line] * 8
        edge = MODULE.CorrectedHybridScheduler(
            edge_pattern, config=config, generation=MODULE.MAX_GENERATION
        )
        edge.next_source_request_id = MODULE.MAX_REQUEST_ID
        self.assertTrue(edge.issue_source_request())
        with self.assertRaisesRegex(RuntimeError, "ID space exhausted"):
            edge.issue_source_request()
        with self.assertRaisesRegex(ValueError, "18-bit"):
            MODULE.CorrectedHybridScheduler(
                [(MODULE.MAX_SOURCE_LINE + 1) * config.words_per_line] * 8,
                config=config,
            )
        with self.assertRaisesRegex(ValueError, "generation"):
            MODULE.CorrectedHybridScheduler(
                [0] * 8,
                config=config,
                generation=MODULE.MAX_GENERATION + 1,
            )

    def test_all_hidden_transition_work_is_charged_and_bounded(self):
        scheduler = MODULE.CorrectedHybridScheduler(
            [(destination % 4) * 8 for destination in range(32)],
            config=self.small_config(),
        )
        metrics = scheduler.run()
        work = {
            key: value
            for key, value in metrics.items()
            if key.startswith("work_")
        }
        self.assertEqual(metrics["functional_work_total"], sum(work.values()))
        for required in (
            "work_focus_rebuild_source_scans",
            "work_focus_heap_pops",
            "work_sort_input_items",
            "work_sort_comparison_bound",
            "work_reservation_token_walks",
            "work_response_token_prechecks",
            "work_ready_owner_scans",
            "work_write_token_walks",
        ):
            self.assertGreater(work[required], 0)
        self.assertLessEqual(
            metrics["atomic_transition_work_high_water"],
            metrics["atomic_transition_work_limit"],
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
        self.assertEqual(
            hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            "3c4adfe7b06e094b5bb0352369a0a378d6f00b2f1a0d0eab811b0fbc5d1e0077",
        )
        self.assertEqual(artifact["schema"], MODULE.SCHEMA)
        self.assertFalse(artifact["model_scope"]["timing_prediction"])
        self.assertFalse(artifact["model_scope"]["synthesis_or_area_claim"])
        self.assertIn(
            "unavailable",
            artifact["model_scope"]["measurement_domains"]["timing"],
        )
        self.assertEqual(
            artifact["state_contract"]["widths"]["source_line_bits"], 18
        )
        self.assertEqual(artifact["flag"]["case_count"], 14)
        self.assertEqual(artifact["flag"]["max_source_line"], 222112)
        self.assertEqual(
            {case["sha256"] for case in artifact["flag"]["cases"]},
            {
                "9f344be7df05084a33d1675e1cfa29fe60e0aa3740791b9900c74066e5443919",
                "1aea650887ee2e0424a0208039f32bd777886c6c746514fc7945b86b66c9f61c",
                "995cd9c0e9cfc37bdde92220e832162d6a5d5dbf837060c9d3e4cf87818f65ef",
                "5050da44959941078daa859c13420a7e83a9e0e5be2452f506e5f6fd64153cf2",
                "fadee14ce0da8334af2a3bf7d5416fc96bf5d1b5051aa3ed0bce445d71488488",
                "c5bad529c2dd45d23cee0bc10cfe5d109f2a971db1ade90a091a67dff641fe8c",
                "4863bc4ad276c6a7f3021fbd002bcc37d8c7c60b91502d2fd125d63269dfd11f",
                "549f83b4d28063b6240b4e6c1d424ee115142231017f304c26defa40d04ad471",
                "c7f8a957edf689cf92b9bcf14707f8f0ddacbaba6d6242557582a5204f5e274a",
                "82eb717150a0a321554788dac62bcf53b5460f87af1729dc3b72d22f61c8f2d5",
                "e68891544be79a293fe9c35f5209209e1e3d38cefc9403613f06a83f6e3c19a9",
                "dc2a28bfc7be88c1a99c98d8e3548d76bc569bc339abfb54831f71d43c0551e5",
                "b16c0f8aba0bf377d429c054b426683220c9d012817d605b36b901a04a4931ed",
                "5938c8bea649b29380e9f19b2fc70002d91ebcc72d9348dc3e9d8c7fc5cece17",
            },
        )
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
