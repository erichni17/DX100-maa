# Strict line-combined cross-application audit (2026-08-27)

## Decision

**Do not generalize the CG strict-plus-masked treatment by opcode label.** NAS
IS, HashJoin PRO/PRH, and GAPBS SSSP all use the SoA/JIT RMW engine, but none
reaches the CG virtual-gather result-retirement edge. Enabling
`--maa_virtual_strict_two_phase --maa_virtual_masked_writes` on their existing
checkpoints would therefore be inert or would validate the wrong storage
contract.

The accepted generic artifact is the default-off, read-only runner
`experiments/scripts/run_general_strict_linecombined_matrix.py`. It binds the
matrix below to production source hashes, can revalidate the existing full
evidence roots, launches neither gem5 nor native code, and fails closed if a
caller requests any full treatment. No new candidate-only full workload was
launched because the set of applicable families is empty. The already-running
SSSP S22 candidate was not duplicated.

SSSP does legally use masked 64-byte retirement, but through the distinct
`SoaJitOldResultBuffer` contract. That result storage is semantically required
and is not replaceable by CG's virtual result combiner.

## Reference boundary

The validated CG reference is specifically a direct virtual-index producer:

1. `INDIR_LD_VIRTUAL_INDEX` reads one registered B/index stream into bounded
   feeder entries.
2. After each successful Row/Offset insertion, the private numeric B copy is
   poisoned and erased. Row/Offset retains the A-line identity, logical
   ordinal, and returned word ID, not B payload
   (`IndirectAccess.cc:3064-3150, 4185-4260`).
3. Strict mode applies only when `isVirtualLoad() && isDirectIndexLoad() &&
   !isSoaJitRmw()`, plus the separate page-fed q predicate
   (`IndirectAccess.cc:856-877`). It fences actual A issue until every
   descriptor has been admitted.
4. Returned `A[B[i]]` values are not dead. They retire by logical ordinal into
   coherent result backing through `insertVirtualCombineWord`; masked mode
   converts partial victims/final fragments into 64-byte WriteReqs with
   per-word byte enables (`IndirectAccess.cc:10076-10167,
   10611-10715, 10913-10940`). The consumer needs that backing.

Those four properties—not the words “indirect,” “16K,” or “hybrid”—are the
generalization test. The accepted CG evidence and its limits remain in
`experiments/analysis/strict_two_phase_cg_reference_2026-08-27.md`.

## Production matrix

| Application | Exact producer and backing | Is numeric B dead? | Result/old-result storage | Masked 64-B retirement | CG strict + masked decision |
|---|---|---|---|---|---|
| NAS IS | No virtual result producer. `key_array`/`key_buff_ptr2` is registered coherent direct-index input; scalar `1` is captured once; `key_buff1_work` is mutable histogram A. | Yes inside the operation after Row/Offset insertion. There is no later application read of that index window. | No old result and no result backing; the tile is completion-only. The architectural histogram remains in A memory. | Not applicable: there is no virtual result to retire. The normal A path already performs coherent full-line RMW writes. | **Reject as non-applicable.** Both CG flags would miss the executed edge. |
| HashJoin PRO | Host code computes radix bucket IDs into the 256-KiB four-thread `hybrid_soa_indices` coherent arena, then scalar-ADDs into `histR`/`histS`. | Yes after Row/Offset insertion; the arena is reused only after completion. | No old result or extra result backing. PRO has no shifted histogram pass. | Not applicable for the same reason as IS; histogram A-line writeback is not virtual result retirement. | **Reject as non-applicable.** |
| HashJoin PRH | Same first-pass producer/backing as PRO. The accepted full PRH shifted pass is `tail_only`; it produces no second full 16K SoA/JIT window. | Yes after admission for each routed window. | No old result or extra result backing. | Not applicable; no virtual result producer exists. | **Reject as non-applicable.** |
| GAPBS SSSP | Four response-bearing physical-4K publishers write coherent `indices` and `values`; the direct vector SoA/JIT MIN also reads coherent predicates and writes old results. | The private feeder copy is dead after admission, but **numeric B is not dead at the application boundary**: host reconstruction rereads index and value backing after completion. | Required. The old distance at every original logical ordinal is used to reproduce `candidate == final && old > final` page-local winner semantics. | **Legal only through the distinct old-result publisher.** It emits masked 64-byte writes with exact generation/sequence/address/mask WriteResp ownership. CG masked retirement is not applicable. | **Reject CG mapping; retain SSSP's existing masked old-result contract.** |

### NAS IS

The production call is
`maa_indirect_rmw_scalar_soa_jit<int>(work_buff, key_buff_ptr2 + i, nullptr,
...)` followed by its completion wait (`benchmarks/NAS/is/is.cpp:895-919`).
The terminal explicitly requires zero predicate words, zero value words, zero
host-SPD reads, and zero staging bytes (`is.cpp:946-972`). The API encodes the
scalar register in `backingaddr`; it does not name a value/result array and
forbids an old-value destination
(`benchmarks/API/MAA_gem5.hpp:1040-1088`).

Consequently:

- the direct index is the only B-like numeric stream;
- its private feeder copy is dead after descriptor insertion;
- there is no P/product/result producer between B admission and the histogram
  A update; and
- applying CG's masked output combiner would have no address span to target.

The full Class-B candidate adds no staging/result backing. The existing
`key_array` and per-thread histograms are application state, not virtual result
storage.

### HashJoin PRO and PRH

Both live histogram sites explicitly calculate `HASH_BIT_MODULO` into
`hybrid_soa_indices` and issue scalar-broadcast SoA/JIT ADD
(`parallel_radix_join.cpp:472-510, 825-864`). The arena allocation is exactly
`4 threads * 16,384 words * 4 B = 262,144 B` and is registered as one coherent
region (`parallel_radix_join.cpp:1425-1538`). The subsequent padded scatter is
still an ordered physical-4K path (`parallel_radix_join.cpp:534-564,
908-938`).

PRO and PRH diverge only after their shared partitioning path. The actual join
result is a cardinality returned by bucket-chain or histogram probe code; the
SoA/JIT histogram operation does not publish an old result. The accepted full
roots correctly distinguish PRO's shifted pass as `not_applicable` and PRH's as
`tail_only`. Treating either as a second full virtual producer would invent
work.

### GAPBS SSSP

SSSP has four aligned coherent arrays per four-core build:

| Span | Bytes | Current role |
|---|---:|---|
| `sssp_hybrid_indices` | 262,144 | response-published B/index input and later winner destination |
| `sssp_hybrid_values` | 262,144 | response-published candidate values and later winner comparison |
| `sssp_hybrid_predicates` | 262,144 | immutable routed predicate plus coherent-fallback reuse |
| `sssp_hybrid_old_results` | 262,144 | architectural old-value result and coherent-tail scratch |
| **Total external coherent backing** | **1,048,576** | separate from the 524,288-B physical SPD payload |

The index/value publishers issue and wait for response-bearing physical-page
stores (`benchmarks/gapbs/src/sssp.cc:101-162`). The RMW then binds
`dist`, index, value, predicate, and old-result spans
(`sssp.cc:282-310`). After completion, the reverse/forward reconstruction reads
indices, values, and old results to preserve page-local duplicate-winner order
(`sssp.cc:312-344`). Eliminating any of these current backings requires a new
application proof and producer/consumer contract; numeric-B death inside the
MAA feeder does not provide that proof.

The old-result path captures the pre-update A word by original logical ordinal
before applying MIN (`IndirectAccess.cc:5182-5208`). Its fixed eight-slot buffer
groups words by the 64-byte result line, retains payload and exact response
identity until WriteResp, and rejects a wrong mask
(`SoaJitOldResultBuffer.hh:13-28, 85-177, 248-267`). Pressure or terminal drain
may emit a partial mask; `serviceSoaJitOldResultWrites` expands it to byte
enables and sends one response-bearing 64-byte WriteReq
(`IndirectAccess.cc:5249-5348`). This is legal because each logical ordinal is
captured exactly once, disabled bytes are untouched, same-line reuse waits for
the prior response, and the CPU reads the span only after the completion token.

## Generic runner and launch contract

The runner emits schema `dx100.general_strict_linecombined_matrix.v1` and:

- hashes the exact production sources used by every classification;
- asserts both treatment parameters remain default-off;
- binds strict admission to the actual virtual/page-fed predicates;
- binds numeric-B death to the private feeder poison/erase edge;
- binds SSSP old-result legality to 64-byte masks and exact response identity;
- optionally reuses the established full-evidence validators with
  `--validate-evidence`; and
- reports `native_runs=0`, `candidate_full_runs=0`, and an empty
  `applicable_full_families` list.

An explicit `--launch-full {is,hashjoin-pro,hashjoin-prh,sssp}` request exits
before creating output. This is intentional: a generic runner must not turn an
inert flag combination into apparent application evidence. A future family may
be admitted only after production source proves all four CG reference
properties, including a real virtual result backing and an actual masked
retirement edge.

## Evidence reuse and current status

No native baseline was rerun. The audit reused these previously accepted exact
small/bounded screens:

- IS scalar-SoA smoke:
  `/data1/nier/dx100-runs/2026-08-24-is-scalar-soa-smoke-2a0bc33c-r1`;
- HashJoin PRO/PRH small:
  `/data1/nier/dx100-runs/2026-08-24-hashjoin-hybrid-small-a77f77f1`;
- SSSP routed full-cache small:
  `/data1/nier/dx100-runs/2026-08-25-sssp-coherent-small-fullcache-r2`.

The generic read-only full-evidence validation returned:

| Root | Status | Interpretation |
|---|---|---|
| IS full certificate, `2026-08-26-is-scalar-soa-full-certificate-r1` | passed | official exact correctness; no performance promotion |
| HashJoin PRO hardened full, `2026-08-24-hashjoin-pro-hardened-r1` | passed | exact 2,000,000 cardinality; first pass routed; shifted pass not applicable |
| HashJoin PRH hardened full, `2026-08-24-hashjoin-prh-hardened-r1` | passed | exact 2,000,000 cardinality; first pass routed; shifted pass tail-only |
| SSSP S22, `2026-08-25-sssp-coherent-full-s22-r2` | pending | `gate.complete` absent; no correctness or timing claim |

At the last ownership check, the SSSP wrapper PID was `2635394` and the exact
gem5 PID was `2637298`, started 2026-08-25 02:24:08 EDT and still using 99.9%
CPU. The restore log continued to report DeltaStepMAA progress, while final
`stats.txt` was empty and `restore.exit`, `result.txt`, and `gate.complete` were
absent. A dead or transiently unreadable `/proc` observation is not terminal
evidence; the PID, wrapper status, m5 exit, final stats, fingerprint, ledgers,
and gate must all close.

## Accepted, rejected, and pending milestones

- **Accepted:** production-source instruction/dataflow matrix; private numeric-B
  death for IS and both HashJoin histogram sites; SSSP's distinct masked
  old-result legality; default-off read-only generic audit contract.
- **Rejected:** treating ordinary scalar SoA/JIT histogram A writeback as CG
  virtual result retirement; treating PRH tail-only work as a full shifted
  producer; removing SSSP old-result backing; using the two CG flags as evidence
  when their production predicate is false.
- **Pending:** the pre-existing full SSSP S22 candidate. This audit neither owns
  nor promotes it and did not launch a replacement.

## Validation

Focused validation at this milestone:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  experiments.tests.test_general_strict_linecombined_matrix
python3 experiments/scripts/run_general_strict_linecombined_matrix.py \
  --validate-evidence \
  --output /tmp/crossapp-linecombined-matrix-20260827-r2.json
experiments/scripts/strict_two_phase/run_reference_unit.sh
git diff --check
```

The combined bounded surface passed 53 Python contract cases: 6 cross-app, 7
strict-reference, 8 IS, 9 HashJoin, and 23 SSSP cases. The optimized and
sanitized strict C++ reference also passed. The source contract and read-only
evidence audit passed for IS and both HashJoin kernels; SSSP remained
explicitly pending.
