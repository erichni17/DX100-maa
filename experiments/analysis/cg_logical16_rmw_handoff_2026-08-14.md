# NAS CG logical-16 SoA/JIT RMW handoff — 2026-08-14

## Implemented boundary

NAS CG contains eight live sparse indirect FP32 ADD callsites: four dynamic
`q += A*p` sites and four residual `r += A*z` sites across the compile-time
virtual-consumer alternatives. None requests an old-value/result tile and none
has a predicate. This change converts one representative runtime slice only:
full 16,384-nonzero windows in the general-hybrid residual SpMV. The iterative
`q` SpMV, partial residual tails, bounded-specialized path, and non-general
paths remain on their existing page-local RMWs.

| Runtime role | Source call expressions | Result/predicate | Treatment |
|---|---:|---|---|
| iterative `q += A*p` | 4 | no old result; no predicate | unchanged legacy 4K |
| residual `r += A*z` | 4 | no old result; no predicate | general-hybrid full windows only |

The count of four per role reflects mutually exclusive bounded, general,
virtual, and ordinary compile-time arms, not four RMWs executed for every
sparse window. The selected general-hybrid arm retains exactly one callsite
for each role.

For each selected residual window, the unchanged range loop, virtual gather,
and FP32 multiply produce four 4K physical pages. After exact tile completion,
the guest validates each page length and row index, then copies the row indices
and FP32 product bits in page/lane order into per-thread `index[16384]` and
`value[16384]` arrays. One existing `maa_indirect_rmw_vector_soa_jit<float>`
call consumes those arrays with a null predicate and waits for its completion
token before buffer reuse. The MAA therefore builds one full 16K Row/Offset
epoch and retains the existing duplicate-chain insertion order.

The eight staging arrays are ordinary registered guest memory:

```text
4 owners * 16,384 words * 4 bytes * 2 roles = 524,288 bytes
```

They are external producer storage, not window-sized MAA state. This first
vertical slice intentionally uses coherent CPU copies after SPD completion.
That traffic is simulated, but it is not the intended accelerator publisher,
so every marker and the runner declare `performance_promotable=0` and
`speedup_claim=0`. No GZP predicate, map, or fused operation is reused.

## Selector and fail-closed checks

The new `cg_maa_16K_general_fp_rmw` binary accepts only
`MAA_DEFERRED SELECTOR`. Its selector must contain exactly a virtual consumer
and one of `legacy_4k` or `residual_soa_jit`; missing, extra, and unknown tokens
fail before the ROI. The compile-time treatment additionally requires logical
16K, physical consumer 4K, gem5, MAA, and the existing general consumer.

At runtime, every staged tile size must equal the exact page size, every row
index must remain within the current row block, staged index/value counts must
equal `full_windows * 16384`, and the treatment must dynamically execute at
least one full window. Existing SoA/JIT span, generation, issue/response,
ordered-alias, terminal WriteResp, and drain checks remain authoritative.

## Smoke contract and limits

`run_cg_logical16_rmw_smoke.sh GEM5 OUTDIR` builds a small runtime-generated
`CG_NA=1024` guest and uses separate immutable selector-specific checkpoints.
Both arms pin 16K logical/Offset metadata, 4K physical SPD, predicate credits
16, and value owners 32. Promotion requires an identical complete
`CG_FINGERPRINT`, exact terminal markers, dynamic SoA/JIT instruction and alias
counts, balanced A WriteReq/WriteResp counts, resolved configuration, clean
terminators, and unchanged selector hashes.

CG supplies a null predicate, so the pinned p16 predicate feeder must report no
predicate traffic; it is a resolved portfolio setting, not an asserted CG
speedup source. The v32 value-owner selection is active for JIT value reads.

This smoke is correctness/provenance evidence only. It does not validate class
C input, the four iterative `q` RMW phases, partial-tail conversion, multiple
replicas, speedup, area, or the concurrent selectable apply-lane work. A future
performance treatment should replace the CPU copy with the independently
validated response-bearing SPD publisher while preserving this exact operand
and selector contract.

## Validation performed in this checkpoint

- The focused CG contract suite passes all 8 checks.
- The existing generic bounded SoA/JIT contract suite passes all 10 checks.
- The new `CG_NA=1024` guest compiles and links with `-Wall -Wextra -Werror`;
  only the NAS file's pre-existing unused-parameter warnings are explicitly
  suppressed by the smoke build.
- The legacy general-hybrid CG compile path also passes strict syntax checking.
- The runner passes `bash -n`, and the patch passes `git diff --check`.

No exact live gem5 smoke was run. This worktree has no production binary, and
the available binaries were built from older or concurrently modified MAA
trees that do not reproduce the combined p16/v32 source at this checkpoint.
Using one would violate the runner's provenance purpose. The runner is the
explicit remaining live-validation boundary.
