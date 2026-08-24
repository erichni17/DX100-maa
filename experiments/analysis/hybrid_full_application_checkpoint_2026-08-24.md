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
- CG pre-A lookahead is a valid near-flat result, not a promoted optimization:
  exact first-ROI ticks changed from `6,344,668,065` to `6,341,118,332`
  (0.055948% lower). The option remains default-off and is retained for its
  prior full-GZP benefit.

Raw CG reports:

- `experiments/analysis/cg_page_product_fusion_live_2026-08-24.md`
- `experiments/analysis/cg_page_product_lane_removal_live_2026-08-24.md`
- `experiments/analysis/cg_page_product_pre_a_ablation_2026-08-24.md`

## Active full gates

| Workload | Unit | Raw root | Phase |
|---|---|---|---|
| NAS CG | `dx100-cg-page-product-full-baf142f7-r1` | `/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-baf142f7-r1` | trace-free full checkpoint |
| NAS IS | `dx100-is-scalar-soa-full-a44aaa60-r5` | `/data1/nier/dx100-runs/2026-08-24-is-scalar-soa-full-a44aaa60-r5` | full O3 ROI |
| HashJoin PRH | `dx100-hashjoin-prh-full-recovery-20260824-061147` | `/data1/nier/dx100-runs/hashjoin-hybrid-prh-full-d7d29bf5-20260824-061147` | PRH-only recovery; PRO is not rerun |
| GAPBS SSSP S22 | `dx100-sssp-old-result-full-s22-aa41bdd7-r1` | `/data1/nier/dx100-runs/2026-08-24-sssp-old-result-full-s22-aa41bdd7-r1` | candidate guest/input checkpoint preparation |

All units have infinite runtime and process-exit watchers under
`/data1/nier/.dx-runtime-state`. An exit observation is not success; each raw
root still requires its terminal marker, exact correctness result, final
stats, balanced response ledgers, and hashes.

## Active optimization probes

- Generic old-result write coalescing commits `f153dfaa`, `c0cb4414`, and
  `21e1a7ac` retain the existing eight-line, 1,128-byte per-unit buffer. The
  exact binary is archived as SHA-256 `36ed7d5c...a3ec9f`.
- The one-partial-write policy is rejected. On the frozen sparse old-result
  checkpoint, writes fell from 11,399 to 10,165 (10.83%) and packing rose from
  2.225 to 2.496 useful words/write, but first-ROI latency regressed from
  `687,827,203` to `733,637,257 simTicks` (6.66%). SSSP was not launched.
- The matched pressure sweep selected dense/four: `686,432,788 simTicks` and
  9,491 writes versus the exact oldest/eight reproduction at `687,827,203`
  ticks and 11,399 writes. This is 0.202728% lower latency and 16.7383% fewer
  writes with unchanged 512-byte payload and 1,128-byte buffer.
- Dense/four alone reduced small-SSSP writes 33.4061% but was 0.046838% slower.
  Composing the existing value cache, 64 active owners, and pre-A produced a
  replicated exact endpoint at `9,976,182,331 simTicks`, 0.262468% below the
  accepted small candidate, with 52.0055% fewer result writes. No new payload
  is provisioned; contexts remain eight.

Report:
`experiments/analysis/soa_jit_old_result_write_coalescing_2026-08-24.md`.

## HashJoin partial result

Full PRO is terminal and correct at `28,586,786,731` first-ROI ticks with
2,000,000 matches, 240/240 first-pass windows, zero shifted-pass windows,
240/240 SoA terminals, and closed A ledgers. The runner incorrectly required a
nonzero shifted pass for every full kernel and exited before PRH, leaving no
top-level gate. PRO is therefore partial evidence, not a complete HashJoin
result. Relative to frozen native16/native4 endpoints it is 18.5442%/16.4022%
slower; this is end-to-end context, not causal virtualization attribution.

## Resume order

1. On each full-service exit, validate correctness before comparing first-ROI
   `simTicks` to the frozen physical tile sweep.
2. Combine terminal PRH recovery with the preserved full-PRO evidence before
   classifying overall HashJoin.
3. Update this checkpoint with accepted results or explicit rejections; do not
   infer speedups from live, incomplete, or final-post-ROI timing alone.
