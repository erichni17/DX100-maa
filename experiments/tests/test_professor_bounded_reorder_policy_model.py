import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "experiments/analysis/professor_bounded_reorder_policy_model.py"
)
SPEC = importlib.util.spec_from_file_location(
    "professor_bounded_reorder_policy_model", MODULE_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

XRAGE = Path("/data1/nier/DX100/experiments/inputs/xrage_gather0_full.json")
FLAG_ROOT = Path(
    "/data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/"
    "benchmarks/spatter/tests/test-data/lanl/flag"
)


def require_fixture(path: Path) -> None:
    if not path.exists():
        raise unittest.SkipTest(f"frozen fixture is unavailable: {path}")


class ProfessorBoundedReorderPolicyModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        require_fixture(XRAGE)
        cls.pattern = MODULE.load_gather(XRAGE)[: MODULE.LOGICAL_ELEMENTS]
        cls.analysis = MODULE.analyze_pattern(cls.pattern, 3_585)

    def ledger_state(self, ledger):
        return ledger.generation, ledger.next_serial, ledger.active

    def assert_ledger_rejection_is_atomic(self, ledger, action, regex):
        before = self.ledger_state(ledger)
        with self.assertRaisesRegex(MODULE.ReplayError, regex):
            action()
        self.assertEqual(self.ledger_state(ledger), before)

    def test_source_identities_are_the_frozen_xrage_and_fourteen_flag_files(
        self,
    ) -> None:
        require_fixture(FLAG_ROOT)
        identity = MODULE.verified_input(
            XRAGE, frozenset({MODULE.XRAGE_SHA256})
        )
        self.assertEqual(identity["sha256"], MODULE.XRAGE_SHA256)
        flag = MODULE.verified_flag_paths(FLAG_ROOT)
        self.assertEqual(len(flag), 14)
        self.assertEqual(
            {record["sha256"] for _, record in flag}, MODULE.FLAG_SHA256
        )

    def test_wrong_fixture_identity_fails_closed(self) -> None:
        require_fixture(FLAG_ROOT)
        flag_path = MODULE.verified_flag_paths(FLAG_ROOT)[0][0]
        with self.assertRaisesRegex(MODULE.ReplayError, "unrecognized"):
            MODULE.verified_input(flag_path, frozenset({MODULE.XRAGE_SHA256}))

    def test_exact_state_separates_payload_reorder_and_backing(self) -> None:
        state = MODULE.state_contract()
        self.assertEqual(state["physical_payload"]["bytes"], 524_288)
        policies = state["policies"]
        self.assertEqual(
            policies["row_bucket_rescan"]["reorder_state_bytes"], 66_692
        )
        self.assertEqual(
            policies["row_bucket_rescan"]["on_chip_total_bytes"], 653_142
        )
        self.assertEqual(
            policies["sorted_runs_merge"]["reorder_state_bytes"], 70_109
        )
        self.assertEqual(
            policies["sorted_runs_merge"]["on_chip_total_bytes"], 656_559
        )
        self.assertEqual(
            policies["range_spool_replay"]["added_spool_state_bytes"], 205
        )
        self.assertEqual(
            policies["range_spool_replay"]["on_chip_total_bytes"], 653_343
        )
        observer = state["replay_observer_state"]
        self.assertEqual(observer["metrics_counter_fields"], 16)
        self.assertEqual(observer["admitted_b_snapshot_entries"], 16_384)
        self.assertEqual(observer["admitted_b_snapshot_bits_per_entry"], 21)
        self.assertEqual(observer["admitted_b_snapshot_bits"], 344_064)
        self.assertEqual(observer["total_bits"], 361_499)
        self.assertEqual(observer["total_bytes"], 45_188)
        exhaustion = state["serial_exhaustion_contract"]
        self.assertEqual(exhaustion["next_serial_bits"], 64)
        self.assertEqual(exhaustion["exhausted_sentinel"], 0)
        self.assertFalse(exhaustion["serial_zero_issued"])
        self.assertEqual(exhaustion["extra_exhaustion_flag_bits"], 0)
        for policy in MODULE.POLICIES:
            self.assertTrue(policies[policy]["within_global_budget"])
        self.assertEqual(
            policies["sorted_runs_merge"]["llc_backing_capacity_bytes"],
            262_144,
        )
        self.assertEqual(
            policies["range_spool_replay"]["llc_backing_capacity_bytes"],
            262_144,
        )

    def test_direct4_matches_corrected_replay_first_tile(self) -> None:
        direct = self.analysis["policies"]["direct4"]
        self.assertEqual(direct["a_requests"], 2_310)
        self.assertEqual(direct["same_row_successors"], 2_266)
        self.assertEqual(direct["successor_pairs"], 2_309)
        self.assertEqual(direct["row_transitions"], 43)
        self.assertEqual(direct["row_runs"], 44)
        self.assertEqual(direct["same_row_successor_rate"], 0.98137722)

    def test_three_policies_are_bounded_and_deterministic(self) -> None:
        replay = MODULE.analyze_pattern(self.pattern, 3_585)
        self.assertEqual(replay, self.analysis)
        for policy in MODULE.POLICIES:
            metrics = self.analysis["policies"][policy]
            self.assertEqual(metrics["logical_words"], MODULE.LOGICAL_ELEMENTS)
            self.assertLessEqual(
                metrics["max_active_records"], MODULE.ACTIVE_RECORDS
            )
            self.assertEqual(metrics["llc_transactions"], metrics["llc_acks"])
            self.assertTrue(
                self.analysis["gate"][policy]["bounded_and_ack_complete"]
            )

    def test_policy_scan_and_spill_traffic_is_fully_charged(self) -> None:
        policies = self.analysis["policies"]
        rescan = policies["row_bucket_rescan"]
        self.assertEqual(rescan["index_scan_words"], 65_536)
        self.assertEqual(rescan["b_scan_bytes"], 262_144)
        self.assertEqual(rescan["spill_record_bytes"], 0)

        merge = policies["sorted_runs_merge"]
        self.assertEqual(merge["index_scan_words"], 16_384)
        self.assertEqual(merge["spill_record_bytes"], 262_144)
        self.assertEqual(merge["llc_write_bytes"], 262_144)
        self.assertEqual(merge["llc_read_bytes"], 262_144)
        self.assertEqual(merge["max_merge_heads"], 4)

        ranges = policies["range_spool_replay"]
        self.assertEqual(ranges["index_scan_words"], 16_384)
        self.assertEqual(ranges["spill_record_bytes"], 262_144)
        self.assertEqual(ranges["llc_write_bytes"], 262_144)
        self.assertEqual(ranges["llc_read_bytes"], 1_048_576)

    def test_gate_uses_strict_request_and_transition_comparisons(self) -> None:
        for policy in MODULE.POLICIES:
            gate = self.analysis["gate"][policy]
            self.assertTrue(gate["strictly_fewer_a_requests"])
            self.assertTrue(gate["strictly_fewer_bank_row_transitions"])
            self.assertTrue(gate["within_global_on_chip_budget"])
            self.assertTrue(gate["pass"])

        direct = MODULE.Metrics(
            a_requests=10,
            same_row_successors=8,
            successor_pairs=9,
            row_transitions=1,
            row_runs=2,
        )
        equal_requests = MODULE.Metrics(
            logical_words=0,
            a_requests=10,
            same_row_successors=7,
            successor_pairs=9,
            row_transitions=2,
            row_runs=3,
        )
        rejected = MODULE.strict_gate(
            "row_bucket_rescan", equal_requests, direct
        )
        self.assertFalse(rejected["pass"])
        self.assertIn("A request count", rejected["reasons"][0])

    def test_gate_uses_absolute_transitions_not_confounded_rate(self) -> None:
        direct = MODULE.Metrics(
            logical_words=1,
            a_requests=100,
            same_row_successors=90,
            successor_pairs=99,
            row_transitions=9,
            row_runs=10,
        )
        candidate = MODULE.Metrics(
            logical_words=1,
            a_requests=80,
            same_row_successors=71,
            successor_pairs=79,
            row_transitions=8,
            row_runs=9,
        )
        self.assertLess(
            candidate.same_row_successors / candidate.successor_pairs,
            direct.same_row_successors / direct.successor_pairs,
        )
        gate = MODULE.strict_gate("row_bucket_rescan", candidate, direct)
        self.assertTrue(gate["pass"])
        self.assertTrue(gate["same_row_successor_rate_is_diagnostic_only"])

    def test_llc_identity_rejects_forgery_without_release(self) -> None:
        ledger = MODULE.AckLedger(generation=7)
        good = ledger.issue(direction=0, line_index=3)
        forged = MODULE.TransferTag(
            good.generation, good.serial + 1, good.direction, good.line_index
        )
        self.assert_ledger_rejection_is_atomic(
            ledger, lambda: ledger.complete(forged), "forged"
        )
        self.assertEqual(ledger.active, good)
        ledger.complete(good)
        ledger.finish()
        self.assert_ledger_rejection_is_atomic(
            ledger, lambda: ledger.complete(good), "stale"
        )

    def test_returned_transfer_tag_does_not_alias_live_identity(self) -> None:
        ledger = MODULE.AckLedger(generation=7)
        caller_tag = ledger.issue(direction=0, line_index=3)
        live_snapshot = ledger.active
        self.assertIsNot(caller_tag, live_snapshot)

        object.__setattr__(caller_tag, "serial", caller_tag.serial + 1)
        self.assertEqual(ledger.active, live_snapshot)
        self.assert_ledger_rejection_is_atomic(
            ledger, lambda: ledger.complete(caller_tag), "forged"
        )
        ledger.complete(live_snapshot)
        ledger.finish()

    def test_coverage_proof_snapshots_caller_pattern(self) -> None:
        caller_pattern = [3]
        proof = MODULE.CoverageProof(caller_pattern)
        caller_pattern[0] = 4
        self.assertIs(type(proof.pattern), tuple)
        self.assertEqual(proof.pattern, (3,))

        before = proof.seen_mask, proof.seen_count
        with self.assertRaisesRegex(MODULE.ReplayError, "lost its B identity"):
            proof.observe(MODULE.make_record(0, 4))
        self.assertEqual((proof.seen_mask, proof.seen_count), before)
        proof.observe(MODULE.make_record(0, 3))
        proof.finish()

    def test_generation_true_is_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.ReplayError, "generation"):
            MODULE.AckLedger(True)

    def test_generation_float_is_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.ReplayError, "generation"):
            MODULE.AckLedger(1.0)

    def test_generation_over_uint64_is_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.ReplayError, "generation"):
            MODULE.AckLedger(1 << 64)

    def test_admitted_generation_is_read_only(self) -> None:
        ledger = MODULE.AckLedger(7)
        before = self.ledger_state(ledger)
        with self.assertRaises(AttributeError):
            ledger.generation = 8
        self.assertEqual(self.ledger_state(ledger), before)

    def test_bool_direction_is_rejected_without_mutation(self) -> None:
        ledger = MODULE.AckLedger(1)
        self.assert_ledger_rejection_is_atomic(
            ledger, lambda: ledger.issue(True, 0), "direction"
        )

    def test_bool_line_index_is_rejected_without_mutation(self) -> None:
        ledger = MODULE.AckLedger(1)
        self.assert_ledger_rejection_is_atomic(
            ledger, lambda: ledger.issue(0, False), "line index"
        )

    def test_uint64_serial_exhaustion_uses_zero_sentinel(self) -> None:
        ledger = MODULE.AckLedger(MODULE.UINT64_MAX)
        ledger.next_serial = MODULE.UINT64_MAX
        final_tag = ledger.issue(1, MODULE.MAX_TRANSFER_LINE)
        self.assertEqual(final_tag.serial, MODULE.UINT64_MAX)
        self.assertEqual(ledger.next_serial, 0)
        self.assertEqual(ledger.active, final_tag)
        ledger.complete(final_tag)

        self.assert_ledger_rejection_is_atomic(
            ledger,
            lambda: ledger.issue(0, 0),
            "serial space is exhausted",
        )
        self.assertEqual(ledger.next_serial, 0)
        ledger.finish()

    def test_bool_source_line_phase_is_rejected_without_mutation(self) -> None:
        pattern = [0]
        with self.assertRaisesRegex(MODULE.ReplayError, "source-line phase"):
            MODULE.analyze_pattern(pattern, True)
        self.assertEqual(pattern, [0])

    def test_all_packed_identity_width_rejections_are_atomic(self) -> None:
        for generation in (0, -1):
            with self.subTest(field="generation", value=generation):
                with self.assertRaisesRegex(MODULE.ReplayError, "generation"):
                    MODULE.AckLedger(generation)

        for field, direction, line_index in (
            ("direction", 1.0, 0),
            ("direction", -1, 0),
            ("direction", 2, 0),
            ("line index", 0, 1.0),
            ("line index", 0, -1),
            ("line index", 0, MODULE.MAX_TRANSFER_LINE + 1),
        ):
            with self.subTest(field=field, value=(direction, line_index)):
                ledger = MODULE.AckLedger(1)
                self.assert_ledger_rejection_is_atomic(
                    ledger,
                    lambda d=direction, line=line_index: ledger.issue(d, line),
                    field,
                )

        valid = MODULE.TransferTag(1, 1, 0, 0)
        invalid_responses = (
            ("generation", True),
            ("generation", 0),
            ("generation", MODULE.UINT64_MAX + 1),
            ("serial", True),
            ("serial", 0),
            ("serial", MODULE.UINT64_MAX + 1),
            ("direction", True),
            ("direction", 1.0),
            ("direction", -1),
            ("direction", 2),
            ("line_index", True),
            ("line_index", 1.0),
            ("line_index", -1),
            ("line_index", MODULE.MAX_TRANSFER_LINE + 1),
        )
        for field, value in invalid_responses:
            with self.subTest(response_field=field, value=value):
                ledger = MODULE.AckLedger(1)
                ledger.issue(0, 0)
                response = MODULE.TransferTag(
                    valid.generation,
                    valid.serial,
                    valid.direction,
                    valid.line_index,
                )
                object.__setattr__(response, field, value)
                self.assert_ledger_rejection_is_atomic(
                    ledger,
                    lambda tag=response: ledger.complete(tag),
                    "response",
                )

        for source_line in (True, 1.0, -1, MODULE.MAX_SOURCE_LINE + 1):
            with self.subTest(field="source_line", value=source_line):
                with self.assertRaisesRegex(MODULE.ReplayError, "source line"):
                    MODULE.bank_row_key(source_line, 0)
        for phase in (1.0, -1, MODULE.MAX_SOURCE_LINE + 1):
            with self.subTest(field="source_line_phase", value=phase):
                pattern = [0]
                with self.assertRaisesRegex(
                    MODULE.ReplayError, "source-line phase"
                ):
                    MODULE.analyze_pattern(pattern, phase)
                self.assertEqual(pattern, [0])
        for source_index in (
            True,
            1.0,
            -1,
            MODULE.MAX_SOURCE_INDEX + 1,
        ):
            with self.subTest(field="source_index", value=source_index):
                pattern = [source_index]
                with self.assertRaisesRegex(
                    MODULE.ReplayError, "gather source index"
                ):
                    MODULE.analyze_pattern(pattern, 0)
                self.assertEqual(pattern, [source_index])

        invalid_records = (
            [0, 0, 0],
            (True, 0, 0),
            (1.0, 0, 0),
            (-1, 0, 0),
            (MODULE.MAX_SOURCE_LINE + 1, 0, 0),
            (0, True, 0),
            (0, 1.0, 0),
            (0, -1, 0),
            (0, MODULE.WORDS_PER_LINE, 0),
            (0, 0, True),
            (0, 0, 1.0),
            (0, 0, -1),
            (0, 0, MODULE.LOGICAL_ELEMENTS),
        )
        for record in invalid_records:
            with self.subTest(field="record", value=record):
                proof = MODULE.CoverageProof([0])
                before = proof.seen_mask, proof.seen_count
                with self.assertRaises(MODULE.ReplayError):
                    proof.observe(record)
                self.assertEqual((proof.seen_mask, proof.seen_count), before)

    def test_maximum_valid_identities_and_record_values_succeed(self) -> None:
        ledger = MODULE.AckLedger(MODULE.UINT64_MAX)
        ledger.next_serial = MODULE.UINT64_MAX
        tag = ledger.issue(1, MODULE.MAX_TRANSFER_LINE)
        self.assertEqual(
            tag,
            MODULE.TransferTag(
                MODULE.UINT64_MAX,
                MODULE.UINT64_MAX,
                1,
                MODULE.MAX_TRANSFER_LINE,
            ),
        )
        ledger.complete(tag)
        ledger.finish()

        record = MODULE.make_record(
            MODULE.LOGICAL_ELEMENTS - 1, MODULE.MAX_SOURCE_INDEX
        )
        self.assertIs(type(record), tuple)
        self.assertEqual(
            record,
            (
                MODULE.MAX_SOURCE_LINE,
                MODULE.WORDS_PER_LINE - 1,
                MODULE.LOGICAL_ELEMENTS - 1,
            ),
        )
        runs = MODULE.build_sorted_runs([MODULE.MAX_SOURCE_INDEX], 0)
        self.assertTrue(
            all(type(item) is tuple for run in runs for item in run)
        )

        captured_spool_records = []
        original_issue_sorted_window = MODULE.issue_sorted_window

        def capture_sorted_window(records, observer):
            captured_spool_records.extend(records)
            return original_issue_sorted_window(records, observer)

        MODULE.issue_sorted_window = capture_sorted_window
        try:
            MODULE.replay_range_spool([MODULE.MAX_SOURCE_INDEX], 0, 1)
        finally:
            MODULE.issue_sorted_window = original_issue_sorted_window
        self.assertTrue(captured_spool_records)
        self.assertTrue(
            all(type(item) is tuple for item in captured_spool_records)
        )

        self.assertEqual(
            MODULE.analyze_pattern([0], MODULE.MAX_SOURCE_LINE)[
                "max_mapped_source_line"
            ],
            MODULE.MAX_SOURCE_LINE,
        )


if __name__ == "__main__":
    unittest.main()
