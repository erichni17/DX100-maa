#!/usr/bin/env python3

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments" / "analysis"))

import analyze_adaptive_row_buckets as buckets  # noqa: E402

FROZEN_INPUT = Path(
    "/data1/nier/dx100-runs/verified/"
    "2026-07-24-xrage-retirement-cache-cost-57b2ad3/"
    "inputs/benchmark/xrage_20k.json"
)
PHYSICAL_DIAGNOSTIC = Path(
    "/data1/nier/dx100-runs/2026-08-03-virtualization-integration/"
    "bounded-range-4bf5ef5"
)


def by_name(result: dict) -> dict[str, dict]:
    return {policy["name"]: policy for policy in result["policies"]}


class AdaptiveRowBucketTest(unittest.TestCase):
    def test_frozen_xrage_diagnostics_and_policy_gates(self) -> None:
        pattern, evidence = buckets.load_pattern(FROZEN_INPUT)
        result = buckets.analyze(pattern)
        policies = by_name(result)

        self.assertTrue(evidence["frozen_sha256_match"])
        self.assertEqual(
            result["proxy_observation"][
                "unique_source_proxy_lines_full_window"
            ],
            2169,
        )

        iteration = policies["iteration_chunks"]
        self.assertEqual(iteration["pass_populations"], [4096] * 4)
        self.assertEqual(
            iteration["source_line_coalescing"][
                "sum_unique_lines_across_passes"
            ],
            2310,
        )
        self.assertEqual(iteration["status"], "ACCEPT")

        static = policies["static_full_array_range"]
        self.assertEqual(static["pass_populations"], [16384, 0, 0, 0])
        self.assertEqual(static["status"], "REJECT")

        modulo = policies["source_line_modulo"]
        self.assertEqual(modulo["pass_populations"], [4129, 4010, 4122, 4123])
        self.assertEqual(modulo["status"], "REJECT")

        oracle = policies["exact_offline_source_line_quantile"]
        self.assertEqual(oracle["pass_populations"], [4096] * 4)
        self.assertEqual(
            oracle["upper_inclusive_proxy_boundaries"], [36930, 37514, 38101]
        )
        self.assertEqual(
            oracle["source_line_coalescing"]["sum_unique_lines_across_passes"],
            2169,
        )
        self.assertEqual(oracle["status"], "REJECT")
        self.assertIn(
            "offline_oracle_boundary_selection",
            {item["code"] for item in oracle["reject_conditions"]},
        )

        coarse = policies["online_coarse_histogram_radix_range"]
        self.assertEqual(coarse["status"], "ACCEPT")
        self.assertLessEqual(coarse["maximum_pass_population"], 4096)
        self.assertEqual(sum(coarse["pass_populations"]), 16384)
        self.assertEqual(
            coarse["boundary_selection"]["histogram_full_scans"], 3
        )
        self.assertEqual(coarse["policy_state"]["charged_bytes"], 633)
        self.assertEqual(
            coarse["source_line_coalescing"]["sum_unique_lines_across_passes"],
            2169,
        )
        self.assertIsNone(coarse["dram_row_summary"])

    def test_identical_key_skew_terminates_with_stable_occurrence_chunks(
        self,
    ) -> None:
        pattern = [80] * buckets.DEFAULT_LOGICAL_ELEMENTS
        result = buckets.analyze(pattern)
        coarse = by_name(result)["online_coarse_histogram_radix_range"]
        termination = coarse["recursive_split_termination"]
        self.assertTrue(termination["terminated"])
        self.assertEqual(termination["duplicate_fallback_key_count"], 1)
        self.assertEqual(coarse["pass_populations"], [4096] * 4)
        self.assertEqual(coarse["status"], "ACCEPT")
        self.assertTrue(
            all(
                descriptor["stable_duplicate_ordinal"] is not None
                for descriptor in coarse["range_descriptors"]
            )
        )

    def test_state_budget_and_counter_width_fail_closed(self) -> None:
        pattern = list(range(buckets.DEFAULT_LOGICAL_ELEMENTS))
        result = buckets.analyze(pattern, state_budget=100)
        coarse = by_name(result)["online_coarse_histogram_radix_range"]
        self.assertEqual(coarse["status"], "REJECT")
        self.assertIn(
            "selector_state_budget_exceeded",
            {item["code"] for item in coarse["reject_conditions"]},
        )
        with self.assertRaisesRegex(buckets.AnalysisError, "counters"):
            buckets.analyze(pattern, counter_bytes=1)

    def test_physical_trace_requires_hash_input_identity_and_single_base(
        self,
    ) -> None:
        pattern = [3, 9, 3, 20]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "records.jsonl"
            rows = []
            base = 0x1000
            for itr, value in enumerate(pattern):
                a_paddr = base + value * 8
                rows.append(
                    {
                        "schema": buckets.PHYSICAL_SCHEMA,
                        "itr": str(itr),
                        "b_value": str(value),
                        "a_paddr": hex(a_paddr),
                        "a_line_paddr": hex(a_paddr & ~63),
                        "channel": "0",
                        "rank": "0",
                        "bank_group": "1",
                        "bank": str(itr % 2),
                        "row": str(7 + itr // 2),
                    }
                )
            trace.write_text(
                "".join(
                    json.dumps(row, sort_keys=True) + "\n" for row in rows
                ),
                encoding="utf-8",
            )
            digest = hashlib.sha256(trace.read_bytes()).hexdigest()
            validation = root / "validation.json"
            validation.write_text(
                json.dumps(
                    {
                        "schema": buckets.PHYSICAL_SCHEMA,
                        "record_count": len(pattern),
                        "records": {"sha256": digest},
                    }
                ),
                encoding="utf-8",
            )
            physical, evidence = buckets.load_physical_records(
                trace, validation, pattern
            )
            self.assertEqual(len(physical), len(pattern))
            self.assertEqual(
                evidence["status"], "authenticated_matching_input"
            )
            self.assertEqual(evidence["a_base_paddr"], base)

            bad_rows = list(rows)
            bad_rows[2] = {**bad_rows[2], "b_value": "4"}
            trace.write_text(
                "".join(
                    json.dumps(row, sort_keys=True) + "\n" for row in bad_rows
                ),
                encoding="utf-8",
            )
            bad_digest = hashlib.sha256(trace.read_bytes()).hexdigest()
            validation.write_text(
                json.dumps(
                    {
                        "schema": buckets.PHYSICAL_SCHEMA,
                        "record_count": len(pattern),
                        "records": {"sha256": bad_digest},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                buckets.AnalysisError, "does not match input"
            ):
                buckets.load_physical_records(trace, validation, pattern)

    def test_authenticated_physical_grow_policy_crosscheck(self) -> None:
        records, evidence = buckets.load_physical_diagnostic_records(
            PHYSICAL_DIAGNOSTIC / "physical_admission_records.jsonl",
            PHYSICAL_DIAGNOSTIC / "physical_validation.json",
        )
        result = buckets.analyze_physical_grow_diagnostic(records)
        policies = {policy["name"]: policy for policy in result["policies"]}
        self.assertEqual(evidence["record_count"], 16384)
        self.assertEqual(result["unique_grow_values"], 9)
        self.assertEqual(
            [item["population"] for item in result["grow_populations"]],
            [1785, 2058, 2026, 2028, 2026, 2027, 2028, 2026, 380],
        )
        self.assertEqual(result["unique_physical_a_lines"], 9523)

        static = policies["physical_static_four_unsplit_grow_ranges"]
        self.assertEqual(static["pass_populations"], [5869, 4054, 4055, 2406])
        self.assertEqual(static["status"], "REJECT")

        whole = policies["physical_variable_pass_whole_grow_packing"]
        self.assertEqual(
            whole["pass_populations"], [3843, 4054, 4053, 4054, 380]
        )
        self.assertEqual(
            whole["source_line_coalescing"]["sum_unique_lines_across_passes"],
            9523,
        )
        self.assertEqual(
            whole["finite_model_crosscheck"]["capacity_drains"], 0
        )
        self.assertEqual(whole["finite_model_crosscheck"]["epochs"], 5)
        self.assertFalse(
            whole["finite_model_crosscheck"]["gem5_timing_evidence"]
        )

        split = policies["physical_paired_grow_plus_tail_split_four_pass"]
        self.assertEqual(split["pass_populations"], [4096] * 4)
        self.assertEqual(split["tail_occurrence_gaps"], [10, 41, 44, 285])
        self.assertEqual(
            split["source_line_coalescing"]["sum_unique_lines_across_passes"],
            9582,
        )
        self.assertEqual(
            split["finite_model_crosscheck"]["capacity_drains"], 0
        )
        self.assertEqual(split["finite_model_crosscheck"]["epochs"], 4)

        sequential = policies["physical_sequential_iteration_chunks"]
        self.assertEqual(
            sequential["finite_model_crosscheck"]["a_line_requests"], 16384
        )
        self.assertEqual(sequential["finite_model_crosscheck"]["epochs"], 8)
        self.assertEqual(
            sequential["finite_model_crosscheck"]["drain_reasons"][
                "row_slot_limit"
            ],
            4,
        )
        self.assertEqual(
            result["whole_vs_split_replay_delta"][
                "extra_whole_grow_replay_scans"
            ],
            1,
        )

    def test_frozen_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(
                json.dumps([{"kernel": "Gather", "pattern": [0], "count": 1}]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                buckets.AnalysisError, "hash mismatch"
            ):
                buckets.load_pattern(path, logical_elements=1)


if __name__ == "__main__":
    unittest.main()
