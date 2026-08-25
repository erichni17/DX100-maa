# Full CG page-product native16 oracle classification (2026-08-25)

## Decision

`PASS_NATIVE16_ORACLE` for correctness and `REJECT_SLOWER` for performance.
The candidate is the completed, immutable raw root
`/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-precomputed-5d51743b-r2`.
The oracle is the already frozen native16 arm at
`/data1/nier/dx100-runs/2026-08-11-cg-bounded-789cc703-full-v8/native16`.
No gem5 or native workload was launched (`native_reruns=0`).

The original bounded4 wrapper rejection remains in force and is not a pass:
`CG_REJECTED.status` remains `REJECT_CORRECTNESS_GATE` because it mismatches
the bounded4 fingerprint.  The separate native16 comparison is explicitly
recorded as `PRESERVED_REJECT_CORRECTNESS_GATE` in the oracle certificate.

## Reopened immutable evidence

The one-shot classifier writes only these candidate-root artifacts, atomically
and without overwrite:

- `NATIVE16_ORACLE_RESULT.json`
- `NATIVE16_ORACLE_RESULT.sha256`
- `NATIVE16_ORACLE_GATE.complete`

`--validate` reopened the raw logs, stats, manifests, ledgers, checkpoints,
guests, gem5 binaries, frozen source/config inputs, result ledger, and gate.
It passed after publication.  The baseline hashes include `analysis.json`,
native16 command/exit/log/stats/config, source tarball, guest, gem5, input,
Ramulator, and checkpoint ledger/identity.  Candidate hashes include manifest,
bounded4 rejection record, checkpoint/restore exits and logs, artifact and
checkpoint ledgers, guest, selector, frozen header, gem5/Ramulator, and frozen
CG/config/runner source paths.

Both services were dead; candidate `restore.exit=0`, candidate checkpoint exit
and frozen native16 exits were zero.  The candidate log has exactly one each of
`m5_exit`, ROI end, fingerprint, selection, and terminal marker; both logs are
fatal-free.  Candidate geometry closes at one MAA, 8 tiles, 16,384 logical and
4,096 physical elements, two memory controllers, 32 row-table slices, and the
declared predicate/value-owner capacities.  The frozen native16 geometry is
the 16,384 physical-tile arm with 16 row-table slices.

## Correctness and mechanism closure

The candidate exactly matches native16 for all required quantized fields:

| Field | Value |
| --- | --- |
| `x_q5` | `bd71373530efa77d` |
| `x_q6` | `9a25df4701c4afa9` |
| `z_q5` | `973558f7c958b798` |
| `z_q6` | `5c3a7792ee8d00f3` |

Frozen scalar relative deltas also pass: `x_sum=1.206542125882194e-12`,
`x_norm_sq=2.9432012384652876e-12`, `z_sum=5.193764993876473e-13`,
`z_norm_sq=1.4239552396051597e-12`, `rnorm=3.3027180022515784e-07`, and
`zeta=0.0`.

The candidate terminal is the required physical-page-product SoA/JIT path,
with 10,960 full windows, 179,568,640 staged/product words, 43,840 index and
product pages, 8,768 q-SpMV plus 2,192 residual-SpMV routed windows, and no
predicate rejection or merge fallback. Publisher closure is exactly
22,446,080 issues/accepts/responses and 87,680 terminals.

## Performance disposition

The first-window simulated ticks are 818,687,246,165 candidate and
58,928,150,676 native16.  Candidate/native16 is `13.89297367681405`; therefore
the classifier labels performance `REJECT_SLOWER`.  This is a performance
rejection, not a correctness failure against the native16 oracle.
