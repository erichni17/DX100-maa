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
and FP32 multiply produce four 4K physical pages. The performance treatment
uses the existing response-bearing SPD publisher to copy each exact 64B line
from the separate Row/Offset and FP32 product tiles into per-thread
`index[16384]` and `value[16384]` coherent backing. Each source stays live
until its own WriteResp terminal; there is no CPU read, copy, cache prefetch,
or hidden 16K producer payload. One existing
`maa_indirect_rmw_vector_soa_jit<float>` call then consumes those arrays with a
null predicate and waits for its completion token before buffer reuse. The MAA
therefore builds one full 16K Row/Offset epoch and retains the existing
duplicate-chain insertion order.

The eight staging arrays are ordinary registered guest memory:

```text
4 owners * 16,384 words * 4 bytes * 2 roles = 524,288 bytes
```

They are external producer storage, not window-sized MAA state. The legacy
`residual_soa_jit` selector remains a CPU-staged provenance control. The new
`residual_soa_jit_response_bearing` selector uses two 4K physical producer
tiles, two sets of eight 64B retained publisher credits (1,024 B payload), and
no hidden logical16 payload or CPU copy. Its terminal reports every published
page/word count; the gate closes publisher issue/accept/WriteResp/terminal
traffic against the exact dynamic window count and requires actual non-stream
overlap. No GZP predicate, map, or fused operation is reused.

## Selector and fail-closed checks

The new `cg_maa_16K_general_fp_rmw` binary accepts only
`MAA_DEFERRED SELECTOR`. Its selector must contain exactly a virtual consumer
and one of `legacy_4k`, `residual_soa_jit`, or
`residual_soa_jit_response_bearing`; missing, extra, and unknown tokens
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

The exact performance gate restores both CPU control and response-bearing
treatment from one pre-selector checkpoint with at least two deterministic
replicas and no default timeout. It rejects any fingerprint, terminal, traffic,
configuration, or checkpoint mismatch; it also rejects a slower treatment.
This still does not convert the iterative `q` RMW phases or partial tails.

## Validation performed in this checkpoint

- The focused CG contract suite passes all 8 checks.
- Static and compiler validation are recorded with the implementation commit.
- Live promotion requires the gate's committed-source build and all terminal
  closures; no historical CPU-staged result is reusable for this treatment.
