# SoA/JIT fixed-4K result pipeline audit (2026-08-14)

## Decision

Accept the 64-context mode as a **default-off, fixed-4K two-region capacity
option**, while rejecting the narrower hypothesis that the lead 32-context
path serializes the whole result window at LLC publication.

The existing path already sends each completed 64-byte A line as a
response-bearing coherent `WriteReq` while unrelated contexts may retain or
issue outstanding A reads. A context is not released until its exact-address
`WriteResp`, and instruction completion waits for every A read, A write, value
fill/delivery, and response ledger. Consequently there is no global 4-KiB
publisher drain to split. An ordinary dependent consumer cannot reuse the
result before instruction completion without changing the API dependency and
coherence contract, so no early-consumer bypass or direct-sink special case was
added.

The implemented mode instead provisions the complete physical result budget as
two 32-line ownership regions. It keeps line payload and response authority in
the existing contexts; the new pipeline object only observes exact state
transitions and owns no data. Runtime defaults remain unchanged. Selecting
64 contexts activates both 2-KiB regions; selecting 32 or fewer leaves the
second region inactive. Duplicate ordering, masked-index semantics, response
ownership, coherent cache traffic, terminal drain, and ordinary paths are
unchanged.

## Hardware-byte accounting versus the lead 32-context design

| Increment | Bytes | Classification |
|---|---:|---|
| 32 additional 64-byte A-line payloads | 2,048 | payload |
| Metadata in 32 additional 416-byte contexts | 11,264 | non-payload/context |
| Additional value-coalescer waiter identity bits (256 to 512 waiters across 128 lines) | 4,096 | non-payload/context |
| Apply-lane ownership storage | 0 | the four-owner array is unchanged |
| Additional modeled non-payload/context state | **15,360** | charged area |
| Additional modeled state including payload | **17,408** | charged area |

The fixed result-context array is 26,624 bytes: exactly 4,096 payload bytes and
22,528 context metadata bytes. The corresponding lead-32 context array is
13,312 bytes: 2,048 payload bytes plus 11,264 metadata bytes. Waiter masks are
8,192 bytes in the 64-context design versus 4,096 bytes in the lead design.
The observer's counters are simulator instrumentation rather than modeled
datapath state and add zero hardware bytes. There is no hidden logical-window
buffer and masked indices add zero storage.

## Exact API micro (authoritative r3)

Both arms used the same optimized binary, shared checkpoint, logical16 /
physical4K geometry, active value owners 64, pre-A enabled, and two exact
replicas. Only active contexts changed from 32 to 64; timeout was disabled.

| Arm | ROI simTicks | Context stalls | Read/write overlap ticks | Dual-region overlap ticks | Write-only ticks |
|---|---:|---:|---:|---:|---:|
| context32 | 49,640,235 | 34,079 | 289,212 | 0 | 644,467 |
| context64 | 48,472,745 | 0 | 0 | 0 | 693,608 |

Each replica was bit-exact (`output_hash=2761840269561229581`) and produced the
same row. Treatment improved ticks by 1,167,490, or 1.024085494x (2.3519%
lower ticks). This small case does not exercise cross-region read/write
overlap: its benefit is removal of context-capacity stalls, and its overlap
counter is explicit evidence against attributing the gain to a publisher
pipeline.

Evidence:
`/data1/nier/worktrees/codex-coordination/sessions/hybrid-result-pipeline-20260814-20260814-161653-3ee2f93e/evidence/soa-jit-result-pipeline-r3`.
The recorded gem5 SHA-256 is
`1b63e3450f910effcbea29a661c07eb9d6531e5b8d6ca8d6487fc13a613e8af7`;
the guest SHA-256 is
`c7fb4f8dd038cb129115f11a11390aa672bd4e9fba4f05573e4aa257e089c497`.

## Exact full-GZP promotion gate

The gate used the accepted masked-index publisher, pre-A enabled, active value
owners 64, the same binary/guest/checkpoint/selector/Ramulator inputs in both
arms, two replicas per arm, and no timeout. Only active contexts changed.

| Arm | ROI simTicks | Context stalls | A reads/writes | Value reads | Selected/rejected |
|---|---:|---:|---:|---:|---:|
| context32 | 6,816,306,794 | 3,422,045 | 509,830 / 509,830 | 822,961 | 949,411 / 50,013 |
| context64 | 6,634,051,589 | 2,822,582 | 509,830 / 509,830 | 823,662 | 949,411 / 50,013 |

Every row was identical across its two replicas. Treatment improved ROI ticks
by 182,255,205, or 1.027472684x (2.6739% lower ticks), while removing 599,463
context stalls (17.52%). All 509,830 A reads, 509,830 A writes, and their
responses balanced exactly. Value issues/responses/fills balanced within each
arm; all 949,411 selected values were delivered in exact order. Pre-A
issue/use ledgers also balanced (949,033 at context32, 937,535 at context64).
Both arms matched the expected output hash `11225737641199706160`, reported zero
reference errors and nonfinite values, completed all 61 windows, and published
no predicate buffer.

Across the 61 operations, context32 recorded 951,556,934 read/write-overlap
ticks and zero dual-region ticks. Context64 recorded 765,148,532 read/write
overlap ticks, of which 762,445,151 had traffic live in both ownership regions.
Thus the second region is genuinely exercised concurrently, but the lower
total overlap duration and the stall reduction show that the measured speedup
comes from additional live-line capacity, not from repairing a serialized LLC
publication phase.

Evidence:
`/data1/nier/worktrees/codex-coordination/sessions/hybrid-result-pipeline-20260814-20260814-161653-3ee2f93e/evidence/gzp-result-context-gate-r1`.
The runner recorded checkpoint SHA-256
`1d617cd45b1835a3a11f39ae6b002efd12e33c2ed11cb6a98a3814f1caa4999e`,
guest SHA-256
`00980813e3bbcd74aec84d4352c545f5ff956485cac99c456fadfddfcab8ecda`,
selector SHA-256
`32ebe0418fb690b057b08babaf5d1e7b05e65705f2c6ec776576cd810e86190a`,
and the same gem5 SHA-256 as the API micro.

## Promotion boundary

The repeated exact 2.35% API and 2.67% full-GZP improvements justify the
15,360-byte non-payload charge for this targeted option. Keep 64 contexts
default-off and describe it as result-context capacity / two-region ownership.
Do not claim a general early-publication optimization, do not enable dependent
consumer reuse before exact terminal completion, and do not extrapolate this
single full workload to other applications without matched gates.
