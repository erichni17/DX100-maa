# Bounded index-feeder depth (2026-08-12)

## Decision

Use **32 B/index cache lines with 24 descriptor-read credits** for the next
fully bounded validation. The old descriptor-spool path silently clamped the
B feeder to four cache lines even when a deeper queue was configured. Removing
that clamp improves the exact 16K-logical/4K-physical microbenchmark from
55,772,531 to 46,173,760 `simTicks`, a **17.21% latency reduction**.

This is a bounded-feeder optimization, not evidence that the design has
recovered native16 performance. A full matched CG run is required before making
a workload-level performance claim.

## Why it helps

The fully bounded mechanism performs two sequential B scans and four descriptor
replay passes. With only four B cache lines in flight, the fill stage starves
despite available memory parallelism. Increasing feeder depth allows more B
cache lines to overlap without changing the descriptors admitted, A requests,
or output:

| B/index lines | `simTicks` | Fill ticks | A-request ticks | Delta vs. 4 lines |
|---:|---:|---:|---:|---:|
| 4 | 55,772,531 | 17,217,191 | 30,529,394 | baseline |
| 8 | 48,619,542 | 9,856,996 | 30,554,747 | -12.83% |
| 16 | 47,334,364 | 8,475,414 | 30,637,692 | -15.13% |
| 24 | 46,892,408 | 8,153,337 | 30,525,638 | -15.92% |
| 32 | 46,173,760 | 7,822,496 | 30,527,516 | **-17.21%** |
| 64 | 46,149,346 | 7,442,201 | 30,612,652 | -17.25% |

The knee is 32 lines: 64 lines improve total latency by only 0.053%. Every arm
has output hash `7228541527853630339`, physical-admission digest
`280717e62d330cf4a429e9d0db20b6383f59dfd5aa7de98a1fe44e9ec23b445e`,
and A-issue digest
`4e77ad53f8f7c3a039c2c758e475084e44f9eb064246614df3e7946bcf15192c`.
The speedup therefore comes from overlapping the same B work, not from dropping
or changing requests.

The modeled data storage added between four and 32 lines is 1,792 bytes. Even
including line-state metadata, this is roughly 2 KiB and does not materially
change the design's approximately 71% storage reduction relative to the native
configuration.

## CG transfer check

Two CG arms restored the same checkpoint and ran for the same
1,982,529,733-tick window:

| B/index lines | Total MAA instructions | Indirect instructions | Relative progress |
|---:|---:|---:|---:|
| 16 | 1,047 | 247 | baseline |
| 32 | 1,157 | 273 | +10.51% / +10.53% |

This is liveness and equal-time progress evidence, not a completed-workload
speedup. The durable full CG matrix is running at
`/data1/nier/dx100-runs/2026-08-12-cg-bounded-i32-full` under
`dx100-cg-bounded-i32-full-20260812.service`.

## Rejected adjacent knobs

### More descriptor-read credits

At 32 B lines, increasing descriptor-read credits from 24 to 32 changes
46,173,760 ticks to 46,149,346 ticks, only 0.053%, while descriptor control
storage rises from 3,951 to 4,791 bytes. Keep 24 credits.

### Applying the same change to the hybrid

The payload-only hybrid already has a 16K Row/Offset engine. Deeper B issue
reduces fill latency but does not reduce total latency:

| B/index lines | `simTicks` | Fill ticks | A-request ticks | Delta vs. 4 lines |
|---:|---:|---:|---:|---:|
| 4 | **45,282,023** | 4,179,802 | 35,330,814 | baseline |
| 8 | 45,479,526 | 4,046,777 | 35,141,449 | +0.44% |
| 16 | 45,535,240 | 3,844,266 | 35,399,361 | +0.56% |
| 32 | 45,470,136 | 3,431,732 | 36,558,400 | +0.42% |

At 32 lines, B completes earlier, but RowTable insertion pressure rises from
852 to 21,285 retry events, backing line issues rise from 5,133 to 5,257, and
the A-request phase grows by 1.23 million ticks. Faster B injection creates
contention rather than useful end-to-end overlap. Four lines remains the best
tested hybrid setting.

This also reinforces the prior matched transport sweep: unlimited local
retirement width and 512 write credits changed hybrid `simTicks` by zero, and
the final backing issue-to-ACK drain was only 19,406 ticks. Raw LLC write width,
write credits, and final ACK latency do not explain the hybrid's 13.03% gap to
native16. The remaining gap is primarily producer readiness and scheduling of
A requests, result backing, and consumer page availability.

## RowTable merge answer

The professor's suggested LLC-backed RowTable merge is functionally possible.
The implemented four-run merge retains at most 4,096 active descriptors, sorts
four runs in LLC, and merges four run heads. It produces the exact output and
recovers native16's global A order on the frozen trace (9,523 A-line requests,
129 row groups).

It is not yet a performance solution. The measured live candidate takes
83,208,233 ticks versus 60,538,895 for current bounded paged4, **37.45% slower**,
because sorting/materializing/re-reading the four runs costs far more than the
54 avoided A requests and eight avoided row activations. A future merge design
must avoid full run materialization or exploit a much cheaper hierarchical
summary; the current external sort should not be promoted.

## Provenance

- Simulator binary SHA-256:
  `6ff8c0f077b7d97e1e6240dea7bd7197ba5e92155186fca805c0409f896f89e5`
- Bounded depth roots:
  `/data1/nier/dx100-runs/2026-08-12-bounded-index-depth-fixed-20260812T173229Z`
  and
  `/data1/nier/dx100-runs/2026-08-12-bounded-index-depth-high-20260812T173845Z`
- CG equal-time root:
  `/data1/nier/dx100-runs/2026-08-12-cg-bounded-index-precheck-20260812T174116Z`
- Hybrid depth root:
  `/data1/nier/dx100-runs/2026-08-12-hybrid-index-depth-20260812T174254Z`
- 32-credit root:
  `/data1/nier/dx100-runs/2026-08-12-bounded-i32-c32-fixed-20260812T174642Z`
- Live global-merge root:
  `/data1/nier/dx100-runs/2026-08-10-bounded-global-merge-076d93e9-matrix`
