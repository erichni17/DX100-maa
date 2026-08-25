# Full CG page-product rejection (2026-08-25)

## Decision

**REJECT_CORRECTNESS_GATE.** The candidate completed its full gem5 ROI and all
mechanism/response ledgers close, but it fails the predeclared exact quantized
fingerprint requirement. It is not correctness-promoted and its timing is not
reported as architecture performance.

Raw root:
`/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-precomputed-5d51743b-r2`.
The simulator consumed 7h44m55s CPU time and exited normally. The shell wrapper
exited 1 at the first scientific failure, before its source-status comparison,
result certificate, or `gate.complete`.

## Passing evidence

- Frozen gem5, Ramulator, guest, selector, CG source, config, runner, reference,
  and 992,830,458-byte precomputed matrix hashes all revalidate.
- The checkpoint exits once and its complete file ledger revalidates.
- The full mechanism terminal reports 10,960 windows: 8,768 q-SpMV plus 2,192
  residual-SpMV windows, all routed.
- It stages/publishes 179,568,640 index/product words through 43,840 index and
  43,840 product pages; no value pages or logical scheduler/ALU actions occur.
- Hardware stats close 10,960 SoA/JIT instructions/terminals, 179,568,640
  selected aliases, zero predicate rejections/fallbacks, 22,446,080 publisher
  issues/accepts/responses, and 87,680 publisher terminals.
- Both candidate and reference contain finite values and a local fingerprint
  `result=PASS`.

## Failing evidence

The frozen contract requires exact `x_q5`, `x_q6`, `z_q5`, and `z_q6` hashes
before checking scalar tolerances. All four differ:

| Field | Frozen reference | Candidate |
|---|---|---|
| `x_q5` | `88c0975669c7062d` | `bd71373530efa77d` |
| `x_q6` | `235baae2cde3472e` | `9a25df4701c4afa9` |
| `z_q5` | `9d0c4e827a12742b` | `973558f7c958b798` |
| `z_q6` | `35dce54d02fd013a` | `5c3a7792ee8d00f3` |

The aggregate relative deltas independently pass their declared bounds:
`x_sum=4.4642e-11`, `x_norm_sq=1.6177e-11`, `z_sum=5.5262e-10`,
`z_norm_sq=1.1864e-9`, `rnorm=1.1113e-4`, and `zeta=2.8422e-15`.
That is evidence of a small numerical-order difference, but it does not
override the stronger exact quantized gate after observing the result.

`818,687,246,165 simTicks` is retained only as rejected-run provenance. No
speedup/slowdown is claimed. The one-shot branch-status recovery tool is
deliberately inapplicable: this run failed before `source_status.after`, and
the raw root now records `recovery=prohibited`.

## Diagnosis and next experiment

The page-product path forms four physical 4K product pages, then submits one
16K SoA/JIT ADD. This preserves the large Row/Offset visibility but changes the
floating-point update grouping relative to four page-local RMW operations.
The exact mechanism closure rules out missing pages, aliases, or responses as
the immediate explanation.

Do not rerun this unchanged candidate. First choose one of two independently
gated successors:

1. preserve per-destination update order across the 16K SoA/JIT operation and
   require the existing exact coarse fingerprint; or
2. generate a frozen host-side golden vector from the existing frozen input,
   predeclare per-element absolute/relative and residual bounds, and add
   max/count error statistics before another full candidate run. This is an
   oracle construction step, not a native gem5 baseline rerun.
