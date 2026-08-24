# CG physical-page product fusion live evidence (2026-08-24)

## Status

Accepted as candidate-only small-application correctness and optimization
evidence. The candidate preserves the 16K Row/Offset reorder window and 4K
physical SPD geometry. It does not establish full-CG performance or parity
with native 16K.

- Source: `08a7b2670d7f8640dea4bde5b2b205defcfaa4ad`
- gem5 SHA-256:
  `ef070d16bb1b25668fe80468693dade4eeaf1776a72fbc51d7a9ce070e5af483`
- Raw root:
  `/data1/nier/dx100-runs/2026-08-24-cg-page-product-fusion-small-08a7b267-r2`
- Geometry: 16K logical, 4K physical, two memory channels, four indirect
  units, 32 initial row-table slices
- Native reruns/wall timeout: zero/none

## Mechanism

The preceding page-backed CG path published three 4K arrays per page:
indices, gathered A values, and multiplied products. The optimized path loads
one 4K page of indices and A values into physical SPD, computes the product
with four physical ALU vectors, and publishes only indices and products.
One full 16K SoA/JIT ADD then updates the destination while retaining the 16K
Row/Offset reorder scope.

This removes the intermediate A-value backing array, all logical-page ALU
actions, and four published pages per 16K window. It does not change the
generic Row/Offset or SoA/JIT hardware.

## Exact result

- Candidate: `6,348,682,603 simTicks`.
- Preceding accepted hybrid: `6,566,455,483 simTicks`.
- Improvement: `1.034302058x`, or 3.32% lower simulated latency.
- All 52 q-SpMV and 13 residual-SpMV eligible windows routed; 65/65 SoA/JIT
  terminals completed with zero fallback.
- Publisher traffic fell from 12 to 8 pages per window and from 199,680 to
  133,120 exact response-bearing line writes.
- External coherent backing fell from 1,048,576 to 786,432 bytes.
- The candidate's quantized `x` and `z` hashes exactly match the frozen
  reference; all finite-value and scalar tolerance checks pass.

Raw manifest, result, logs, stats, trace, and hashes are frozen under the raw
root. An independent review found no correctness or evidence-contract issue.

## Limits

The frozen small physical-4K reference is `1,866,403,037 simTicks`, so this
candidate remains about 3.40x slower. The result is an incremental reduction
in hybrid data movement, not a general performance conclusion. Eight
logical-scheduler lanes also remain reserved even though this treatment
records zero logical-page actions; removing that unused payload is the next
iso-function storage experiment.
