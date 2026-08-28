# Hybrid versus native4 dominance contract (2026-08-28)

## Rule

A 16K-logical Row/Offset hybrid is not mathematically guaranteed to beat four
native 4K operations: it adds feeder, coherent backing, page publication, and
completion machinery. However, on a workload with meaningful 16K reorder
headroom, a loss to equal-work native4 is a design failure that requires a
measured bottleneck explanation, not an acceptable endpoint.

For equal semantic work, reason about:

```text
T_hybrid - T_native4x4
  = B-ingestion delta
  + A-service delta from 16K versus four 4K reorder windows
  + result-retirement/backing delta
  + synchronization, queueing, and page-publication delta
  - additional overlap
```

The A-service term should favor the hybrid on a steep benchmark. The other
terms are virtualization cost. The hybrid wins only when global reorder and
overlap savings exceed those costs.

## Fail-closed experiment

Use one binary, input, checkpoint, cache/memory geometry, and total element
count:

| Arm | Work |
|---|---|
| Native16 | one native 16K operation |
| Native4x4 | four native 4K operations |
| Hybrid1 | one logical16/physical4 operation, one B line in flight |
| Hybrid64 | one logical16/physical4 operation, 64 B lines in flight |

Before timing, require exact output and conserved semantic work. For the
hybrid arms additionally require:

- all 16K B words and descriptors admitted exactly once;
- `A_FIRST_ISSUE >= ROW_OFFSET_LAST_INSERT`;
- no descriptor replay, Row/Offset drain, or fallback;
- matching backing issues/ACKs and page readiness;
- bounded feeder, response, combiner, and write-scoreboard high water; and
- resolved physical4/logical16 geometry and feeder depth.

## Diagnostic order after a hybrid loss

1. Compare B-fetch duration and actual feeder/request issue pressure.
2. Compare A cache-line requests, DRAM rows/activations, and A duration.
3. Compare backing semantic bytes, transport bytes, transactions, and ACK
   tail.
4. Compare exposed idle, page-ready skew, and consumer start/end.
5. Check whether the workload actually has native16-over-native4 headroom.

Do not optimize a flat workload to prove the architecture. If native16 and
native4 are nearly tied, virtualization overhead can dominate even when the
mechanism is correct.

## Existing evidence

The earlier controlled equal-work reference already follows the expected
ordering:

| Design | `simTicks` |
|---|---:|
| Native16 | 40,062,748 |
| Hybrid physical4/logical16 | 45,282,023 |
| Native4x4 equal work | 60,314,474 |

That hybrid is 1.332x as fast as native4x4 but 13.03% slower than native16.
It demonstrates the intended middle point: less storage than native16, more
reorder opportunity than native4, and nonzero virtualization overhead.

HashJoin PRO being slower than native4 does not refute this contract because
its tile sweep has little reorder headroom and it does not execute the CG
virtual-result edge. The fully bounded 4K Row/Offset design also does not
refute it because descriptor spill/replay removes the retained-16K advantage.

## Current gate

The new same-binary micro matrix must reproduce the four-arm ordering or give
phase evidence for any exception. The selected hybrid64 full-CG pair is final
application verification only; it cannot replace the equal-work micro because
no provenance-matched native4 full arm is being rerun.
