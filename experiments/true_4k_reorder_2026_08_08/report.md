# True-bounded 16K gather with a 4K active reorder set

## Finding

The narrow viable slice is a timing-visible translated-DRAM-grow summary,
followed by four full B replays whose admissions are capped at 4,096. A fixed
64-record grow plan pairs whole grows in descending-population order and may
split one grow by deterministic replay ordinal to fill pass gaps. If the
summary or plan cannot represent the distribution, the implementation falls
back to four contiguous 4K iteration ranges. It never retains a 16K Word or
Offset descriptor array.

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
| Retained grow plan | 64 records | 1,121 | grow/count/pass records and one split quota per pass |
| Exact-once checker | 16K bits twice | 6,758 | non-functional admission/retirement audit plus pass counters |
| Scratchpad payload | 4,096 elements/tile | 524,288 for 32 visible tiles | data payload, not reorder metadata |

Word/Offset/Row reorder metadata is 148,480 semantic bytes. The grow plan and
checker are reported separately. The candidate allocates exactly one active
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
- A summary overflow, more than 64 retained grows, multiple oversized grows,
  or an un-packable plan selects deterministic 4K iteration ranges. Invalid or
  mismatched populations fail closed.
- The matched runner requires exact output hashes, terminal ROI completion,
  `simTicks`, <=4K Word/Offset/Row-line counters, explicit Row-directory count,
  summary/replay traffic, pass/drain/max-epoch counters, and zero uncached B
  responses.

## Current validation and limitations

ASan+UBSan unit tests pass for the tracker, metadata ledger, source-line
diagnostic, five-pass whole-grow comparison, and authenticated four-pass grow
split. The 13-test source contract suite passes. A production
`build/X86/gem5.opt` build completed through generated local parameters,
compilation of `Tables.cc`, `IndirectAccess.cc`, and `MAA.cc`, and final link;
an immediate second SCons invocation reported the target up to date. The
binary SHA-256 before source checkpointing is
`f52dc9aa6b64c24c40213100c1c1b602bfcc7457b3997482cfb1dc16fded8b2e`.
The matched native16/native4/candidate matrix remains pending until this source
is checkpointed to give the evidence runner a clean exact commit. No full
workload, speedup, area, energy, or DRAM-row-locality claim is made.
