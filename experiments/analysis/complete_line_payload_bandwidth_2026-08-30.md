# Complete-line payload bandwidth (2026-08-30)

## Question

The selected hybrid had bounded result words, tags, lookup latency, combiner
banks, write credits, and line-drain issue width, but copying a completed line
out of the combiner was still instantaneous. This study asks how much internal
read bandwidth is needed to assemble one 64-byte coherent write.

This is not a new payload buffer. The payload remains in the existing bounded
combiner store. One staging identity records the operation generation, slot,
line address, valid mask, and read progress. After all words are read, the LLC
write is issued without waiting for its acknowledgement and the next line may
start staging.

## Fixed design

- logical reorder scope: 16K;
- physical result payload: 4,096 FP64 words;
- XRAGE split: 2,560 combiner words + 1,024 response words;
- FLAG split: 3,072 combiner words + 1,024 response words;
- 8-way XOR7 tags, four combiner banks, three-cycle lookup;
- one completed-line write issue per cycle and bounded page-ready selection;
- exact write-response identity and direct-producer ownership guard.

The treatment is the number of FP64 payload words read per MAA cycle. Width 4
is 32 bytes/cycle, equal to the configured noncoherent MAA crossbar width
(`maa_ncbus_width=32`).

## XRAGE

Native16 remains the historical 42,312,279-tick reference. It is not an
attribution control for this experiment because the XRAGE hybrid also uses the
separately identified direct-retirement/fusion optimization. The valid control
here is width 0, the same hybrid with ideal payload-copy bandwidth.

| Words/cycle | `simTicks` | Versus ideal hybrid | Versus native16 | Result |
|---:|---:|---:|---:|---|
| 0 | 37,407,256 | 0.000% | -11.592% | exact control |
| 1 | n/a | n/a | n/a | rejected on partial-line payload pressure |
| 2 | 37,645,136 | +0.636% | -11.030% | exact |
| 4 | 37,409,134 | +0.005% | -11.588% | exact |
| 8 | 37,406,317 | -0.003% | -11.595% | exact |

Each successful finite arm staged and completed exactly 8,192 full lines. The
measured read-cycle totals were exactly 32,768, 16,384, and 8,192 at widths 2,
4, and 8. No successful XRAGE arm reached result-payload backpressure.

Raw campaign:
`/data1/nier/dx100-runs/2026-08-30-xrage-complete-line-payload-r2`.

## FLAG

All points use the same binary and all 14 LANL gather configurations. Every
arm has exact output hashes and exact line/tail, lookup, staging, and
write-response closure.

| Words/cycle | Geomean versus ideal | Worst case | Backpressure cycles |
|---:|---:|---:|---:|
| 2 | +0.441% | +3.140% | 2,036 |
| 4 | +0.003% | +0.079% | 0 |
| 8 | +0.005% | +0.066% | 0 |

At width 2, 12 of 14 cases are effectively unchanged. The two outliers lose
3.056% and 3.140% and account for all 950 and 1,086 backpressure cycles. Width
4 eliminates that pressure. Width 8 has no measurable advantage over width 4.

Raw campaigns:

- control: `/data1/nier/dx100-runs/2026-08-30-flag-payload-control-bank4-r2`;
- width 2: `/data1/nier/dx100-runs/2026-08-30-flag-payload-width2-bank4-r2`;
- width 4: `/data1/nier/dx100-runs/2026-08-30-flag-payload-width4-bank4-r1`;
- width 8: `/data1/nier/dx100-runs/2026-08-30-flag-payload-width8-bank4-r1`.

## Decision

Select four FP64 words, or 32 bytes, per MAA cycle. It matches the existing
MAA crossbar width, is effectively timing-identical to the ideal payload-copy
model on XRAGE and FLAG, and avoids the unnecessary 64-byte/cycle width-8
path. Width 2 is a viable lower-bandwidth alternative if a 3.14% FLAG tail is
acceptable.

Width 1 is not selected. Increasing tag capacity, associativity, or assigning
the full 4K word budget to the combiner did not prevent partial-line pressure.
A response-aware retry policy is being tested as an optional lower-cost point;
it is not required by the selected width-4 design.

## Remaining boundary

This closes aggregate payload-read throughput without adding hidden payload
capacity. It does not yet synthesize the RAM or prove a conflict-free physical
bank mapping for arbitrary payload references. Area, energy, Fmax, and exact
payload-bank conflicts remain open implementation questions.
