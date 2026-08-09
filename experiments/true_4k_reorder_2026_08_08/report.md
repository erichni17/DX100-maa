# True-bounded 16K gather with a 4K active reorder set

## Finding

The narrow viable slice is a timing-visible translated-DRAM-grow summary,
followed by four full B replays whose admissions are capped at 4,096. A fixed
64-record by 64-pass quota table pairs whole grows in descending-population
order and can split multiple grows by deterministic replay ordinal to fill
pass gaps. If the summary or plan cannot represent the distribution, the
implementation falls back to contiguous 4K iteration ranges; the intended
physical-grow evidence arm rejects that fallback. It never retains a 16K Word
or Offset descriptor array.

This is a correctness/build candidate, not a promotion. Only matched gem5
`simTicks`, exact hashes, and translated-row counters can support a locality or
latency conclusion.

## Terminology and charged state

The existing `OffsetTableEntry` physically co-resides several concepts. This
report accounts for their architectural meanings separately:

| Organization | Candidate bound | Semantic bytes | Meaning |
|---|---:|---:|---|
| Word Table fields | 4,096 | 32,768 | `(iteration, word-id)` response descriptors |
| Offset additions | 4,096 | 36,864 | next link, valid bit, and bounded free-stack id |
| RowTable row directory | 512 | 5,120 | grow key plus valid/sent state |
| RowTable line directory | 4,096 | 73,728 | line key, first/last links, valid/claimed state |
| Retained grow plan | 64 records x 64 passes | 9,370 | grow/count arrays, record/pass quotas, and replay ordinals |
| Exact-once checker | 16K bits twice | 6,758 | non-functional admission/retirement audit plus pass counters |
| Scratchpad payload | 4,096 elements/tile | 524,288 for 32 visible tiles | data payload, not reorder metadata |

Word/Offset/Row reorder metadata is 148,480 semantic bytes; adding the
functional grow plan gives 157,850 charged mechanism bytes. The 6,758-byte
non-functional exact-once checker raises the instrumented total to 164,608
bytes. The candidate allocates exactly one active
RowTable organization; RowTable is a row/line directory, not a 16K payload
table. Response slots and the destination combiner remain existing finite
structures and are not included in the 148,480-byte figure.

The grow histogram phase-shares the 4K Word/Offset storage. It is cleared
before descriptor admission begins. The persistent plan has 64 records; more
records cause the deterministic iteration-range fallback. No host-side array
contains 16K descriptors.

## Information lower bound

An exact one-pass global ordering of 16,384 arbitrary descriptors cannot be
retained with only 4,096 total descriptor slots. The missing information must
be reread from B, stored in timing-visible external backing, or represented by
a separately bounded summary that is sufficient to regenerate the assignment.
This slice uses the third choice for assignment and still rereads B for every
pass. It does not claim that nine grow counts encode the descriptors.

## Mechanism comparison

1. Four-pass B reread/filter moves 262,144 B word bytes. It is simple and uses
   existing MAA cache/memory ports, but static ranges can overflow under skew.
   Ordinary sequential 4K chunks on the authenticated physical model require
   16,384 A-line requests, eight epochs, four row drains, and 644 row groups.
2. One descriptor spill plus replay retains all descriptor information in the
   LLC. The existing finite model charges 131,328 padded bytes each for append,
   merge-read, and eventual writeback: 459,584 coherent line bytes and 7,181
   line transfers, versus 262,400 bytes/4,100 transfers for four B scans. That
   model needs eight finite subruns and has not been timed in gem5.
3. A translated-grow histogram is small on this trace and directly describes
   the DRAM grouping key. The live slice charges a 65,536-byte summary scan,
   hash probes, finite planner visits, and four 65,536-byte replays (262,144
   replay bytes). The combined B word traffic is 327,680 bytes. Exact cache-line
   requests are counted by gem5 rather than inferred from word bytes.

## Frozen trace evidence

The authenticated physical input is
`/data1/nier/dx100-runs/2026-08-03-virtualization-integration/bounded-range-4bf5ef5/physical_admission_records.jsonl`:

- 16,384 records; SHA-256
  `2803564faba235362e4ffe1b33cec0fecbe52860bd86261369082fcb977f7605`.
- Nine translated `grow_addr` values 13 through 21 with populations
  `1785/2058/2026/2028/2026/2027/2028/2026/380`; peak 2,058.
- Four unsplit contiguous range quartiles are
  `5869/4054/4055/2406`, so pass zero violates 4K.
- Greedy whole-grow contiguous packing is five passes:
  `3843/4054/4053/4054/380`. The authenticated finite model reports 9,523
  A-line requests, five epochs, zero Offset/Row drains, 129 row groups, peak
  4,054 offsets, and peak 310 row slots.
- Pairing the eight main grows and splitting only grow 21 fills four exact
  4,096 passes with quotas `10/41/44/285`. The model reports 9,582 A-line
  requests, four epochs, zero drains, 136 groups, and peak 317 row slots. This
  is the implemented preference: 59 extra A-line requests buy one fewer full B
  replay.

The separate frozen XRAGE source-address diagnostic is not a DRAM-row result.
Static full-array quarters produce `16384/0/0/0`. Adaptive FP64 source-line
quantiles at `36930/37514/38101` produce four 4,096 buckets; eight buckets
produce 2,048 each. The full input has 2,169 unique source lines (7.554
words/line). Four ordinary sequential chunks total 2,310 unique-line requests,
whereas source-line quantiles retain 2,169 with per-bucket unique counts
`522/526/524/597`. These numbers diagnose source-range skew and set a
coalescing target only; they do not establish translated DRAM locality.

## Exactness and timing contract

- Every summary/replay B line is requested through the existing MAA port;
  summary and replay line/word counters are separate.
- A phase token rejects stale feeder words. Every replay must inspect logical
  iterations `0..16383` in order.
- Admission and retirement bitmaps fail on duplicate, missing, stale, or
  retirement-before-admission behavior.
- More than 4,096 admissions without a fully retired explicit drain is fatal.
- A summary overflow, more than 64 retained grows, or an un-packable quota
  plan selects deterministic 4K iteration ranges. Multiple oversized grows
  are supported by bounded record/pass quotas. Invalid or mismatched
  populations and stale/incomplete replay ordinals fail closed.
- The matched runner requires exact output hashes, terminal ROI completion,
  `simTicks`, <=4K Word/Offset/Row-line counters, explicit Row-directory count,
  summary/replay traffic, pass/drain/max-epoch counters, and zero uncached B
  responses.

## Matched gem5 evidence

The clean matrix at
`/data1/nier/dx100-runs/2026-08-08-true-4k-reorder/9ddf1ad3`
uses source commit `9ddf1ad3f2aede325b03d532c8f8f26d4a0dd5e3`, gem5 SHA-256
`64980714a719621fe061aa7d7d3a7f14a4b70950cff1acd63f1a94b175064f1e`,
and three selector-isolated immutable checkpoints. The arms ran concurrently.
All produced exact output hash `7228541527853630339` and terminal ROI
completion.

| Arm | simTicks | A-line requests | row insertions | row drains | DRAM activates |
|---|---:|---:|---:|---:|---:|
| native16 | 41,346,674 | 10,576 | 1,472 | 846 | 4,510 |
| native4 | 59,297,850 | 16,384 | 2,103 | 3,589 | 5,115 |
| physical-grow true4K | 66,685,589 | 9,603 | 1,260 | 0 | 3,848 |

The candidate is 61.284% more simTicks than native16 and 12.459% more than
native4, so this slice provides no speedup. Against native16/native4 it reduces
A-line requests by 9.200%/41.388%, row insertions by 14.402%/40.086%, DRAM
activates by 14.678%/24.770%, and DRAM reads by 6.500%/23.770%. These are
mechanism counters, not a performance claim.

The candidate charged one 65,536-byte summary and four 65,536-byte B replays:
327,680 bytes total, 5,125 line reads, 81,920 filtered words, 6,413 filter
cycles, and 1,301 wait cycles across 14 events. It reported accepted physical
planning, no fallback, four exact passes of 4,096 inspected/admitted/retired
descriptors, zero replay/Row/Offset drains, and exactly 16,384 authenticated
physical admissions. Word/Offset/Row-directory/Row-line high-water bounds were
4,096/4,096/512/4,096. The authenticated admission and grow-histogram hashes
are respectively
`d333ada7974d16bb397774127f9e54d6bb7700aff5d62eca4e380a52079ffa51`
and `821c07b4a41cac39fdebf791c841a63b3bd40531e2abe7915362bab5f28788da`.

The live translated-grow populations for grows 13 through 21 are
`1580/2072/2028/2026/2027/2028/2026/2027/570`. Their difference from the
earlier frozen physical trace demonstrates why source-line or prior-placement
counts cannot substitute for runtime translated-grow evidence.

## Current validation and limitations

ASan+UBSan unit tests pass for the tracker, metadata ledger, source-line
diagnostic, five-pass whole-grow comparison, multi-oversized-grow quotas, and
authenticated four-pass grow splitting, including a forced admission retry
that proves ordinal mutation is transactional. All 19 source contracts pass.
The production build and final no-op recheck pass. Failed roots remain
preserved: `6faef8cb` exposed an invalid native4 high-water gate, `60d33659`
exposed a null reset, and `bc27f3b5` silently used iteration fallback with no
physical admissions. They are not positive evidence. The final disposition is
a viable true-bounded vertical slice for mechanism study, not promotion: no
full workload, speedup, area, energy, or general DRAM-locality claim is made.
