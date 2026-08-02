# Corrected Hybrid Single-Owner Scheduler (CHSO-384)

## Decision

Replace rejected PFCC-64 proposal `54f712a` with a finite policy in which the
existing 384 destination-combiner lines become the only owners of destination
lines.  A line remains in the same owner entry across page-focus changes,
source-response processing, write-request acceptance, and the matching memory
write response.  No second “normal” combiner may own the line concurrently.

The accompanying model executes this policy as a deterministic transition
system.  It does not substitute direct4 traffic for an unexecuted policy.
Archived replay reports work and ordering counts only.  It is not gem5 timing,
an application performance claim, RTL, synthesis, area, power, or frequency
evidence.

CHSO-384 is a corrected executable candidate, not a promoted architecture.  On
the archived FLAG cases it performs more source requests and has a lower
same-bank-row successor proxy than both references.  That negative result must
be resolved in a response-timed implementation experiment before promotion.

## Evidence read and corrections made

This design uses four inputs:

1. The current transparent controller through `a9d3821`, including
   `TransparentSPDController.hh`, its C++/Python contract tests, the controller
   integration in `MAA.cc`, the no-response stream-store path in
   `StreamAccess.cc`/`Port.cc`, and the response-bearing indirect retirement
   path in `IndirectAccess.cc`/`Port.cc`.
2. The professor's retain/spill analysis through `57eea77`.  In particular, a
   4K selected-subset reconstruction needs at least four B scans, and a
   balanced selector needs another scan or external spill state.  CHSO does not
   claim such scans or reconstruct a free 16K reorder window.
3. Rejected proposal `54f712a`, which split focus words into the normal
   combiner and future words into PFCC, used an inexact `0xff` expected mask,
   retired tokens on write acceptance, gated after a full scan without charging
   the page-0 barrier, and reported bounded4 as a PFCC target without executing
   PFCC.
4. The independent review session
   `pfcc-scheduler-independent-review-20260802-150907-d3d475ea`.  Its P1
   findings define the single-owner, exact-predicate, true-ACK, generation, and
   charged-barrier requirements below.

## Exact policy

### Fixed dimensions

One logical descriptor contains 16,384 words in four 4,096-word destination
pages.  With FP64 data, each destination cache line has eight word positions.
The policy state is finite:

- 384 destination-line owners, 96 sets, four ways;
- four accepted source-request slots and four reserved source-response slots;
- eight write-request slots and eight accepted-but-not-complete write slots;
- a 128-request active bank-row quantum;
- at most 384 new focus-page lines and one new future-page line reserved by one
  source request;
- one 64-bit generation on every logical descriptor, owner, source request,
  source response, write request, and write response match.

The apparently large “384 focus lines per response” is a capacity bound, not a
one-cycle datapath claim.  One response can name at most 384 owners × eight
word tokens.  A hardware implementation would walk that finite token list at a
declared retirement throughput.  The replay applies the bounded logical
transition atomically and therefore provides no throughput or clock result.

### Charged build barrier and exact masks

CHSO performs one ordinary 16K descriptor/live-mask build before its first A
request.  For each destination line `L`, it stores

```text
live_mask[L][w] = predicate[i] && i maps to (L,w)
```

False predicates are accounted once as architectural no-writes.  They do not
reserve an owner word, appear in a write mask, or block completion.  The model
reports all 16,384 scanned words per full tile as both `index_scan_words` and
`preissue_barrier_words`.  There is no selection prepass and no hidden B
rescan.

This corrects the rejected page-0 claim: CHSO does **not** claim page-0 issue as
early as direct4.  Direct4's ordering reference may begin after one 4K page;
CHSO and full-row wait for the charged logical-tile build.  The archive cannot
turn those word counts into cycles.

### One owner per destination line

Each live word is exactly one of:

```text
unconsumed -> reserved(source request) -> tentative(owner) -> committed(ACK)
predicated_false
```

Before a source request is queued, every selected Offset token reserves a word
in the unique owner for its destination line.  Allocation probes the line's
four-way set.  A hit always uses the existing owner, including after a focus
change.  A miss allocates only with a free way and a reserved response credit.
There is no eviction and no partial spill.  If allocation is refused, the
Offset token remains unconsumed and may be selected later.

The owner stores the exact live mask, received mask, reserved mask, payload,
Offset tokens, generation, state, and write-request identity.  A line cannot
enter the old normal combiner while it has a CHSO owner; CHSO replaces that
combiner's ownership role rather than sitting beside it.

### Focus selection and line promotion

The oldest page with an uncommitted live word is the focus page.  Its eligible
source descriptors are ordered by the archived RoBaRaCoCh bank-row key.  The
selector continues an active row for at most 128 accepted source requests,
then chooses the next eligible row.  Owner-pressure promotion may interrupt a
row quantum.

For a selected source line, CHSO reserves all unconsumed contributions to
existing owners, any allocatable focus-page lines up to the table capacity,
and at most one allocatable future-page line.  Future ownership is the bounded
cross-page reuse mechanism.

Normal focus selection runs while it can reserve at least one token.  When it
cannot, the selector finds the oldest incomplete owner, identifies the source
descriptor for its lowest missing live word, and promotes that descriptor.
The promoted response fills all matching existing owners; unrelated tokens
remain unconsumed unless they fit the stated allocation rules.  Thus a full set
causes backpressure/refetch work, not a second owner or a partial write.

Focus advances only after true write responses have committed every live word
in the page.  Future-page owners survive that change unchanged.

### Finite request and completion transitions

The transition model separates the following events:

1. **Source issue:** reserve owner words and a downstream response credit, then
   enqueue a generation-tagged source request.
2. **Source acceptance:** move that request to the bounded response queue.
3. **Source completion:** merge the returned values into their pre-reserved
   owners.  A response with the wrong generation is counted and ignored.
4. **Write request:** once `received_mask == exact_live_mask` and no reserved
   word remains, enqueue one exact-mask write.
5. **Write acceptance:** move the request into the eight-entry ACK/outstanding
   queue.  The owner and every tentative token remain live.
6. **True write completion:** only a matching `(generation, line, request_id)`
   response commits tokens, decrements page work, and frees the owner.  A stale
   or duplicate response is counted and cannot release current state.

Every queue admission is credit checked.  Source request plus response
occupancy may not exceed the four reserved response credits.  Set occupancy,
table occupancy, and all queue capacities are asserted during replay.

### Liveness argument

Assume fair source-request acceptance, eventual source response, fair
write-request acceptance, and eventual matching write response.

- Every accepted current-generation source response moves at least one live
  token from `reserved` to `tentative`.
- Every matching write response moves at least one token from `tentative` to
  `committed` and frees an owner way.
- If focus work cannot allocate, either an accepted/write pipeline transition
  is pending or promotion selects a source for a missing word in the oldest
  incomplete owner.
- An owner is never evicted, so promotion cannot lose previously received
  words.  Its finite exact mask therefore eventually fills and drains.
- There are at most 16,384 live tokens, 384 owners, and the stated finite
  queues.  The model enforces a finite transition bound and fails on repeated
  no-progress states.

No claim is made for a memory system that can permanently refuse or never
acknowledge a request; that violates the fairness premise.

## What current gem5 can and cannot model

Current gem5 already can model these pieces separately:

- the transparent controller's fixed four-page lifecycle, descriptor hazards,
  one clock-timed lookup delay, already-ready-page import, and descriptor
  generation check;
- bounded counters/configuration for indirect response and write limits;
- response-bearing indirect retirement stores using `WriteReq`/`WriteResp`,
  with retirement completion called from `Port.cc::recvTimingResp`;
- exact masked retirement writes using the indirect path's byte enable.

Current gem5 does **not** model CHSO as wired code.  In particular:

- transparent `STREAM_ST` creates `WritebackDirty`, which needs no response;
  `Port.cc` calls `writePacketSent` on send acceptance, and the stream unit
  counts that as a received response.  The transparent controller therefore
  completes and releases its descriptor after acceptance, not memory ACK;
- producer page-ready callbacks still pass `(token_tile, page)` rather than an
  explicit generation.  `MAA.cc` compares the current arrays after callback,
  but a stale producer event cannot be independently identified by its own tag;
- CHSO owner selection, reverse missing-word lookup, finite source/response
  descriptors, exact per-line live-mask generation, and accepted-write ACK
  queue are not implemented in simulator source;
- several current virtual structures are STL maps/sets/vectors.  A configured
  count is not a synthesized bounded descriptor implementation;
- the archived XRAGE/FLAG index JSON has no source response tick/order,
  write-request refusal, write-ACK tick, or predicate stream.  All archived
  words are treated as live.  FLAG also lacks the A-base phase, so phase zero
  is a declared bank-row proxy.

A future gem5 implementation can model true completion by routing CHSO writes
through the response-bearing indirect `WriteReq` path (or an equivalent new
port contract), carrying generations end to end, and materializing every queue
and owner capacity.  Until that exists, neither this replay nor the current
transparent stream-store run is true CHSO timing evidence.

## Finite state contract

The executable model reports the following bit-packed state contract for its
default dimensions:

| Component | Bytes |
|---|---:|
| 384 owner payloads | 24,576 |
| Owner tags/masks/generations/tokens/state | 10,272 |
| Exact live-mask + generation table | 18,688 |
| Reverse destination-to-source token table | 28,672 |
| Two-bit live-token state | 4,096 |
| Four source-request descriptors | 21,543 |
| Four source-response descriptors and payloads | 21,799 |
| Eight write-request descriptors | 97 |
| Eight accepted-write/ACK descriptors | 97 |
| **Bit-packed policy-state contract** | **129,840** |

The source queue descriptors conservatively reserve the maximum 384 × eight
Offset tokens.  Existing full Row/Offset/row-selection storage is not counted
again.  Conversely, this table is not a whole-MAA storage ledger and excludes
selector logic, ports, allocators, wiring, ECC, banking overhead, and existing
STL capacity.  The 384 payload lines replace/repurpose the existing combiner
role; the total must not be described as incremental area.  These byte counts
are neither synthesized area nor energy evidence.

## Archived deterministic replay

The frozen artifact is
`experiments/analysis/corrected_hybrid_scheduler_replay_2026-08-02.json`
(SHA-256 `11d34294d329f1054271579da377e1a84c9949ab3836d7eb6927908528a7fdf3`).
It uses XRAGE input SHA-256
`1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde`
and all 14 archived FLAG gather JSON files.  Each file hash is stored in the
artifact.

All three policies use static index mappings and model-selected immediate
source-response order.  Full-row and direct4 are finite combiner ordering
references with immediate write acceptance/completion.  Corrected is the
executed owner/request/response/ACK transition model.  Therefore differences
are policy work/ordering observations, not simulated latency.

### Full XRAGE (128 logical tiles, 2,097,152 live words)

| Policy | A requests | Duplicate A reads | Same-row successor proxy | C write requests | Partial-live-mask writes | Preissue scan words |
|---|---:|---:|---:|---:|---:|---:|
| full-row | 299,046 | 0 | 98.685% | 271,221 | 17,925 | 2,097,152 |
| direct4 | 322,188 | 23,142 | 97.981% | 262,762 | 1,235 | 524,288 |
| corrected CHSO-384 | 318,099 | 19,053 | 79.074% | 262,144 | 0 | 2,097,152 |

CHSO executes 115,142 owner-pressure promotions, reaches all 384 owners, and
observes 90,313 refused owner-allocation probes.  Its A-request count lies
between the two references and its writes reach the exact-live-line minimum in
this all-live issue-order model.  Its lower same-row successor proxy is an
explicit cost, not a timing conclusion.

### Fourteen FLAG gathers (40 logical tiles, 638,460 live words)

| Policy | A requests | Duplicate A reads | Same-row successor proxy | C write requests | Partial-live-mask writes | Preissue scan words |
|---|---:|---:|---:|---:|---:|---:|
| full-row | 153,567 | 0 | 87.768% | 80,650 | 1,614 | 638,460 |
| direct4 | 155,262 | 1,695 | 87.754% | 79,958 | 288 | 163,840 |
| corrected CHSO-384 | 158,207 | 4,640 | 73.339% | 79,814 | 0 | 638,460 |

CHSO executes 54,924 promotions, reaches 384 owners, and observes 57,527
allocation refusals.  It removes partial-live-mask writes in this ordering
model but performs 2,945 more A requests than direct4 and has a lower row proxy.
This is evidence against promotion without response-timed validation.

## Focused validation

`experiments/tests/test_corrected_hybrid_scheduler_model.py` covers:

- a future destination-line owner surviving a focus change and promoting the
  missing source word;
- exact masks with predicated holes;
- a full one-entry owner/write-ACK configuration retaining ownership after
  request acceptance and draining only after true completion;
- stale source and write responses failing to mutate the current generation;
- an adversarial repeated-source pattern completing within a finite bound;
- explicit full-row/direct4/corrected execution and charged scan barriers;
- nonzero finite state for exact masks, generations, and every queue.

## Reproduction

```bash
python3 -m unittest \
  experiments.tests.test_corrected_hybrid_scheduler_model -v
python3 experiments/analysis/corrected_hybrid_scheduler_model.py \
  --xrage /data1/nier/DX100/experiments/inputs/xrage_gather0_full.json \
  --flag-root /data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag \
  --output /tmp/corrected_hybrid_scheduler_replay.json
sha256sum /tmp/corrected_hybrid_scheduler_replay.json
```

No gem5 run or simulator-source integration is part of this handoff.
