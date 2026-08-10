# Bounded four-run global merge

This directory contains an executable deterministic screen for a true 4K-active
descriptor design. It preserves the current 16K-informed counted-grow grouping,
forms four row-local sorted runs in a timing-visible logical LLC store, and
merges them with four finite heads by RowTable slice order, DRAM row, physical
A line, and logical iteration. Equal A lines are issued once and every result
is reconstructed at its exact logical iteration.

Regenerate the frozen result:

```sh
python3 experiments/bounded_global_merge_2026_08_10/bounded_global_merge_model.py \
  --trace /data1/nier/worktrees/codex-coordination/sessions/resident-first-spool-20260809-20260809-025450-7603f4d7/evidence/resident_first_descriptor_spool_a0677ed7/matched_matrix/resident_first_4k/physical_admission_records.jsonl \
  --trace-sha256 b0e6b5b4b349815085a6dede6ded45bd6025ef120b3cab7f01a0673c4ba516e8 \
  --output /tmp/bounded-global-merge-results.json
cmp /tmp/bounded-global-merge-results.json \
  experiments/bounded_global_merge_2026_08_10/results.json
```

Run the focused invariant tests:

```sh
python3 -m unittest \
  experiments/bounded_global_merge_2026_08_10/test_bounded_global_merge_model.py -v
```

The model emits structural and traffic evidence, not candidate gem5 timing.
See `report.md` for the gate decision and evidence boundary.
