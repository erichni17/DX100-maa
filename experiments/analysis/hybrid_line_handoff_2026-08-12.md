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
