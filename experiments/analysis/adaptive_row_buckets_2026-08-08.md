# Adaptive row/address buckets for true 4K metadata

## Decision

The finite candidate is a 256-bin, 16-bit-counter radix histogram with a
bounded recursion stack and at most eight pass descriptors. It never retains a
16K label array. On the frozen XRAGE 16K window it uses 633 B of selector state
and produces five source-line-proxy ranges with populations
`4072/4096/4080/4012/124`. Every pass fits 4,096 active Row/Offset entries.

This is a mechanism and hardware-state result, not a performance result. No
core MAA behavior was edited, no simulated timing was predicted, and exact
offline quantiles were not promoted as implementable boundaries.

## Frozen XRAGE proxy result

Input:
`/data1/nier/dx100-runs/verified/2026-07-24-xrage-retirement-cache-cost-57b2ad3/inputs/benchmark/xrage_20k.json`,
SHA-256
`7cb86c456e11f32ea4664510c43b519af6fac3e3bfa1bc86f95f330ca230c136`.
Only its first 16,384 indices are analyzed. An FP64 source cache-line ID is
`B[i] // 8`; it is a locality proxy, not a DRAM-row decoder.

| Policy | Pass populations | Selector state | Full B scans | Sum source-line requests | Gate |
| --- | --- | ---: | ---: | ---: | --- |
| Sequential iteration chunks | 4096/4096/4096/4096 | 5 B | 1 | 2310 | accept |
| Static full-array quarters | 16384/0/0/0 | 14 B | 4 | 2169 | reject: 16,384 > 4,096 |
| Source-line modulo 4 | 4129/4010/4122/4123 | 5 B | 4 | 2169 | reject: max 4,129 |
| Exact offline line quantile | 4096/4096/4096/4096 | at least 49,163 B | 5 | 2169 | reject: offline sort/oracle and state |
| Online coarse radix range | 4072/4096/4080/4012/124 | 633 B | 8 | 2169 | accept |

The full window has 2,169 unique proxy lines. Iteration chunks repeat 141 of
those lines across chunk boundaries, giving 2,310 idealized line requests. The
exact diagnostic boundaries are upper-inclusive line IDs
`36930/37514/38101`; their balance is an upper diagnostic only.

The coarse state ledger is 512 B of reusable histogram counters, 88 B for
eight bounded pass descriptors, 27 B for a three-level recursion stack, and
6 B of cursor/count/control state. On this input, one initial 16,384-word scan
and two refinement rescans find the ranges. Counter walking costs 768 cycles
with two comparators (1,536 comparator evaluations). Five replay scans bring
the scan-only lower bound to 131,072 one-word-per-cycle cycles and 8,192 B-line
reads, of which 7,168 repeat the initial traversal. These are explicit work
charges, not a cache-latency or speedup estimate.

Each recursive split consumes up to eight more address bits. The configured
18-bit source-line domain therefore needs at most three stack levels. If a
singleton address still exceeds 4,096 occurrences, recursion terminates by
stable logical-iteration occurrence chunks; the adversarial all-identical-key
test produces four exact 4K passes.

## Authenticated physical diagnostic

The submitted
`/data1/nier/dx100-runs/2026-08-03-virtualization-integration/bounded-range-4bf5ef5/physical_admission_records.jsonl`
is authenticated by its adjacent `physical_validation.json`: 16,384
`dx100.physical_admission.v1` records and raw SHA-256
`2803564faba235362e4ffe1b33cec0fecbe52860bd86261369082fcb977f7605`.
It is a separate bounded-range workload, not the frozen XRAGE input. Its row
metrics are therefore reported as a physical policy diagnostic and are not
substituted for XRAGE's proxy-only result.

The trace has nine grow values with populations
`1785/2058/2026/2028/2026/2027/2028/2026/380`, 9,523 unique physical A lines,
and 129 unique decoded `(channel, rank, bank-group, bank, row)` identities.

| Physical diagnostic policy | Populations | A-line requests | Decoded row identities across passes | Epochs / capacity drains | Replay scans |
| --- | --- | ---: | ---: | --- | ---: |
| Static four unsplit ranges | 5869/4054/4055/2406 | 9523 | 129 | not run after capacity rejection | 4 |
| Variable whole-grow packing | 3843/4054/4053/4054/380 | 9523 | 129 | 5 / 0 | 5 |
| Paired grows + split grow-21 tail | 4096/4096/4096/4096 | 9582 | 136 | 4 / 0 | 4 |
| Sequential iteration chunks | 4096/4096/4096/4096 | 16384 | 516 | 8 / 4 row-slot drains | one sequential traversal |

Whole-grow packing uses 574 B of selector state and preserves every grow as an
indivisible unit. It needs five replay passes. Relative to the split four-pass
policy, that is one extra replay, 1,024 extra B-line reads, and 16,384 extra
one-word-per-cycle scan cycles.

The 562 B four-pass alternative greedily pairs the eight main grow groups into
populations `4086/4055/4052/3811`. Stable logical-iteration occurrence quotas
split only grow 21 into the remaining gaps `10/41/44/285`. This exact balance
costs 59 repeated A-line requests and seven repeated decoded row identities
across passes compared with whole-grow packing. The boundary model charges a
256-counter walk plus a bounded 36-comparison pairing step: 292 selection
cycles, two comparators, and 548 comparator evaluations.

The included finite-table replay models 16 slices, 32 rows per slice, eight
lines per row, 4,096 Offset/line entries, and a 480-word per-line limit. It
reproduces zero capacity drains for both adaptive physical policies and four
row-slot drains for sequential chunks. This is analytical mechanism evidence
on authenticated physical records; it is not gem5 `simTicks`, Ramulator
ACT/PRE command evidence, or a performance claim.

## Reject conditions

A policy is rejected if any pass exceeds 4,096 entries, coverage is not exact,
selector state exceeds the 1,024 B budget, selection requires an unmodeled
offline oracle, recursion lacks singleton-key termination, or DRAM-row claims
are made without matching authenticated physical records. Promotion would
also require a timing-visible implementation, exact output correctness, and a
matched gem5 experiment; none is claimed here.

The machine-readable result is
`experiments/evidence/2026-08-08_adaptive_row_buckets.json`; the reproducible
analyzer is `experiments/analysis/analyze_adaptive_row_buckets.py`.
