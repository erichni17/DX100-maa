# Fully Bounded 4K Read-Credit Sweep

## Result

The fully bounded design now beats native 4K on the deterministic indirect
microbenchmark. With 24 descriptor-read credits and 64 classifier words per
cycle, it takes 55,772,531 ticks versus native4's 59,267,176: 5.896% lower
latency, or a 1.063x speedup. It remains 39.667% slower than native16, so this
does not recover the complete 16K reorder opportunity.

This result uses 4K active RowTable entries, 4K active OffsetTable entries,
and a 4K physical result page. Descriptors outside the active population are
kept in coherent LLC-backed storage. The 24 line-read credits add finite
queues and tags; the model reports 3,951 bytes of descriptor-spool control,
145 bytes below the experiment's 4 KiB ancillary-control limit. A 32-credit
point reports 4,791 bytes and is rejected under that limit.

## Matched results

| Configuration | `simTicks` | Versus native4 | Control | Within-pass wait cycles |
|---|---:|---:|---:|---:|
| native16 | 39,932,540 | 1.484x faster | n/a | 0 |
| native4 | 59,267,176 | baseline | n/a | 0 |
| 4 credits, filter 16 | 60,915,747 | 2.782% slower | 1,851 B | 16,771 |
| 8 credits, filter 16 | 57,603,268 | 2.807% lower latency | 2,271 B | 6,740 |
| 16 credits, filter 16 | 56,696,820 | 4.337% lower latency | 3,111 B | 3,325 |
| 24 credits, filter 16 | 56,327,480 | 4.960% lower latency | 3,951 B | 2,085 |
| 24 credits, filter 64 | 55,772,531 | 5.896% lower latency | 3,951 B | 2,085 |

The 24-credit/filter-16 point reproduced exactly at 56,327,480 ticks. All
accepted arms produced output hash `7228541527853630339`. Candidate physical
admissions have digest `280717e62d330cf4a429e9d0db20b6383f59dfd5aa7de98a1fe44e9ec23b445e`,
and issued A requests have digest
`4e77ad53f8f7c3a039c2c758e475084e44f9eb064246614df3e7946bcf15192c`.

## Attribution

The improvement is primarily replay concurrency, not speculative prefetch:

- Raising credits from 4 to 24 reduces the source-routing point by 7.532% and
  reduces within-pass demand-wait cycles by 87.568%.
- On the resident control, raising credits from 4 to 24 reduces latency by
  7.026%. This isolates demand replay concurrency from source routing.
- Cross-pass read-ahead at 24 credits changes the resident point from
  58,939,465 to 58,806,753 ticks, only 0.225% lower latency.
- Source routing changes the 24-credit overlap point from 58,806,753 to
  56,327,480 ticks, another 4.216% lower latency.
- Raising classifier throughput from 16 to 64 words/cycle changes the final
  point from 56,327,480 to 55,772,531 ticks, another 0.985% lower latency.

The 24-credit point's fill interval is 17,519,549 ticks versus 22,116,267 at
four credits, a 20.784% reduction. Request time is nearly unchanged. The
remaining native16 gap is therefore not explained by the replay-credit
serialization that this change removes.

## What ordering is preserved

This mechanism does not hold or globally sort all 16K descriptors at once.
It classifies the logical 16K set into four translated-grow populations, then
replays one bounded population through the 4K physical RowTable at a time.
This preserves coarse ordering across the full logical range and row locality
within each population, but not arbitrary native16 global reorder. Recovering
more of the remaining 39.667% gap requires a lower-traffic cross-population
ordering mechanism, not merely more replay credits.

## Provenance

- Source: `9929dd35e3d47ae882c36bb55c5aca92761ade77`
- gem5 SHA-256: `a93b299dfdd6d2d1aac4cfaa4bbcc0b24886bb29719ba3dc5185ba1773722430`
- Workload SHA-256: `96d274918b1164ed692f452d78761ea96f79c117d35176fb2df0e62453c3e066`
- Ramulator SHA-256: `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`
- native16 checkpoint: `96474070352fa09feb46c7c1030c25d7ee43c1c6096ea78d956648fdea92aec6`
- native4 checkpoint: `6c22ae2074043106da17f34063e755c56058e56456ef1e8d49d993bf5e94a007`
- virtual4 checkpoint: `13e449567f118ef62ced754608b7fc36ba4cc216ea919491cd631088e825540a`
- Primary root: `/data1/nier/dx100-runs/2026-08-10-bounded-read-credits-shared-9929dd35`
- Replica/combined root: `/data1/nier/dx100-runs/2026-08-10-bounded-read-credits-followup-9929dd35`

The frozen checkpoints are byte-identical across the credit matrices. Every
matrix completed without a wall-clock timeout and passed the exact-output,
physical-admission, source-issue, and mechanism-counter validators.
