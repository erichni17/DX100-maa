# Full CG page-fed p16/q16 cache-on lane-4 prelaunch (2026-08-26)

## Scope and decision boundary

This milestone authorizes exactly one candidate-only `CG_NA=150000` restore
for `page_fed_product_soa_jit`, with SoA/JIT value retention enabled, 32
active value owners per indirect unit, and four active apply lanes. It does
not authorize a native run, lane-1 rerun, cache-off run, direct4 run, another
candidate, or a full-promotion claim. The runner has no timeout.

Planned durable identity:

- service:
  `dx100-cg-page-fed-p16q16-value-cache-lane4-full-r1.service`;
- fresh raw root:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-value-cache-lane4-full-r1`;
- runner:
  `experiments/scripts/run_cg_page_fed_p16_q16_value_cache_lane4_full.py`.

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
- P16/q16 lane-4 selection authority:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-apply-lanes-na1024-r1`,
  raw-root SHA-256
  `78c38caf27664795e1684d64ef1595140a955253361beb7a199fd25b752734dc`.
  It selects lane 4 as the exact-faster arm and fixes the sole knob delta to
  `maa_soa_jit_apply_lanes`.
- Post-PASS lane-1 full baseline:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-value-cache-full-r1`,
  result SHA-256
  `55d6d3e51779d086d6b435a5d5e9603c7b6a5b5f85483b5a409f0af78dd2ee3f`,
  `simTicks=162849334269`.

The runner records the baseline path and expected pins in its pre-execution
manifest but does not open or compare its result, stats, config, gate, or
certified ledger until the candidate independently reaches
`PASS_NUMERICAL_MECHANISM_CORRECT`.

## Fail-closed candidate gate

The candidate must resolve exactly one each of
`--maa_soa_jit_value_cache_enable`,
`--maa_soa_jit_active_value_owners=32`,
`--maa_soa_jit_apply_lanes=4`, and `--maa_num_tiles_per_core=8`. It must prove:

- `p16_reorder_preserved=1` and `q16_reorder_preserved=1`;
- 10,960 total windows: 8,768 q and 2,192 residual;
- 10,960 virtual-p gathers, 43,840 product pages, 43,840 page-fed admits,
  and 10,960 page-fed closes;
- 179,568,640 product, selected, delivered, admitted, SPD-index-read, and
  row-written words;
- exact publisher issue/accept/WriteResp closure at 11,223,040 and 43,840
  publisher terminals;
- exact A read issue/response and write issue/response closure at 57,491;
- value issue/response/fill/cached-response identity, positive retained hits,
  and `issues + hits + merged = deliveries`;
- zero value, lookahead, and context stalls, zero epoch drains, and zero
  bounded-global-merge fallbacks;
- `IND_SoaJitActiveApplyLanes = 4 * 10960` and
  `IND_SoaJitApplyLaneHighWater = 4 * 10960`, proving every terminal
  instruction reached four-lane same-cycle high water;
- 524,288 B physical SPD and 524,288 B external coherent backing, split into
  262,144 B virtual-p plus 262,144 B product backing, with zero coherent
  q-index backing and zero host payload access; and
- one m5 exit, one ROI close, a zero wrapper exit, final nonempty stats, no
  fatal text, no per-access trace, immutable checkpoint/artifact ledgers, and
  unchanged source commit/status.

Only after those checks pass does the runner verify the frozen lane-1 gate,
result, manifest, certified ledger, raw stats, and resolved lane-1 config. It
then requires exact p/product/q/page-fed terminal identity and exact conserved
counter identity, including all retained-value and cache-port counters, before
computing lane-1/lane-4 `simTicks`.

## Fixed hardware accounting

All configurations already contain four fixed apply-lane owners per indirect
unit. Lane 4 therefore adds zero payload bytes, control bytes, pool bytes, or
ports relative to lane 1. The pinned C++ state accounting is 32 B per lane
owner and 144 B per four-lane pool per indirect unit, or 576 B per MAA across
four indirect units. This is simulator state-layout accounting, not a
synthesis-area claim.

Value retention likewise uses the fixed 128-line owner pool per indirect unit,
with a 32-line active prefix: 32,768 B fixed and 8,192 B active per MAA. No new
value-retention storage or port is attributed to this run.

## Prelaunch validation

Prelaunch validation passed:

- 13/13 new adversarial lane-4 full-run tests;
- 83/83 inherited page-fed p16/q16 lane, full-run, reduction-order,
  full-application, direct4 common-gate, and tolerant-classification tests
  (96/96 combined);
- Python compilation and AST validation;
- Black at the repository's 79-column configuration, isort, pyupgrade, the
  gem5 style checker, whitespace/end-of-file/line-ending checks, added-file
  size, merge-marker, case-conflict, and symlink checks; and
- `git diff --check`.

The final audit must also show an absent output root, no same-name service, no
conflicting live full-CG process, and clean registered worktrees. After exactly
one no-timeout durable launch, the service PID and `/proc` start identity are
published and the session closes without polling the run.
