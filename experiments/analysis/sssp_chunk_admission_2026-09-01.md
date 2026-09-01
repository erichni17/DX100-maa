# SSSP per-chunk admission gate

## Decision

**ACCEPT for small-graph mechanism correctness; do not promote performance or
full S22 yet.** The per-chunk successor preserves safe logical 16K windows
without allowing an active-source or cross-owner hazard in one chunk to poison
unrelated chunks. All three candidate-only gates use gem5 SHA-256
`45206b3433449e10b26bbd8ff32281c06e533c101213097a27d50c364ca3c267`,
launch no native arm, use no wall timeout, reproduce an exact graph-specific
SSSP fingerprint, and close before/after artifact identity.

| Directed case | Eligible | Routed | Unsafe | Required reason | `simTicks` | Old-result writes/ACKs |
|---|---:|---:|---:|---|---:|---:|
| all safe | 4 | 4 | 0 | none | 8,462,400,399 | 18,143 / 18,143 |
| active source | 4 | 3 | 1 | active source = 1 | 6,899,819,263 | 13,204 / 13,204 |
| cross owner | 4 | 2 | 2 | cross owner = 2 | 5,336,975,207 | 8,847 / 8,847 |

Every routed window publishes four index and four value pages and accounts
16,384 old-result words. Every unsafe window uses four fallback pages, three
coherent publications per page, and exactly 16,384 legacy words. All cases
report zero bounds rejection, host-SPD reads, illegal aperture starts, hidden
payload, and unacknowledged writes; `response_closure=1` and `counts_close=1`.

The timing values are not a route-versus-fallback comparison. The directed
graphs differ at the hazard edge, and their leaf destinations are sequential,
so they intentionally stress correctness rather than the scattered locality
where a 16K reorder window should help.

## Evidence

- all safe:
  `/data1/nier/dx100-runs/2026-09-01-sssp-chunk-admission-all-safe-af0adba9-r2`
  - result SHA-256:
    `2c50b34adb4e32ae4c8cc2fd677137849eeab6332a3b4f02b614375007b67285`
  - restore SHA-256:
    `e0a2d6f092bfad0edd7f6ac3018cf5b98b8ea8524250fa9d362b882a8832dc35`
- active source:
  `/data1/nier/dx100-runs/2026-09-01-sssp-chunk-admission-active-source-5fbaa33e-r2`
  - result SHA-256:
    `f616a00737cf0dbfe6151625b23be5dae0972088d277c8b26455306bd69c8518`
  - restore SHA-256:
    `f53047b7a71f1885210e9bb2ad566ba4e48b83a72f023369cbb1b22bf09b2029`
- cross owner:
  `/data1/nier/dx100-runs/2026-09-01-sssp-chunk-admission-cross-owner-5fbaa33e-r2`
  - result SHA-256:
    `fbbb1254d3b05ae133d4b641bbb123fefc50ba51905883149ccd5a8e298cee61`
  - restore SHA-256:
    `32ba952b6bc226fe81537e0793c3450a3d3baab03f6689e3038bb7826d0fc9fb`

## Preserved rejection

The first active-source and cross-owner attempts are rejected before gem5:

- `/data1/nier/dx100-runs/2026-09-01-sssp-chunk-admission-active-source-af0adba9-r1`
- `/data1/nier/dx100-runs/2026-09-01-sssp-chunk-admission-cross-owner-af0adba9-r1`

Their functional GAPBS oracle passed verification but emitted no fingerprint
because the host-only call was in `DeltaStepMAA`, while the oracle binary calls
the base `DeltaStep`. Commit `5fbaa33e` moved the host-only call to the executed
path and added a placement regression test. Neither rejected root contains a
checkpoint, restore, or performance observation.

## Next gate

Measure full-S22 routing coverage with the independently validated host-side
admission predictor. Launch a new candidate-only S22 gem5 run only if that gate
shows material safe-window coverage; the frozen full runner then requires an
exact 4,194,304-vertex fingerprint, routed-plus-fallback closure, bounded
16K-logical/4K-physical storage, and no native rerun.
