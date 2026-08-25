# Hardened full HashJoin results (2026-08-24)

## Decision

Both full kernels are **terminal-valid correctness evidence**. Neither is a
performance promotion: the runners are candidate-only and deliberately contain
no matched baseline arm.

## PRO

- Root: `/data1/nier/dx100-runs/2026-08-24-hashjoin-pro-hardened-r1`.
- Exact cardinality: 2,000,000.
- First pass: 240 eligible / 240 routed logical windows.
- Shifted pass: not applicable, 0/0 as required.
- First ROI: `28,733,601,885 simTicks`.
- SoA/JIT instructions and terminals: 240/240; A read/write ledgers close.
- Full `result_sha256.txt` verifies; service exited zero.

## PRH

- Root: `/data1/nier/dx100-runs/2026-08-24-hashjoin-prh-hardened-r1`.
- Exact cardinality: 2,000,000.
- First pass: 240 eligible / 240 routed logical windows.
- Shifted pass: `tail_only`, 0/0 logical windows. All 1,024 shifted
  partitions remain below 16K and execute 1,024 bounded 4K tail actions.
- First ROI: `46,316,864,226 simTicks`.
- SoA/JIT instructions and terminals: 240/240; A read/write ledgers close.
- Full `result_sha256.txt` verifies; service exited zero.

## Interpretation

The hardened mechanism-status files distinguish real routed coverage from
tail-only or inapplicable phases without inventing work. Both use two memory
channels, 32 RowTable slices, four indirect units, 16K logical reorder, and 4K
physical SPD. No native arm was rerun.

The runner's `results.tsv` records a later stats-window `simTicks`; the
one-shot classifier correctly reports the first ROI values above. Future
runners should write first-window timing directly, but this reporting defect
does not affect correctness or the frozen raw stats.
