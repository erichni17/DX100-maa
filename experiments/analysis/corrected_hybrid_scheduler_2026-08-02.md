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

CHSO-384 is a corrected executable candidate, not a promoted architecture.
Against direct4, XRAGE CHSO has fewer A requests but a much worse same-bank-row
successor proxy; FLAG CHSO has more A requests and a worse row proxy.  It
therefore fails the strict improve-both gate.  That honest negative result must
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
- four issued source-request slots, four accepted-response ledger entries, and
  four bounded incoming response-event slots;
- eight write-request slots and eight accepted-but-not-complete write slots;
- a 128-request active bank-row quantum;
- at most 384 new focus-page lines and one new future-page line reserved by one
  source request;
- one nonzero 64-bit generation and one non-wrapping 64-bit unique request ID
  on every request/response obligation, plus an 18-bit source-line field.  The
  archived FLAG maximum is line 222,112, so the prior 14-bit field was invalid.
- an external response-admission boundary that accepts only the exact
  `SourceResponse` record, nonzero uint64 request ID and generation, uint18
  source line, and exactly eight uint64 payload words.  Malformed events never
  enter the response FIFO; charged observer work and a saturating 64-bit
  diagnostic record their rejection.

The apparently large “384 focus lines per request” is a capacity bound, not a
one-cycle datapath claim.  One accepted-request ledger entry can name at most
384 owners × eight word tokens.  A source response carries only one eight-word
cache-line payload; it cannot nominate destination tokens.  A hardware
implementation would walk the accepted request's finite token list at a
declared retirement throughput.  The replay charges and bounds that walk but
provides no throughput or clock result.

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
in the unique owner for its destination line and records the exact request ID
and source line that may complete it.  Allocation probes the line's four-way
set.  A hit always uses the existing owner, including after a focus change.  A
miss allocates only with a free way and a reserved accepted-response credit.
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

The selector materializes bounded per-row source heaps, a membership CAM, and a
finite row directory.  At quantum expiry it chooses the first eligible row
strictly after the active row, wraps to the first different eligible row, and
reselects the old row only when no different row remains.  Rebuild source scans,
focus-membership scans, row-directory scans, heap pushes/pops, sort inputs and a
comparison upper bound, allocation/planning scans, promotion walks,
response-admission field/word/diagnostic checks, response payload/token checks,
ready-owner scans, and write walks all have explicit `work_` counters.  Every
logical transition also records a work high-water mark and fails if it exceeds
the configuration-derived finite bound.  Diagnostic invariant checks are
validation work outside the modeled policy transition and are not presented as
scheduler work.

Focus advances only after true write responses have committed every live word
in the page.  Future-page owners survive that change unchanged.

### Finite request and completion transitions

The transition model separates the following events:

1. **Source issue:** reserve owner words under a unique
   `(generation, request_id, source_line)` identity and a downstream accepted-
   response credit, then enqueue the source request.
2. **Source acceptance:** move the exact request into the bounded authoritative
   accepted-response ledger.  Issued requests plus accepted ledger entries may
   never exceed the four shared credits.
3. **External response admission:** before any hardware-policy state mutation,
   require the exact record and tuple types, nonzero bounded request ID and
   generation, bounded source line, exact eight-word length, and uint64 range
   for each payload word.  Length rejection is constant work and at most eight
   words are inspected.  Malformed responses fail closed before queue
   insertion, including when the queue is already full.  Only replay/evidence
   observer state can change: the normal bounded-transition accounting, three
   admission-work counters, and the saturating malformed-event counter.
4. **Source completion:** match all three identity fields, validate the exact
   deterministic eight-word source payload, and preflight the complete accepted
   token list before mutating any owner.  Unknown, stale, forged, reordered, or
   duplicate well-formed events cannot consume unrelated reservations.  Valid
   responses may arrive in any order through the bounded event FIFO.
5. **Write request:** once `received_mask == exact_live_mask` and no reserved
   word remains, enqueue one exact-mask write.
6. **Write acceptance:** move the request into the eight-entry ACK/outstanding
   queue.  The owner and every tentative token remain live.
7. **True write completion:** only a matching `(generation, line, request_id)`
   response commits tokens, decrements page work, and frees the owner.  A stale
   or duplicate response is counted and cannot release current state.

Every queue admission is credit checked.  Well-formed unmatched response events
occupy only the bounded event FIFO and cannot create an accepted-response
credit; malformed events cannot occupy even that FIFO.  Issued source requests
plus authoritative accepted responses remain bounded by four.  Set occupancy,
table occupancy, every queue, request-ID exhaustion, and every field width are
asserted during replay.

### Liveness argument

Assume fair source-request acceptance, eventual source response, fair
write-request acceptance, and eventual matching write response.

- Every exactly matched accepted source response moves at least one live token
  from `reserved` to `tentative`, and the payload oracle proves that the mapped
  source value is received exactly once.
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

The replay now emits a field-level inventory rather than inferring a total from
a hand-selected component list.  Each of the 129 persistent fields is assigned
exactly once: 69 fields are **hardware policy state** and 60 are
**replay/evidence-only observer state**.  The four added observer fields are
three admission-work counters and the saturating malformed-response counter.
The complete named inventory and its component mapping are in the artifact's
`state_contract.persistent_field_inventory`.  The unit test parses every
`self.field` assignment, checks all embedded record schemas, checks the
work-counter and high-water key sets, rejects duplicates, and rejects
component/classification disagreement.

The width contract remains 11-bit destination-line tags, 14-bit destination
tokens, **18-bit source-line tags**, 3-bit source-word offsets, 64-bit
generations and non-wrapping request IDs, 12-bit allocation sequences, 11-bit
bank-row keys, two-bit owner states, exactly eight 64-bit response words, and
64-bit values/counters.  The malformed-response counter saturates at the uint64
maximum.  The owner also retains each received 64-bit word in an explicit
persistent payload field; completion checks that owner payload before releasing
the owner.  This repair does not change any valid replay's mechanism, ordering,
provenance, work-total, or exact-once result.

| Classification | Component | Bits |
|---|---|---:|
| Hardware policy | Configuration image | 113 |
| Hardware policy | Source mapping | 344,064 |
| Hardware policy | Live predicate + exact live-mask + destination-line directories | 53,248 |
| Hardware policy | Source-target and pending-line directories | 786,432 |
| Hardware policy | Token/progress state | 32,835 |
| Hardware policy | Focus row structure + membership | 1,294,336 |
| Hardware policy | Selector state | 311 |
| Hardware policy | Owner payload + metadata | 559,872 |
| Hardware policy | Source request + accepted-source ledger | 345,346 |
| Hardware policy | Source-response event queue | 2,643 |
| Hardware policy | Write request + ACK queues | 4,244 |
| Hardware policy | Global generation/request/allocation identity | 204 |
| **Hardware policy subtotal** | **all 69 policy fields** | **3,423,648** |
| Replay/evidence observer | Expected/observed values and receive-count oracle | 2,129,920 |
| Replay/evidence observer | Functional-work counters and atomic-bound diagnostics | 1,862 |
| Replay/evidence observer | Transition/promotion/refusal/error counters | 1,216 |
| Replay/evidence observer | Previous-row ordering observer | 12 |
| Replay/evidence observer | Six queue/owner high-water observers | 26 |
| **Replay/evidence observer subtotal** | **all 60 observer fields** | **2,133,036** |
| **Combined finite replay-model total** | **all 129 persistent fields** | **5,556,684** |

Those subtotals are 427,956 B, 266,630 B, and 694,586 B respectively after a
whole-total byte ceiling.  The JSON contains the exact per-component arithmetic;
component byte ceilings are intentionally not summed.  The source descriptors
still reserve at most 384 × eight Offset tokens.  The combined total includes
the source-target and pending-line directories actually retained by this Python
model, owner payload, every promotion/refusal/stale/forged/error counter, every
functional-work diagnostic, and every high-water observer.  It excludes only
ephemeral interpreter overhead such as Python object headers, hash-table slack,
temporary locals, and transient sorting/materialization containers.

This is a finite model-state ledger, not a whole-MAA storage or physical-cost
result.  It excludes ports, allocators, wiring, ECC, banking overhead, and
implementation margins.  The 384 payload lines replace/repurpose the existing
combiner role; neither subtotal nor the combined total may be described as
incremental area, synthesis, energy, or timing evidence.

## Archived deterministic replay

The frozen artifact is
`experiments/analysis/corrected_hybrid_scheduler_replay_2026-08-02.json`
(SHA-256 `b82945feea355782b5f319de2430683b223c646bd7de9cc5e76a45e54f57086f`).
It uses XRAGE input SHA-256
`1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde`
and all 14 archived FLAG gather JSON files.  Each file hash is stored in the
artifact; the aggregate FLAG maximum source line is 222,112 and is checked
against the 18-bit contract.

All three policies use static index mappings and model-selected immediate
source-response order.  Full-row and direct4 are finite combiner ordering
references with immediate write acceptance/completion.  Corrected is the
executed owner/request/response/ACK transition model.  Every integer field
prefixed `work_`, plus request/write/scan/transition counts and row successors,
is a **functional work or ordering count**.  The artifact has no timing domain:
it reports no cycles, ticks, latency, throughput, or speedup.  Therefore all
differences below are policy work/ordering observations, not simulated latency.

### Full XRAGE (128 logical tiles, 2,097,152 live words)

| Policy | A requests | Duplicate A reads | Same-row successor proxy | C write requests | Partial-live-mask writes | Preissue scan words |
|---|---:|---:|---:|---:|---:|---:|
| full-row | 299,046 | 0 | 98.685% | 271,221 | 17,925 | 2,097,152 |
| direct4 | 322,188 | 23,142 | 97.981% | 262,762 | 1,235 | 524,288 |
| corrected CHSO-384 | 318,160 | 19,114 | 79.058% | 262,144 | 0 | 2,097,152 |

CHSO executes 115,188 owner-pressure promotions, reaches all 384 owners,
observes 90,583 refused owner-allocation probes, and performs 5,260 explicit
row rotations.  The exact-once oracle validates all 2,097,152 live destination
values with zero failures.  Its 91,394,097 charged functional-work items have a
10,505-item maximum atomic transition, below the derived 33,620,161-item bound.
Its A-request count lies between the two references and its writes reach the
exact-live-line minimum in this all-live issue-order model.  Its much lower
same-row successor proxy is an explicit negative cost, not a timing conclusion.
Against direct4 it saves 4,028 A requests but sharply worsens the row proxy, so
XRAGE fails the strict improve-both gate.

### Fourteen FLAG gathers (40 logical tiles, 638,460 live words)

| Policy | A requests | Duplicate A reads | Same-row successor proxy | C write requests | Partial-live-mask writes | Preissue scan words |
|---|---:|---:|---:|---:|---:|---:|
| full-row | 153,567 | 0 | 87.768% | 80,650 | 1,614 | 638,460 |
| direct4 | 155,262 | 1,695 | 87.754% | 79,958 | 288 | 163,840 |
| corrected CHSO-384 | 158,209 | 4,642 | 73.340% | 79,814 | 0 | 638,460 |

CHSO executes 54,900 promotions, reaches 384 owners, observes 57,677 allocation
refusals, and performs 12,044 explicit row rotations.  The exact-once oracle
validates all 638,460 live destination values with zero failures.  Its
59,143,509 charged functional-work items have a 17,721-item maximum atomic
transition, below the same derived bound.  It removes partial-live-mask writes
in this ordering model but performs **2,947 more A requests than direct4** and
has a substantially lower row proxy.  The repaired policy therefore remains
negative: FLAG worsens both strict-gate dimensions and is not a promotion
candidate; response-timed evidence is still absent.

## Focused validation

`experiments/tests/test_corrected_hybrid_scheduler_model.py` covers:

- a future destination-line owner surviving a focus change and promoting the
  missing source word;
- exact masks with predicated holes;
- a full one-entry owner/write-ACK configuration retaining ownership after
  request acceptance and draining only after true completion;
- forged same-generation IDs/source lines, reordered legitimate responses, and
  duplicate responses failing to mutate unrelated reservations;
- adversarial response-event injection never increasing the combined issued +
  accepted-response credit total;
- corrupted/reordered payload words failing before partial owner mutation, then
  the legitimate payload satisfying the exact-once destination oracle;
- zero-, seven-, nine-, and 100,000-word payload tuples failing before FIFO or
  owner mutation, with length-independent charged work;
- negative and `2^64` payload words; zero, negative, boolean, over-width, and
  million-bit request/generation/source-line values failing closed;
- a full response queue and 256 repeated malformed events preserving the exact
  queued record and owner state, plus explicit uint64 diagnostic saturation;
- stale write responses failing to release the current owner;
- row-burst expiry rotating to a different eligible row before the old minimum;
- an adversarial repeated-source pattern completing within a finite bound;
- explicit full-row/direct4/corrected execution and charged scan barriers;
- nonzero charged heap/rebuild/sort/token/transition work under the atomic bound;
- 18-bit archived source-line edges, 64-bit generation/request-ID exhaustion,
  exact response-slot arithmetic, field-inventory coverage, and nonzero finite
  state for protocol, selector, identity, ordering, oracle, exact masks, and
  every queue.

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
