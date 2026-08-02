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
        self.assertEqual(observer["total_bits"], 17_435)
        self.assertEqual(observer["total_bytes"], 2_180)
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
        with self.assertRaisesRegex(MODULE.ReplayError, "forged"):
            ledger.complete(forged)
        self.assertEqual(ledger.active, good)
        ledger.complete(good)
        ledger.finish()
        with self.assertRaisesRegex(MODULE.ReplayError, "stale"):
            ledger.complete(good)


if __name__ == "__main__":
    unittest.main()
