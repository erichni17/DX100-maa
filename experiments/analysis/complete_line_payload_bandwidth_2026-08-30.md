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

Widths 0/2/4/8 and the FLAG control curve use simulator source
`df6a9576bac106848e839c28b8368d760e40dedd` and binary SHA-256
`733c2b64858f844bd78301cb78626196f184a1801af74bdc1479fcbe1e53ab0a`.
The width-1 liveness rerun adds only response-aware retry at
`6a6737a5eef3ef84fcd355bac5d4eb5729ff5d7e`, binary SHA-256
`8799671e25abfb230767780bd4ab58e45421b41749c72e55a2acc149368392aa`.
The default-off masked-line extension uses simulator source
`40762e0d9a135ac7a3ea07f69ecf1b9f502b7116`, binary SHA-256
`e79bcf18fb520fe1e68f9d2efcc67dda568c8a564182663c14a1c51194869893`.

## XRAGE

Native16 remains the historical 42,312,279-tick reference. It is not an
attribution control for this experiment because the XRAGE hybrid also uses the
separately identified direct-retirement/fusion optimization. The valid control
here is width 0, the same hybrid with ideal payload-copy bandwidth.

| Words/cycle | `simTicks` | Versus ideal hybrid | Versus native16 | Result |
|---:|---:|---:|---:|---|
| 0 | 37,407,256 | 0.000% | -11.592% | exact control |
| 1 | 43,123,888 | +15.282% | +1.918% | exact after response-aware retry; rejected on performance |
| 2 | 37,645,136 | +0.636% | -11.030% | exact |
| 4 | 37,409,134 | +0.005% | -11.588% | exact |
| 8 | 37,406,317 | -0.003% | -11.595% | exact |

Each finite arm staged and completed exactly 8,192 full lines. The measured
read-cycle totals were exactly 65,536, 32,768, 16,384, and 8,192 at widths 1,
2, 4, and 8. Width 1 incurred 24,259 producer-backpressure cycles. Widths 2,
4, and 8 incurred none.

Raw campaigns:

- widths 0/2/4/8:
  `/data1/nier/dx100-runs/2026-08-30-xrage-complete-line-payload-r2`;
- width 1 after response-aware retry:
  `/data1/nier/dx100-runs/2026-08-30-xrage-payload-width1-retry-r1`.

## FLAG

All points use the same binary and all 14 LANL gather configurations. Every
arm has exact output hashes and exact line/tail, lookup, staging, and
write-response closure.

| Words/cycle | Geomean versus ideal | Worst case | Backpressure cycles |
|---:|---:|---:|---:|
| 1 | +3.245% | +21.845% | 37,464 |
| 2 | +0.441% | +3.140% | 2,036 |
| 4 | +0.003% | +0.079% | 0 |
| 8 | +0.005% | +0.066% | 0 |

At widths 1 and 2, 12 of 14 cases are effectively unchanged. Width 1's two
outliers lose 21.845% and 21.806% and account for all 37,464 backpressure
cycles. Width 2 reduces those losses to 3.056% and 3.140% and backpressure to
950 and 1,086 cycles. Width 4 eliminates that pressure. Width 8 has no
measurable advantage over width 4.

Raw campaigns:

- control: `/data1/nier/dx100-runs/2026-08-30-flag-payload-control-bank4-r2`;
- width 1: `/data1/nier/dx100-runs/2026-08-30-flag-payload-width1-bank4-r1`;
- width 2: `/data1/nier/dx100-runs/2026-08-30-flag-payload-width2-bank4-r2`;
- width 4: `/data1/nier/dx100-runs/2026-08-30-flag-payload-width4-bank4-r1`;
- width 8: `/data1/nier/dx100-runs/2026-08-30-flag-payload-width8-bank4-r1`.

## Cross-application scope

A matched `CG_NA=256` strict line-combined pair is exact at width 0 and at the
32-byte/cycle CG setting (eight 4-byte words). Ticks increase from 246,463,712
to 246,594,233, or 0.053%. This is a scope diagnostic, not general CG payload
validation: only 5 of 26,672 masked P writes are fully populated lines, so the
staging mechanism charges only 5 lines and 10 read cycles. The dominant
partial-mask retirement path remains outside this complete-line study.

Raw CG roots:

- control: `/data1/nier/dx100-runs/2026-08-30-cg-na256-payload-control-r1`;
- 32-byte port: `/data1/nier/dx100-runs/2026-08-30-cg-na256-payload-width8-r1`.

The existing cross-application source audit also shows that NAS IS and
HashJoin do not execute the virtual-result combiner edge, while SSSP uses a
separate response-bearing old-result publisher. No suite-wide payload-port
claim is made for those paths.

### Masked-line extension

A default-off extension applies the same finite port to every masked CG line,
not only fully populated lines. At `CG_NA=256`, all arms preserve the exact
fingerprint, 11 deterministic reductions, 26,672 write issues/completions, and
strict mechanism closure:

| Payload bytes/cycle | `simTicks` | Versus ideal | Starts/completions | Read cycles |
|---:|---:|---:|---:|---:|
| ideal | 246,463,712 | 0.000% | 0 | 0 |
| 4 | 298,239,233 | +21.007% | 26,672 | 163,840 |
| 8 | 276,104,812 | +12.027% | 26,672 | 88,815 |
| 16 | 263,114,373 | +6.756% | 26,672 | 51,656 |
| 32 | 257,943,613 | +4.658% | 26,672 | 33,644 |

The promoted `CG_NA=1024` same-checkpoint pair is also exact. The ideal arm is
1,247,488,418 ticks; the 32-byte arm is 1,366,470,047 ticks, or +9.538%.
It stages and completes all 358,114 masked writes in 358,810 read cycles with
975 blocked-line cycles and zero producer-backpressure cycles.

Raw masked-CG roots are named
`/data1/nier/dx100-runs/2026-08-30-cg-na{256,1024}-payload-*`.

Multi-line pipelining was tested at 1/2/4/8/16 active identities. Four is the
small-size knee, but improves the larger `CG_NA=1024` result by only 0.063%.
The selected design therefore retains one identity. See
`masked_payload_pipeline_2026-08-30.md`.

## Decision

Select four FP64 words, or 32 bytes, per MAA cycle. It matches the existing
MAA crossbar width, is effectively timing-identical to the ideal payload-copy
model on XRAGE and FLAG, and avoids the unnecessary 64-byte/cycle width-8
path. Width 2 is a viable lower-bandwidth alternative if a 3.14% FLAG tail is
acceptable.

Width 1 is not selected. Increasing tag capacity, associativity, or assigning
the full 4K word budget to the combiner did not prevent partial-line pressure.
Response-aware retry makes it legal without partial writes, but the resulting
15.282% XRAGE cost and 24,259 backpressure cycles are too high. This retry is
retained as a liveness fix, not as the selected performance point.

## Remaining boundary

This closes aggregate payload-read throughput without adding hidden payload
capacity. It does not yet synthesize the RAM or prove a conflict-free physical
bank mapping for arbitrary payload references. Area, energy, Fmax, and exact
payload-bank conflicts remain open implementation questions. The masked-line
extension remains a correctness and bandwidth-attribution result; its tested
multi-line optimization did not materially reduce the larger-case cost.

Artifact ledger:
`complete_line_payload_bandwidth_artifacts_2026-08-30.sha256`.

An independent read-only review found no source correctness, liveness, or
hidden-payload defect. It identified one P2 experiment-gate gap: the generic
runner required only a positive staging-cycle count. The accepted runner now
requires the exact `full_lines * ceil(8 / width)` count.
