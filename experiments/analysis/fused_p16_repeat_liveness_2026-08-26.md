# Repeated fused-p16 reset/liveness diagnosis (2026-08-26)

## Decision

**ACCEPT the repeated micro as diagnostic evidence of linear cost and no
fused-p16 generation/reset state leak through 64 sequential operations.** All
16-, 32-, and 64-operation cases completed serially from one immutable
checkpoint with exact outputs and complete per-operation producer/q ledgers.
No MAA change is justified.

This is not a workload-performance promotion. No CG, native, or full-workload
run was launched, and no timeout was applied to a healthy case.

## Exact experiment

The new guest repeats one guarded 16K fused gather-map-product followed by one
page-fed q16 ADD. The entire sequence for a case is inside one ROI. It reuses
producer completion token 0 while advancing unique producer and q generations
1 through N. Each operation has a distinct deterministic source/coefficient
input and exact reference/product/q hashes; the four index segments retain the
accepted all-same, same-line, cross-page, and pseudorandom pattern.

The cases run strictly in the order 16, 32, 64. A case must reach `m5_exit`,
emit every progress marker, match every hash, and close every required stat
window before the next directory is created. One shared checkpoint, one pinned
gem5, and one pinned Ramulator library feed all three restores. The selected
one-indirect-unit geometry makes the pinned MAA's monotonic internal fused
generation counter advance once per serial operation.

Each completed operation performs `m5_dump_reset_stats`, producing an
independent hardware-stat window without the accepted single-operation
micro's 28 MiB per-access trace. For every window the analyzer requires:

- one producer operation and one complete p16 epoch;
- 16,384 source ordinals, coefficient deliveries, MUL accepts/completions,
  product insertions, and semantic WriteResp completions;
- coefficient issue = response = fill, bounded to 1,024..16,384 lines;
- one terminal q operation, four admissions, one close, five command
  responses, and 16,384 selected/value-delivery/row-write words;
- schema-present zeros for fused and generic drains, fused and bounded-global
  fallbacks, fused publisher/virtual-p traffic, generic publisher
  issues/responses, and coherent page-fed index traffic.

The pinned terminal implementation additionally fails closed unless response
owners, the bounded combiner, outstanding writes, and the coefficient
coalescer generation are empty. Exact MUL accept/completion counts plus tagged
`finishDirectPair` retirement return the sole ALU to `Idle` before operation
closure. Because the simulator/config/API tree is byte-identical to pinned
simulator source `4a4d91b8f176c33779804fbd163014593d89e737`, this is a source-bound
inference from the exercised terminal path, not a new debug-trace claim.

## Accepted r2 evidence

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-fused-p16-repeat-liveness-20260826-20260826-205832-d01978ec/fused-p16-repeat-liveness-r2`

All 74 ledger entries pass `sha256sum -c`. Raw-root ledger SHA-256:
`4bde03cc6ccb51b189ad48fbc602c826b8a23f5d610d52c39fbe244aab9feed1`.
The success-only gate binds that digest and records
`classification=LINEAR_NO_STATE_LEAK`.

| Operations | Progress/stat windows | Product words | q words | Coefficient issue/response/fill sum | Rolling hash | Total `simTicks` |
|---:|---:|---:|---:|---:|---:|---:|
| 16 | 16/16 | 262,144 | 262,144 | 90,048 | 5965429330145648202 | 12,457,291,702 |
| 32 | 32/32 | 524,288 | 524,288 | 180,132 | 6601373722485720462 | 25,201,836,650 |
| 64 | 64/64 | 1,048,576 | 1,048,576 | 360,260 | 7159243353686113274 | 51,263,955,052 |

Every operation has `reference_hash == product_hash == q_hash`, zero
sentinels/errors, a distinct input hash, the expected generation, and the same
reused producer token. Coefficient issue/response/fill counts close exactly in
every window; the per-operation issue range is 5,618..5,672 lines. All other
producer and q work ledgers equal the exact counts above. Every required zero
field is present and zero in all 112 operation windows.

### Scaling

| Measure | 16 | 32 | 64 |
|---|---:|---:|---:|
| Mean `simTicks` / operation | 778,580,731.375 | 787,557,395.313 | 800,999,297.688 |
| Last quarter / first quarter | 0.996344 | 1.045697 | 1.048034 |

- Total 32 / total 16 = `2.023059044684157`.
- Total 64 / total 32 = `2.034135676853536`.
- Maximum normalized per-operation drift across sizes = `2.879414479332%`.

The totals therefore scale linearly within the predeclared 10% normalized and
1.8..2.2 doubling bounds. The latest-quarter cost stays within 4.81% of the
earliest quarter even at 64 operations. There is neither a functional state
leak nor a superlinear timing accumulation in this micro.

## Rejected r1 evidence

The first root, `fused-p16-repeat-liveness-r1`, is **REJECTED and unsealed**.
Its repeat-16 guest reached 16/16 with the same rolling hash and zero errors,
but the generic analyzer attempted to convert an unrelated gem5 `inf` stat to
an integer. It stopped before repeat-32, proving the fail-fast escalation rule.
No raw-root gate exists, so r1 is not an authority.

The parser-only defect was repaired by skipping nonnumeric/nonfinite unrelated
stats while still requiring every named ledger. A synthetic `inf` regression
test was added, and r2 was run from a fresh root. No raw artifact was reused.

## Provenance and validation

- Runner/source commit: `7212ac673f845a3cd60e3299d849ee36209b8129`.
- Initial guest/runner commit: `55f988262b9b7da7e292e0ef97e5c2ed9f76b018`.
- Pinned gem5 SHA-256:
  `271836b58d02d9d50a658cd5c7628e15559ca22d3a04477ab15475e3744dfd2e`.
- Pinned Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
- Guest SHA-256:
  `bc1a3a36cee2933cf8997dd3cd941616896800475d74f91831db86bb99a0abf2`.
- Immutable artifact-ledger SHA-256:
  `d60099ffa694ff5bc174fc719f81f02c12459d31c575a2755e88b10cab2da965`.
- Immutable checkpoint-ledger SHA-256:
  `c9d653caab66b86196ef157ade0432167d81509c8d443718cab274a6e9ce7abe`.
- Checkpoint and all three restore exit codes are zero; each restore has one
  guest terminal and one gem5 `m5_exit` terminal.
- The focused Python module passes 7/7 tests; the guest compiles with the
  production C++17 warnings-as-errors flags; repository source/style hooks
  pass. The commit-message maintainer hook was skipped only because this
  checkout lacks `MAINTAINERS.yaml` and the hook crashes before validation;
  the independent Gerrit message check passes.

This is one deterministic observation per size. It diagnoses repeated
generation/reset liveness and within-treatment scaling only; it does not claim
application speedup, variability, arbitrary geometry, or broader promotion.
