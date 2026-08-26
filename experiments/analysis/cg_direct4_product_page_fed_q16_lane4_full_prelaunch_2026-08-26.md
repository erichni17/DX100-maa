# Full CG direct4/q16 cache-on lane-4 prelaunch (2026-08-26)

## Scope and decision boundary

This milestone authorizes exactly one candidate-only `CG_NA=150000` restore
for `direct4_product_page_fed_q16`, with SoA/JIT value retention enabled, 32
active value owners per indirect unit, and four active apply lanes. It does not
authorize a native run, a lane-1 rerun, a cache-off run, another candidate, or
a full-performance/promotion claim. The runner has no timeout.

Planned durable identity:

- service: `dx100-cg-direct4-product-page-fed-q16-lane4-full-r1.service`;
- fresh raw root:
  `/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-lane4-full-r1`;
- runner: `experiments/scripts/run_cg_direct4_product_page_fed_q16_lane4_full.py`.

## Frozen inputs and authorities

- Full header:
  `/data1/nier/dx100-runs/2026-08-25-cg-page-fed-application-full-31c00be8-r2/input/cg_data_4C.h`,
  992,830,458 B, SHA-256
  `f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131`.
- Page-fed gem5 SHA-256:
  `606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427`.
- Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
- Tolerant full numerical authority:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-full-reclassification-r1`.
  Its pinned verdict is `PASS_NUMERICAL_MECHANISM_CORRECT`; it does not claim
  raw/quantized equality or official NAS verification.
- Lane-4 selection authority:
  `/data1/nier/worktrees/codex-coordination/sessions/hybrid-q16-retained-apply-lanes-20260826-20260826-131017-d5eb8ebb/evidence/direct4-q16-apply-lanes-na1024-r1`,
  raw-root SHA-256
  `a6834f858bca1b1db0c22a341c6eda180b6549d9075039e802ddd51a26b901c4`.
  It names lane 4 as an exact-faster arm and fixes the sole knob delta to
  `maa_soa_jit_apply_lanes`.
- Post-PASS lane-1 full baseline:
  `/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-value-cache-full-r2`,
  result SHA-256
  `270edfd1d868c5fcd257582136e04f4da679dc0a0119efa75d39eeaa797068a4`,
  `simTicks=123968991971`.

The runner records the baseline path and expected pins in its pre-execution
manifest but does not open or compare the baseline result, stats, config, or
gate until the candidate independently reaches
`PASS_NUMERICAL_MECHANISM_CORRECT`.

## Fail-closed candidate gate

The candidate must resolve exactly one each of
`--maa_soa_jit_value_cache_enable`,
`--maa_soa_jit_active_value_owners=32`,
`--maa_soa_jit_apply_lanes=4`, and `--maa_num_tiles_per_core=8`.
It must prove:

- `p16_reorder_preserved=0`, `q16_reorder_preserved=1`;
- 10,960 total windows, including 8,768 q windows and 2,192 residual windows;
- 43,840 product publisher pages and q-index admissions;
- 179,568,640 selected, delivered, admitted, SPD-read, and row-written words;
- exact publisher issue/accept/WriteResp closure at 11,223,040 and 43,840
  publisher terminals;
- exact A read issue/response and write issue/response closure at 57,491;
- value issue/response/fill/cached-response identity, positive retained hits,
  and `issues + hits + merged = deliveries`;
- zero value, lookahead, and context stalls, zero epoch drains, and zero
  bounded-global-merge fallbacks;
- `IND_SoaJitActiveApplyLanes = 4 * 10960` and
  `IND_SoaJitApplyLaneHighWater = 4 * 10960`, proving all terminal
  instructions reached four-lane same-cycle high water;
- 524,288 B physical SPD, 262,144 B external product backing, zero virtual-p
  backing, zero coherent q-index backing, and zero host payload access; and
- one m5 exit, one ROI close, a zero wrapper exit, final nonempty stats, no
  fatal text, no per-access trace, immutable checkpoint/artifact ledgers, and
  unchanged source commit/status.

Only after those checks pass does the runner verify the frozen lane-1 gate,
result, manifest, certified ledger, raw stats, and resolved lane-1 config. It
then requires exact terminal and conserved-counter identity, including value
retention and cache-port traffic, before computing lane-1/lane-4 `simTicks`.

## Fixed hardware accounting

All configurations already contain four fixed apply-lane owners per indirect
unit. The selected setting therefore has zero incremental payload bytes,
control bytes, ports, or apply-lane-pool bytes relative to lane 1. The pinned
C++ state accounting is 32 B per lane owner and 144 B per four-lane pool per
indirect unit, or 576 B per MAA across four indirect units. This is simulator
state-layout accounting, not a synthesis area claim.

Value retention likewise uses the fixed 128-line owner pool per indirect unit,
with a 32-line active prefix: 32,768 B fixed and 8,192 B active per MAA. No
new value-retention storage or port is attributed to this run.

## Prelaunch validation

Prelaunch validation passed:

- 13/13 new adversarial lane-4 full-run tests;
- 68/68 inherited direct4/q16, p16/q16 full, reduction-order,
  full-application, and tolerant-classification tests (81/81 combined);
- Python byte compilation and AST validation;
- Black at the repository's 79-column configuration, isort, pyupgrade, the
  gem5 style checker, whitespace/end-of-file/line-ending checks, added-file
  size, merge-marker, case-conflict, and symlink checks; and
- `git diff --check`.

The final launch audit must also show an absent output root, no same-name
service, no conflicting live full-CG process, and a clean registered worktree.
The durable service PID and `/proc` start identity are published immediately
after the one launch; the session then closes without polling the run.
