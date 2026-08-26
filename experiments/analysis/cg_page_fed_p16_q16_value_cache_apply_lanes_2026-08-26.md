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

**ACCEPT lane 4 for the bounded cache-on p16/q16 configuration.** Both accepted
roots have exact raw/quantized fingerprints, all 11 deterministic reductions,
zero wrapper/restore failures, immutable artifact/checkpoint ledgers, and the
required full mechanism closure.

Accepted roots:

- `CG_NA=256`:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-apply-lanes-na256-r5`,
  raw ledger `6e90f5ae...aebf`;
- `CG_NA=1024`:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-apply-lanes-na1024-r1`,
  raw ledger `78c38caf...4dc`.

| CG_NA | Lane 1 `simTicks` | Lane 4 `simTicks` | Lane 1 / lane 4 | Lower latency |
|---:|---:|---:|---:|---:|
| 256 | 422,359,383 | 399,729,170 | 1.056613864x | 5.3580% |
| 1024 | 2,363,188,186 | 2,282,467,051 | 1.035365739x | 3.4158% |

At 256, lane 4 reduces INDRMW cycles from 323,512 to 254,384, request cycles
from 422,491 to 355,400, and context stalls from 5,098 to 1,608. At 1024,
context stalls are already zero in both arms, but INDRMW/request cycles still
fall from 2,071,657/2,739,256 to 1,797,897/2,464,848. Exact lane high-water is
1 versus 4 in every operation (`10/40` at 256 and `65/260` at 1024).

All logical and traffic ledgers are conserved: p16/q16 windows, selected/value
deliveries, A reads/writes, product publisher traffic, page-fed admissions,
cache reads/writes, and zero drains/fallbacks. Context stalls are deliberately
reported as a performance/backpressure effect, not a conserved work counter.
The fixed four-lane pool is present in both arms, so lane 4 adds zero payload,
control bytes, or ports at this modeled configuration.

Excluded roots:

- `...na256-r2` reached exact arms but its original policy incorrectly treated
  context stalls as conserved work and wrote no result/gate;
- `...na256-r4` reached exact arms but exposed a parser omission before writing
  a result/gate;
- the earlier r1/r3 preflight/duplicate attempts remain non-evidence.

This bounded selection does not authorize a native or full-CG claim. The
running full p16/q16 lane-1 candidate remains a separate baseline observation;
any lane-4 full successor requires its own candidate-only gate.
