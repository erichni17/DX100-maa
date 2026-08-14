# SoA/JIT CPU value-warm diagnostic (2026-08-14)

Raw evidence is frozen at
`/data1/nier/dx100-runs/2026-08-14-soa-jit-value-warm-probe`.
The runner is `experiments/scripts/run_soa_jit_value_warm_probe.sh`.

## Scope and frozen inputs

The guest source is commit `1709888a4376650d773a48e3021fc8fa32b99336`
(`45a24552de8faf3897e34a8b4f67694d9e2a15cd5d2e31a4360f5675ed008918`).
The guest binary SHA-256 is
`c7fb4f8dd038cb129115f11a11390aa672bd4e9fba4f05573e4aa257e089c497`.
The lead gem5 binary SHA-256 is
`cd1217648670fde4bb84d7b1a4fe2346099e02d0cc30d1d78edaaabaec2eefa2`;
the SE config and Ramulator config SHA-256s are respectively
`aacc6e624b7ab0e7b032d5cb913974fa790efdca84598bf468c11f14b9575d0f`
and `aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b`.

All three arms use metadata/logical 16K, physical 4K, one MAA indirect unit,
and the same cache/memory knobs. The only requested guest-treatment delta is
the mode:

| Arm | Mode | Charged loop immediately before each JIT op |
| --- | --- | --- |
| baseline | `soa` | none |
| dummy control | `soa-warm-control` | one volatile word per 64-B line of a disjoint 16K dummy array |
| values warm | `soa-warm` | one volatile word per 64-B line of the operation's 16K values array |

The loops touch exactly 1,024 lines per operation. Their volatile checksums
are stored inside the ROI, then checked and printed only after
`m5_dump_stats`/`m5_work_end`; no host-output activity is placed between a warm
loop and its JIT operation. The dummy array is initialized before checkpoint
and is intentionally not registered as an MAA memory region because only the
CPU treatment accesses it.

Each mode requires its own checkpoint: the mode is parsed before the guest's
`m5_checkpoint`, so one checkpoint cannot cleanly select all three treatments
on restore. This is a comparability limitation recorded in `manifest.txt`.

## Exact terminal and correctness evidence

All arms have `selected=29689`, `rejected=3079`, two terminal completions,
`IND_SoaJitValueReadIssues=IND_SoaJitValueReadResponses=29689`, context-high-
water `2`, context stalls `0`, and exact output/expected hash
`2761840269561229581` with `errors=0`.

| Arm | simTicks | terminal m5_exit tick | Trace SHA-256 |
| --- | ---: | ---: | --- |
| baseline | 807872406 | 5420124909 | `279aa89027139f94b72faeea3ecea3bf00cab380026e51fbc3eaf9cdefc05202` |
| dummy control | 818330049 | 5439302732 | `aa6f356c08473e1208f91fb7e66ad1ddb1c8a4c8aafc6798969f220360c6f601` |
| values warm | 869822618 | 5489876959 | `43ea30d2f1dafb8a9490607f7c9e35e6f17f0940128289b3786e034b80e1c91a` |

The post-ROI checksum lines are exact: dummy operation 0/1 are
`2109566091264`/`2107394490368`; values operation 0/1 are
`1907688996864`/`1901198311480`. Each reports `lines=1024` and equals its
post-ROI recomputation.

## Trace timing breakdown

For each generation, `pre-first-A` is first `soa_jit_a_read_issue` minus its
`operation_tick`; `post-first-A` is `soa_jit_complete` minus that first A
issue. These are trace-derived operation intervals, not a claim that they
cover unrelated CPU/warm-loop time outside the MAA operation.

| Arm | Generation | pre-first-A ticks | post-first-A ticks |
| --- | ---: | ---: | ---: |
| baseline | 1 | 151268831 | 253115901 |
| baseline | 2 | 151289802 | 250679196 |
| dummy control | 1 | 151608123 | 253699959 |
| dummy control | 2 | 151607810 | 248956131 |
| values warm | 1 | 151634415 | 279045134 |
| values warm | 2 | 151618139 | 275807775 |

The summed values-warm versus dummy-control change is +36621 pre-first-A
ticks (+0.012078%) and +52196819 post-first-A ticks (+10.384201%). Total ROI
simTicks are +1.294467% for dummy-control versus baseline and +6.292396% for
values-warm versus dummy-control.

## Observed LLC value-line behavior

The L3's requestor/role counters identify roles 9 and 12 as the two operation
value streams: their access counts sum exactly to the 29,689 JIT value reads.
In the baseline they report 27,641 MAA hits and 2,048 MAA misses, or a 93.1%
observed LLC hit rate. The 2,048 misses are exactly one miss for each of the
1,024 sequential value lines in each operation. In the values-warm arm, all
29,689 later MAA value accesses are hits; the same 2,048 line fills are instead
attributed to the CPU warming loads.

Despite eliminating the later MAA misses, values-warm increases summed
`IND_CyclesRequest` from 1,605,930 cycles in the dummy control to 1,772,693
cycles. This CPU treatment therefore is not a model of a free hardware
prefetch: accessing an MAA-registered region from the CPU can introduce
coherence/invalidation and timing effects absent from an internal prefetcher.
The counters do establish a narrower result: most current value requests
already wait on LLC hits, so overlapping those serialized hit responses is a
higher-priority optimization than merely eliminating the first miss per line.

## Interpretation

This narrow CPU-warm experiment did not show an acceleration: values-warm is
slower than the matched dummy-control, and the trace attributes nearly all of
that delta to post-first-A time. It is diagnostic evidence only. The CPU loop
is not the intended hardware prefetcher, the dummy loop has its own cache-
pollution treatment, and separate per-mode checkpoints prevent a shared-
checkpoint performance claim. Do not use this result to claim a hardware
prefetcher benefit or regression without a matched hardware implementation and
shared-checkpoint-capable evidence.
