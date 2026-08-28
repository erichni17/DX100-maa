# Virtualization microbenchmark acceptance contract (2026-08-28)

## Existing suite

No new benchmark executable is required for the current hybrid questions.
Use the existing API tests:

| Existing test | Required coverage |
|---|---|
| `test_virtual_tile_attribution.cpp` | Equal-work native16, native4x4, and logical16/physical4 timing |
| `test_virtual_gather.cpp` | Random, resident, dirty, fanout, page, line, conditioned, all-false/all-true, boundary, short, and unregistered behavior |
| `test_virtual_gather64.cpp` | FP64 line/page/tail behavior |
| `test_virtual_gather_multiunit.cpp` | Same-line multi-unit ownership and masked-write interaction |
| `test_virtual_index_gather.cpp` | Direct streamed-index feeder and 16K admission |
| `test_virtual_tile_consumer.cpp` | Backing publication and dependent consumer visibility |
| `test_hybrid_rmw_soa.cpp` | Vector SoA/JIT update path |
| `test_hybrid_rmw_scalar_soa.cpp` | Scalar broadcast update path |
| `test_hybrid_rmw_old_result.cpp` | Semantically required old-result backing |
| `test_cg_page_fed_soa.cpp` | Four-page product publication and q16 consumption |
| `test_cg_product_handoff.cpp` | Producer/result handoff and completion ownership |

Add a new executable only if a required state transition cannot be expressed
by parameterizing or composing these tests.

## Per-arm gates

Every measured arm must provide:

1. Same binary/input/checkpoint within its comparison family.
2. One terminal ROI, one `m5_exit`, and no fatal text.
3. Exact output, guards, and deterministic work where applicable.
4. Resolved logical/physical geometry and treatment flags.
5. Bounded feeder/response/combiner/write-scoreboard high water.
6. Matching request/response and write/ACK counts.
7. No Row/Offset drain, descriptor replay, or fallback when claiming retained
   16K reordering.
8. `A_FIRST_ISSUE >= ROW_OFFSET_LAST_INSERT` for the strict hybrid.
9. `simTicks` from the intended first ROI, never host time.
10. A storage/control delta and an explicit statement of unmodeled timing.

## Performance interpretation

- `test_virtual_tile_attribution` decides whether the hybrid lies between
  native16 and equal-work native4x4.
- Gather patterns test correctness and sensitivity; they do not all need to
  favor virtualization.
- Flat native16/native4 patterns are coverage tests, not optimization targets.
- A steep pattern where hybrid64 loses to native4x4 is a blocking bottleneck
  finding and must be decomposed before promotion.
- A speedup with changed work, missing tails, or inactive mechanism counters
  is rejected regardless of output equality.

## Scaling ladder

Use this order for future changes:

```text
unit/adversarial C++
  -> existing API micro matrix
  -> NA256
  -> NA1024
  -> one final full candidate/control pair
```

Do not launch a full application for knob search, replacement-policy search,
or basic correctness debugging.
