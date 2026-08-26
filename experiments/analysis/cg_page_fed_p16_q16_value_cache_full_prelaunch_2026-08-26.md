# Full CG page-fed p16/q16 value-cache prelaunch — 2026-08-26

## Scope

This milestone authorizes exactly one candidate-only `CG_NA=150000` restore
of `page_fed_product_soa_jit` with
`--maa_soa_jit_value_cache_enable`. It authorizes no native, cache-off,
predecessor, or direct4 run. The result cannot make a native, direct4,
iso-area, or official NAS-verification claim.

The prospective durable identity is:

- service: `dx100-cg-page-fed-p16q16-value-cache-full-r1.service`;
- fresh raw root:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-value-cache-full-r1`;
- runner: `experiments/scripts/run_cg_page_fed_p16_q16_value_cache_full.py`;
- timeout: none;
- observations: one.

Launch is prohibited unless the runner/tests/milestone are committed and
published, the source worktree is clean, the root is absent, no matching
service/process is live, and all preflight gates pass.

## Frozen authority and comparison boundary

The runner reuses the accepted page-fed gem5
`606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427`
and Ramulator
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
It reflink-copies the accepted full input/header, requiring exactly
992,830,458 bytes and SHA-256
`f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131`.

Numerical correctness is governed only by the immutable tolerant full-CG
successor certificate at
`/data1/nier/dx100-runs/2026-08-26-cg-page-fed-full-reclassification-r1`.
It requires project-local PASS, finite x/z vectors, and relative bounds of
`1e-8` for x/z sum and norm, `1e-3` for rnorm, and `1e-10` for zeta. Raw or
quantized equality and official NAS verification are not claimed.

Only after the candidate independently reaches
`PASS_NUMERICAL_MECHANISM_CORRECT` may the runner read the pinned accepted
cache-off page-fed full stats and compare against `715,387,684,015 simTicks`.
The control stats SHA-256 is
`3b0654de30ea2a1024373d2cf23f98f84b01d96abcf7d6906ea82a4762351c23`.
No control simulation is rerun.

## Fail-closed mechanism contract

The sole guest is compiled with production reductions, `USE_DATA_FROM_FILE`,
four cores, eight tiles/core, 16,384 logical elements, 4,096 physical elements,
and 524,288 B physical SPD payload. The terminal must preserve both
`p16_reorder_preserved=1` and `q16_reorder_preserved=1`.

The external coherent backing must be exactly 524,288 B: 262,144 B virtual-p
plus 262,144 B product backing. Coherent q-index backing and host payload
access must be zero. The exact terminal/stats closure is:

- 10,960 full windows: 8,768 q and 2,192 residual;
- 43,840 page-fed admissions and closes for all 10,960 windows;
- 179,568,640 selected aliases, admitted/index-read/row-write words, and value
  deliveries;
- 57,491 A read issues/responses and write issues/responses;
- 11,223,040 publisher issues, accepts, and WriteResps, with 43,840 terminals;
- zero coherent page-fed index traffic, predicate rejections, epoch drains,
  bounded-global-merge fallbacks, open contexts, and trace artifacts;
- positive value hits, value-read issues strictly below deliveries, exact
  issue/response/fill/cached-response equality, and
  `issues + hits + merged_waiters == deliveries`.

## Provenance and durability

Before simulator execution the runner records compile/checkpoint/restore
commands, source commit/status, the full immutable-artifact ledger, and a
nonterminal manifest. It permits one deferred checkpoint and one restore. It
then requires byte-identical checkpoint and artifact ledgers plus identical
source commit/status before sealing `result.json` and `gate.complete`.

The service must be launched without a timeout and handed off by exact unit,
raw-root, PID, `/proc` start time, and command identity. A process-exit callback
will be registered so completion does not depend on agent token polling.
