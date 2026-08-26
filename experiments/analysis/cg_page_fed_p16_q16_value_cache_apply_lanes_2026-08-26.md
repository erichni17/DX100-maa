# Cache-on page-fed p16/q16 apply-lane A/B — 2026-08-26

## Scope and gate

This is a bounded, fail-closed A/B between `--maa_soa_jit_apply_lanes=1` and
`=4`, using one `page_fed_product_soa_jit` guest and one deferred checkpoint
per size. Both arms retain p16 and q16 reordering, use four cores/eight
tiles per core, 524,288 B SPD, 524,288 B external backing (262,144 B each of
virtual-p and product), zero q-index backing/host payload/fallback/drain, and
the cache-on 32-owner active prefix of a fixed 128-owner pool.

The required first size is `CG_NA=256`. `CG_NA=1024` is forbidden unless the
terminal screen has byte-identical raw/quantized fingerprints and all 11
deterministic-reduction records, exact p-gather/product/q/A/value/publisher/
page-fed ledgers, lane-4 high-water of exactly four per instruction, and lane
4 is strictly lower in `simTicks`. The only normalized command/config delta is
`maa_soa_jit_apply_lanes`; the fixed four-owner lane pool therefore adds zero
incremental bytes, control bytes, or ports.

No native or full-CG simulation is permitted.

## Direct4 reconciliation boundary

The runner independently rehashes the accepted direct4 cache-on lane screen at
`/data1/nier/worktrees/codex-coordination/sessions/hybrid-q16-retained-apply-lanes-20260826-20260826-131017-d5eb8ebb/evidence/direct4-q16-apply-lanes-na256-r1`.
That evidence confirms the fixed-pool accounting and apply-lane mechanism, but
its p16 reorder is false. It must not be used to attribute p-stage timing in
this p16-preserving experiment.

## Result

Pending the required `CG_NA=256` screen. This report will record either the
accepted raw-root hash (and the conditional 1024 confirmation, if authorized)
or the rejected screen root. Neither outcome promotes a native/full result.
