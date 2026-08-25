# Hybrid goal completion audit (2026-08-24)

## Architecture contract

The selected hybrid keeps a 16,384-entry logical instruction/Offset scope and
the existing RowTable mechanism while limiting each host-visible SPD tile to
4,096 32-bit words. With 4 cores and 8 tiles/core, dedicated SPD payload is
524,288 bytes versus 2,097,152 bytes for native 16K: a 1,572,864-byte (75%)
payload reduction. Row/Offset storage is not claimed as virtualized.

Current full-application runners select 32 RowTable slices, 64 rows/slice, and
8 unique-line slots/row: 16,384 gross cache-line slots. This is distinct from
the 16,384 logical descriptors in the OffsetTable. Multiple descriptors may
share one line slot, while row-distribution pressure can still force a drain;
the geometry is not by itself a proof of one uninterrupted 16K reorder epoch.

Logical page data lives in ordinary coherent backing, not hidden SPD. CG
explicitly charges 786,432 backing bytes for its three 16K x 4-core arrays.
This is shared cache/memory capacity, not dedicated DX100 SRAM, and may evict
other LLC data.

The CPU-aperture repair adds no payload, queue entry, or persistent table.
Hardware needs the existing physical-bound comparator, prefetch provenance, a
full-byte-enable check, and error-response control. The two new gem5 counters
are instrumentation and contribute zero modeled hardware bits.

## No-native-rerun contract

Every current full runner is candidate-only. HashJoin manifests record
`native_rerun=0`; IS records `native_runs=0`; CG and SSSP record
`native_arms=0`. Existing native/tile-sweep endpoints remain frozen references.

## Full-application status

| Workload | Evidence | Status |
|---|---|---|
| HashJoin PRO | `2026-08-24-hashjoin-pro-hardened-r1` | terminal-valid; exact 2M result |
| HashJoin PRH | `2026-08-24-hashjoin-prh-hardened-r1` | terminal-valid; exact 2M result; shifted tail-only |
| NAS IS | `2026-08-24-is-scalar-soa-full-a44aaa60-r5` | terminal-valid; verification 6 |
| NAS CG | `2026-08-24-cg-page-product-full-precomputed-5d51743b-r2` | terminal-valid against frozen native16 oracle; performance rejected at 13.893x native16 latency |
| GAPBS SSSP small | `2026-08-25-sssp-coherent-small-fullcache-r2` | terminal-valid routed-path gate; exact fingerprint, zero fallback/host-SPD reads |
| GAPBS SSSP S22 | `2026-08-25-sssp-coherent-full-s22-r2` | active coherent-fallback successor |

The goal is incomplete until CG's full-application performance overhead is
materially reduced and the active SSSP successor passes its exact correctness,
mechanism, artifact, and response-ledger gates.

## Optimization decisions

- Accepted/integrated: physical-page product formation for CG; dense/four
  old-result publication; existing value cache/64 owners/pre-A composition;
  physically bounded stream/ALU/range units; task-tagged SPD prefetch drop.
- Rejected: context64, compact write retirement, expanded RowTable, strict
  all-B sequencing, page-aware A ordering, one-partial-write pressure, and
  the 4K+ SSSP tail-replay series.
- Compact retirement is exact but fails its predeclared threshold: SSSP ties
  and HashJoin improves only 0.012326154%, while adding 584 persistent bytes.

## Remaining proof

1. CG correctness is complete against the frozen native16 oracle: exact
   quantized fingerprint/tolerances, all logical windows routed, zero
   fallbacks/open contexts, and closed publisher/A ledgers. The current
   page-product implementation remains rejected for 13.893x native16 latency.
   The selected successor directly feeds four physical index pages into one
   persistent 16K Row/Offset operation, eliminating index backing/publication
   and the second descriptor-build pass. Its focused exact gate passes with
   zero coherent index lines, four/1,024 product pages/lines, one
   open/four-admit/one-close operation, and 16 bytes persistent control. A
   small/full CG application gate is still required before performance
   promotion.
2. SSSP: exact S22 fingerprint, nonzero coherent fallback-page coverage, zero
   host-SPD reads and architectural aperture rejections, closed fallback
   publication responses, and closed predicate/value/A/old-result ledgers.
3. Verify each final raw hash ledger and commit the terminal classification.
4. Only then perform the requirement-by-requirement goal completion audit.
