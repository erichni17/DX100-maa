# Hybrid Result-Line Handoff

## Question

The 4K-SPD/16K-reorder hybrid waits for each complete 4K producer page before
the dependent multiply/store consumer starts reading it.  The experiment asks
whether that page boundary exposes avoidable LLC-copy latency.

## Treatment

The producer still writes every result to coherent backing storage.  The only
change is visibility: after all valid words in one 64-byte result line receive
their real `WriteResp`, that line may be consumed.  The page-level acknowledgement
remains a fail-closed fallback.  This overlaps producer writes with dependent
reads without treating an issued write as completed.

The bounded hardware model remains:

- 16 payload credits x 64 bytes = 1,024 bytes.
- 4,912 bytes of modeled control state, including a fixed 2 KiB line-readiness
  ledger.
- Full 16K producer reordering state and coherent LLC backing remain required.

This is not fully bounded RowTable virtualization.  It reduces the hybrid's
result-retirement stall but does not make a 4K RowTable preserve a 16K reorder
window.

## Matched Evidence

Raw results:
`/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-b7ef7ea4`

- Simulator source: `8a5c7712` for the C++ implementation; `b7ef7ea4` fixes
  treatment selection in the runner only.
- gem5 SHA-256:
  `9806157ce009ae7de01d506379076557f0d12718142a4e2c5bc69aea41c93e0f`.
- Workload SHA-256:
  `6b0b8407cc919c32490ce2b5a3e47ce8545602056782a6f8dd7dc6f19e81de3d`.
- All arms used one frozen checkpoint and produced exact output hash
  `7228541527853630339`.

| Arm | Replica 1 | Replica 2 | Result |
| --- | ---: | ---: | ---: |
| Page-gated control | 43,334,224 | 43,334,224 | exact replicas |
| 64-byte line handoff | 40,207,041 | 40,207,041 | exact replicas |

Line handoff lowers latency by 7.216% and gives a 1.07778x speedup over the
page-gated control.  Against the prior native references it is 0.360% slower
than native 16K (40,062,748 ticks) and 1.50010x faster than native 4K
(60,314,474 ticks).

## Closure Checks

Each replica completed one descriptor, four page acknowledgements, 2,048
reads, 2,048 ALU completions, and 2,048 writes with zero fallback errors.

- Page control: zero producer-line acknowledgements and 2,048 page-fallback
  lines.
- Line treatment: 2,048 producer-line acknowledgements and zero page-fallback
  lines.
- In both cases,
  `producer_line_acks + page_fallback_lines == expected_result_lines`.

## Conclusion

For the terminal `C[i] = A[B[i]] * scalar` microbenchmark, most of the measured
hybrid gap was an avoidable page-visibility barrier, not loss of 16K producer
reordering.  The result is strong but narrow.  Promotion requires a real XRAGE
expression run and does not establish a generic virtual scratchpad or a
fully bounded 4K reorder engine.

## XRAGE Expression Promotion

The same mechanism was integrated into XRAGE's terminal
`C[i] = 3 * A[B[i]]` expression.  The guest issues a 16K logical direct-index
gather followed by the scalar multiply/dense store consumer.  The temporary
`A[B[i]]` values remain in coherent backing memory; the treatment changes only
when acknowledged result lines become visible to the consumer.

One-descriptor evidence:
`/data1/nier/dx100-runs/2026-08-12-xrage-direct-x3-line-handoff-promote-r3-c0935d1a`

- Page-gated control: 41,325,077 ROI ticks.
- Line handoff: 37,849,212 ROI ticks.
- Result: 8.411% lower latency, or 1.091835x speedup.
- Both arms produced exact output hash `2624823738765411203`.
- Physical memory and all serialized checkpoint state except gem5's
  wall-clock header are byte-identical.
- The page arm closed with 2,048 fallback lines.  The treatment closed with
  2,048 real producer-line acknowledgements and no fallback lines.

The paired validator records the simulator commit and binary hash, guest build
commit and binary hash, input hash, checkpoint identity, exact output, terminal
exit, and mechanism closure.  Its checkpoint normalization ignores only the
first `## checkpoint generated:` timestamp line and has tests that reject any
other state or physical-memory difference.

## B-Index Lifetime

The direct-index producer does not save `B[i]` values to LLC.  Once Row/Offset
insertion retains the A cache-line address, logical destination iteration, and
response word ID, the private streamed index copy is poisoned and erased.  The
architectural B array is unchanged.  Therefore, the remaining backing traffic
is the returned `A[B[i]]` result payload, not the B-index stream.

## Multi-Descriptor Limit

A four-descriptor, 65,536-element gate used the same frozen simulator, guest,
runner, input, and checkpoint contract:
`/data1/nier/dx100-runs/2026-08-12-xrage-direct-x3-line-handoff-64k-52ba9e98`

Both arms completed gem5 and produced exact output hash
`5576400619275092867`.  The page control passed its wrapper.  The treatment
wrapper deliberately failed its no-fallback promotion gate:

- Page control: 105,258,457 ROI ticks, 8,192 page-fallback lines.
- Line treatment: 105,258,144 ROI ticks, 2,048 line acknowledgements and 6,144
  page-fallback lines.
- Difference: 313 ticks, or 0.000297% lower latency.

The single direct-retirement context overlaps line production and consumption
for the first descriptor.  While that context drains, the next three producers
finish and their complete pages wait in the queue.  When those consumers are
admitted, no producer latency remains to hide, so page readiness is sufficient
and line granularity cannot improve steady-state throughput.  This is not an
output-correctness failure, but it blocks promotion of the 8.411% result beyond
single-descriptor latency.

The next design must either support a bounded set of concurrent consumer
contexts sharing the existing ALU/cache ports, or bypass the temporary backing
round trip by handing complete producer result lines directly to a legal
terminal ALU/store consumer.  Merely retaining early-ready bits for queued,
already-complete producers would not improve performance.
