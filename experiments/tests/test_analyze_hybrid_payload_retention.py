import unittest

from experiments.analysis.analyze_hybrid_payload_retention import (
    Arrival,
    FallbackRead,
    Identity,
    TraceFormatError,
    parse_events,
    simulate,
)


def lifecycle(arrivals, reads, activation=None):
    return {
        "arrivals": tuple(arrivals),
        "reads": tuple(reads),
        "activation_ticks": activation or {0: 3, 1: 30, 2: 40, 3: 50},
        "clock_ticks": 1,
        "lines_per_page": 8,
    }


class HybridPayloadRetentionTest(unittest.TestCase):
    def test_owner_policy_changes_direct_index_collision_winner(self):
        line_zero = Identity(7, 3, 0)
        line_two = Identity(7, 3, 64)
        base = lifecycle(
            [Arrival(1, line_zero, 0), Arrival(2, line_two, 0)],
            [FallbackRead(4, line_zero, 0), FallbackRead(5, line_two, 0)],
        )
        first = simulate(base, 64, "first_owner_wins")
        latest = simulate(base, 64, "latest_owner_wins")
        self.assertEqual(
            first["per_page"][0]["predicted_fallback_lines_avoided"], 1
        )
        self.assertEqual(
            latest["per_page"][0]["predicted_fallback_lines_avoided"], 1
        )
        # The result is one in both cases, but the retained identity differs;
        # verify it by making the first line the only observed fallback below.
        first_only = simulate(
            lifecycle(
                [Arrival(1, line_zero, 0), Arrival(2, line_two, 0)],
                [FallbackRead(4, line_zero, 0)],
            ),
            64,
            "first_owner_wins",
        )
        latest_only = simulate(
            lifecycle(
                [Arrival(1, line_zero, 0), Arrival(2, line_two, 0)],
                [FallbackRead(4, line_zero, 0)],
            ),
            64,
            "latest_owner_wins",
        )
        self.assertEqual(
            first_only["totals"]["predicted_fallback_lines_avoided"], 1
        )
        self.assertEqual(
            latest_only["totals"]["predicted_fallback_lines_avoided"], 0
        )

    def test_multi_read_cycle_is_reported_not_arbitrated(self):
        identity = Identity(2, 1, 0)
        report = simulate(
            lifecycle(
                [Arrival(1, identity, 0)],
                [
                    FallbackRead(3, identity, 0),
                    FallbackRead(3, Identity(2, 1, 1), 0),
                ],
            ),
            64,
            "first_owner_wins",
        )
        self.assertEqual(report["totals"]["read_port_conflicts"], 1)
        self.assertEqual(
            report["totals"]["matching_reads_blocked_by_read_port_conflict"], 1
        )
        self.assertEqual(
            report["totals"]["predicted_fallback_lines_avoided"], 0
        )

    def test_generation_is_part_of_identity(self):
        report = simulate(
            lifecycle(
                [Arrival(1, Identity(2, 1, 0), 0)],
                [FallbackRead(2, Identity(2, 2, 0), 0)],
            ),
            64,
            "latest_owner_wins",
        )
        self.assertEqual(
            report["totals"]["predicted_fallback_lines_avoided"], 0
        )

    def test_malformed_target_event_fails_closed(self):
        with self.assertRaises(TraceFormatError):
            parse_events(
                ["1: system.maa: event=page_materialization_submit token=2\n"]
            )
        with self.assertRaises(TraceFormatError):
            parse_events(
                ["1: system.maa: event=page_materialization_unknown\n"]
            )


if __name__ == "__main__":
    unittest.main()
