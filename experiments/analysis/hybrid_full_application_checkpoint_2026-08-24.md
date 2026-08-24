# Hybrid full-application checkpoint (2026-08-24)

## Architecture

The primary design remains a 16K logical Row/Offset reorder scope with 4K
physical SPD tiles. Existing native tile-sweep endpoints are reused; none of
the campaigns below reruns a native arm.

## Accepted today

- CG now computes each 4K page product before publishing into one 16K SoA/JIT
  ADD. This reduced the preceding small hybrid from `6,566,455,483` to
  `6,348,682,603 simTicks` (3.32%) with exact output.
- A page-product-only CG target removes the unused logical scheduler and two
  tiles/core. Physical SPD payload falls from 655,360 to 524,288 bytes.
  Exact small-CG latency is effectively unchanged: `6,344,668,065 simTicks`,
  0.0633% below its immediate predecessor.
- Full-CG and HashJoin runners now disable per-event tracing. The small CG
  trace exceeded 1 GB, so full tracing would have distorted long runs.

Raw CG reports:

- `experiments/analysis/cg_page_product_fusion_live_2026-08-24.md`
- `experiments/analysis/cg_page_product_lane_removal_live_2026-08-24.md`

## Active full gates

| Workload | Unit | Raw root | Phase |
|---|---|---|---|
| NAS CG | `dx100-cg-page-product-full-baf142f7-r1` | `/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-baf142f7-r1` | trace-free full checkpoint |
| NAS IS | `dx100-is-scalar-soa-full-a44aaa60-r5` | `/data1/nier/dx100-runs/2026-08-24-is-scalar-soa-full-a44aaa60-r5` | full O3 ROI |
| HashJoin PRO/PRH | `hashjoin-hybrid-full-fc5f3ea4-20260824-0425` | `/data1/nier/dx100-runs/hashjoin-hybrid-full-fc5f3ea4-20260824-0425` | trace-free PRO checkpoint, then PRH |

All units have infinite runtime and process-exit watchers under
`/data1/nier/.dx-runtime-state`. An exit observation is not success; each raw
root still requires its terminal marker, exact correctness result, final
stats, balanced response ledgers, and hashes.

## Active optimization probes

- CG pre-A off/on pair:
  `/data1/nier/dx100-runs/2026-08-24-cg-page-product-pre-a-pair-baf142f7-r1`.
  The two trace-free restores share the accepted eight-lane checkpoint and
  differ only by row-directed pre-A value lookahead.
- Generic old-result write coalescing: commits `f153dfaa`, `c0cb4414`, and
  `21e1a7ac` retain the existing eight-line, 1,128-byte per-unit buffer. Under
  pressure, at most one partial line is in flight; the densest line is chosen,
  while full-line writes remain concurrent. Unit and sanitizer tests pass.
  The isolated exact gem5 rebuild is still in progress; no performance result
  exists yet.

## Resume order

1. Classify the CG pre-A pair; keep it only if exact work is unchanged and
   treatment ticks improve.
2. Finish the old-result build, archive/hash the exact binary, run the exact
   old-result smoke, then the small SSSP application only after the smoke.
3. On each full-service exit, validate correctness before comparing first-ROI
   `simTicks` to the frozen physical tile sweep.
4. Update this checkpoint with accepted results or explicit rejections; do not
   infer speedups from live, incomplete, or final-post-ROI timing alone.
