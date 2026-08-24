# CG page-product lane removal live evidence (2026-08-24)

## Status

Accepted as candidate-only small-CG correctness and storage evidence. The
page-product treatment uses exactly the eight guest tiles it allocates and
does not enable the logical page scheduler.

- Source: `164470fd3e080631b00421f9c9cd4f7b6dbb1f6c`
- gem5 SHA-256:
  `ef070d16bb1b25668fe80468693dade4eeaf1776a72fbc51d7a9ce070e5af483`
- Raw root:
  `/data1/nier/dx100-runs/2026-08-24-cg-page-product-8lane-small-164470fd-r1`
- Native reruns/wall timeout: zero/none

## Result

The previous accepted page-product candidate provisioned ten 4K tiles per
core because it shared a scheduler-capable build. This treatment-specific
target provisions eight tiles per core and disables that unused scheduler.

- Physical SPD payload falls from 655,360 to 524,288 bytes: 131,072 bytes
  removed.
- Candidate: `6,344,668,065 simTicks`.
- Predecessor: `6,348,682,603 simTicks`.
- Ratio: `1.000632742x`, a 0.0633% latency reduction. Treat this as
  performance-neutral, not a meaningful speedup.
- All 52 q-SpMV and 13 residual-SpMV windows route and complete.
- Publisher writes close at 133,120/133,120; SoA/JIT terminals close at
  65/65 with zero fallback.
- Logical page actions and admits/retires are both zero.
- Raw and quantized CG fingerprints match the frozen reference exactly.

The resolved configuration records eight tiles/core, 16K logical and Offset
capacity, 4K physical tiles, 32 initial row-table slices, and
`logical_tile_page_scheduler=false`. All frozen raw hashes revalidate.

## Boundary

This removes storage from the page-product-only CG target. It does not prove
that every scheduler-capable workload can use eight tiles/core, and it does
not improve the much larger end-to-end gap seen by the small CG hybrid. Full
CG correctness is the next gate; its verbose per-event trace must remain
disabled to avoid distorting the long run.
