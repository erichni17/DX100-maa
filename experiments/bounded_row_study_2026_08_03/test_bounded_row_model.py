#!/usr/bin/env python3

import json
import unittest
from pathlib import Path

from bounded_row_model import (
    ACTIVE_ELEMENTS,
    LOGICAL_ELEMENTS,
    Model,
    TraceSpec,
    compare,
)


class BoundedRowModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.model = Model()

    def test_primary_trace_is_exact_and_bounded(self) -> None:
        spec = TraceSpec.named("tile_consumer_fp64", LOGICAL_ELEMENTS)
        result = self.model.run("bounded_rows", spec)
        self.assertTrue(result.active_bound_respected)
        self.assertLessEqual(result.peak_active_descriptors, ACTIVE_ELEMENTS)
        self.assertEqual(result.response_placements, LOGICAL_ELEMENTS)
        self.assertEqual(result.missing_placements, 0)
        self.assertEqual(result.duplicate_placements, 0)
        self.assertEqual(result.b_passes, 4)
        self.assertEqual(result.b_scan_bytes, 262_144)
        self.assertEqual(result.b_reread_bytes, 196_608)
        self.assertEqual(result.selector_cycles_lower_bound, 4_096)
        self.assertEqual(result.reorder_metadata_lower_bound_bytes, 66_688)
        self.assertEqual(result.mechanism_total_lower_bound_bytes, 653_142)

    def test_all_policies_place_the_same_output(self) -> None:
        for trace in (
            "tile_consumer_fp64",
            "virtual_index_random_fp32",
            "virtual_index_fanout_fp32",
            "virtual_index_same_line_fp32",
            "virtual_index_line_revisit_fp32",
        ):
            spec = TraceSpec.named(trace, LOGICAL_ELEMENTS)
            hashes = {
                self.model.run(policy, spec).output_hash
                for policy in ("native16", "native4k", "bounded_rows")
            }
            self.assertEqual(len(hashes), 1, trace)

    def test_static_row_bounds_do_not_use_trace_histogram(self) -> None:
        spec = TraceSpec.named("tile_consumer_fp64", LOGICAL_ELEMENTS)
        first, last = self.model.decoder.aperture_rows(spec)
        partitions = {
            self.model._row_partition(spec, row)
            for row in range(first, last + 1)
        }
        self.assertEqual(partitions, {0, 1, 2, 3})

    def test_skew_drains_instead_of_overflowing(self) -> None:
        spec = TraceSpec.named("virtual_index_fanout_fp32", LOGICAL_ELEMENTS)
        result = self.model.run("bounded_rows", spec)
        self.assertEqual(result.peak_active_descriptors, ACTIVE_ELEMENTS)
        self.assertEqual(result.capacity_drain_barriers, 3)
        self.assertTrue(result.active_bound_respected)

    def test_primary_trace_gate_is_fail_closed(self) -> None:
        spec = TraceSpec.named("tile_consumer_fp64", LOGICAL_ELEMENTS)
        result = compare(self.model, spec)
        decision = result["decision"]
        self.assertEqual(
            decision["passes_trace_gate"],
            decision["strictly_reduces_a_requests_vs_native4k"]
            and decision["strictly_reduces_row_transitions_vs_native4k"],
        )

    def test_full16_and_bounded4_metadata_ledgers(self) -> None:
        spec = TraceSpec.named("tile_consumer_fp64", LOGICAL_ELEMENTS)
        full = self.model.run("native16", spec)
        bounded = self.model.run("native4k", spec)
        self.assertEqual(full.reorder_metadata_lower_bound_bytes, 254_464)
        self.assertEqual(bounded.reorder_metadata_lower_bound_bytes, 66_688)
        self.assertEqual(
            full.reorder_metadata_lower_bound_bytes
            - bounded.reorder_metadata_lower_bound_bytes,
            187_776,
        )

    def test_committed_summary_matches_executable_model(self) -> None:
        summary = json.loads(
            (
                Path(__file__).resolve().parent / "results_summary.json"
            ).read_text()
        )
        columns = summary["columns"]
        observed = {
            (row[0], row[1]): dict(zip(columns, row))
            for row in summary["rows"]
        }
        for trace in {row[0] for row in summary["rows"]}:
            spec = TraceSpec.named(trace, LOGICAL_ELEMENTS)
            for policy in ("native16", "native4k", "bounded_rows"):
                run = self.model.run(policy, spec)
                row = observed[(trace, policy)]
                self.assertEqual(row["a_line_requests"], run.a_line_requests)
                self.assertEqual(row["row_transitions"], run.row_transitions)
                self.assertEqual(row["epochs"], run.epochs)
                self.assertEqual(
                    row["capacity_drains"], run.capacity_drain_barriers
                )
                self.assertEqual(row["b_scan_bytes"], run.b_scan_bytes)
                self.assertEqual(row["b_reread_bytes"], run.b_reread_bytes)
                self.assertEqual(
                    row["reorder_metadata_bytes"],
                    run.reorder_metadata_lower_bound_bytes,
                )
                self.assertEqual(row["output_hash"], run.output_hash)
                self.assertEqual(
                    row["issue_order_sha256"], run.issue_order_sha256
                )


if __name__ == "__main__":
    unittest.main()
