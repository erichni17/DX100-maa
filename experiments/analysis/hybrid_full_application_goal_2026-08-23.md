# Hybrid full-application goal checkpoint - 2026-08-23

## Objective

Optimize the accepted DX100 hybrid with a 16K logical Row/Offset reorder scope
and 4K physical payload. Reuse valid ordinary native16/native4 evidence,
isolate backed-path virtualization overhead where required, expand beyond GZP,
and pursue a single-digit native16 gap on at least two full applications.

The retained 16K reorder structures are intentional. A 4K physical RowTable is
a secondary path toward logical tiles larger than the maximum physical reorder
table, not a requirement for this hybrid.

## Existing baseline evidence

Ordinary native16/native4 data does not need to be regenerated when its binary,
input, configuration, ROI, and correctness identity match the new comparison.
The completed 77-point physical tile sweep already supplies validated capacity
curves for 11 workloads.

The extra backed16/backed4 comparison is needed only when the hybrid changes the
instruction path. It separates physical-payload virtualization from staging or
API changes:

1. ordinary native16;
2. ordinary native4;
3. backed instruction path with physical16; and
4. the identical backed instruction path with physical4.

Only 3-versus-4 isolates virtualization overhead.

## Current accepted results

### API

Across two exact checkpoint instances, the 16K-reorder/4K-payload hybrid is
3.04-7.42% slower than native16 and 1.48-1.55x faster than native4. The
configured capacity lower bound is 873.28 KiB versus 2.30 MiB for native16, a
62.875% reduction. This is a capacity result, not synthesized area.

### Full GZP scheduler composition

The exact six-restore matrix at
`/data1/nier/dx100-runs/2026-08-23-gzp-combined-optimization-46693906-r1`
completed with exact replicated endpoints, output hash
`11225737641199706160`, 61 terminal windows, and closed A/value/write ledgers.

| arm | simTicks | adjacent result |
|---|---:|---:|
| masked owner32, pre-A off | 7,033,542,566 | baseline |
| masked owner32, pre-A on | 6,855,742,603 | 2.528% lower latency |
| masked owner64, pre-A on | 6,816,306,794 | another 0.575% lower |
| masked owner128, pre-A on | 6,835,912,488 | 0.288% regression |

Owner64 plus pre-A is the selected point: 3.089% lower latency than the matrix
baseline. The separate exact context32/context64 gate reduces the selected
control from 6,816,306,794 to 6,634,051,589 ticks, making the chained optimized
point 5.679% lower than masked owner32/pre-A-off. Context64 adds 17,408 bytes of
modeled state and still needs a banked timing implementation before a 3.2-GHz
physical claim.

### CG context capacity

The first exact context32/context64 restores both produce 2,978,885,165 ticks
and identical fingerprints. Context64 therefore has no effect on this CG
configuration. A runner-only config-name normalization bug blocked the formal
matrix; commit `9e15186c` fixes that gate and a clean rerun is active.

The separate CG response-bearing publisher is a valid negative result:
3,012,242,827 versus 2,992,566,395 ticks, or 0.657% slower, with 25,792
publisher-credit stalls per stream and zero overlap issues.

## Full-application readiness

| workload | ordinary native controls | hybrid status | next action |
|---|---|---|---|
| API | exact native16/native4 | complete | retain as the single-digit reference |
| UME GZP | exact native16/native4 | complete and optimized | add matched backed16/backed4 attribution if the API changes |
| NAS CG | exact native16/native4 | logical16 RMW active | finish corrected gate; optimize only measured CG bottlenecks |
| UME GZZ | exact native16/native4 | general hybrid binary and runner wired | launch full matrix with exact UME fingerprint/reference |
| XRAGE | validated controls | non-fused hybrid runner exists | run general non-fused hybrid attribution; keep direct sink separate |
| GAPBS PR/BFS | exact native16/native4 | not wired | virtualize downstream tile-sized intermediates before claiming hybrid performance |
| HashJoin | physical sweep complete | no general hybrid runner | audit PRH/PRO instruction pattern and port only if the hybrid operation is legal |

## Immediate milestones

1. Close the corrected CG context matrix and strict split-2K gate.
2. Launch full UME GZZ and XRAGE general-hybrid matrices with existing exact
   oracles.
3. Assemble reusable ordinary baselines rather than rerunning identical points.
4. Rank cross-application stalls and implement the smallest general treatment.
5. Commit every coherent mechanism, gate, accepted result, and explicit
   rejection locally before proceeding to the next design.

## Promotion rule

No speedup is promoted from an incomplete run, unmatched binary/input, changed
semantic work, missing exact output, open traffic ledger, or uncharged hidden
storage. A treatment must improve repeated simulated performance on at least
one full application without regressing the other validated target beyond its
deterministic spread. The final goal requires a single-digit native16 gap on at
least two full applications, not only an API microbenchmark.
