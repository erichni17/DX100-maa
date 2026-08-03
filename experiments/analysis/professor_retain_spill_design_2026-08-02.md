# Professor retain/spill design for a bounded DX100 gather reorder

Date: 2026-08-02

Source revision inspected: `9fcb18c4cabb782975c68b6a8f484364f8987637`

Scope: one unpredicated FP64 gather `C[i] = A[B[i]]`, `N = 16,384`, at
most `K = 4,096` active on-chip producer descriptors, 4-byte B elements,
8-byte A/C elements, and 64-byte cache lines.  This is a source-derived
correctness and storage contract.  No gem5 executable or simulation was run.

## Decision

The spoken retain-half/spill-half idea can be made **architecturally correct**,
but a future-blind greedy “keep top half” rule cannot reproduce native 16K A
request issue order for every B stream while holding only 4K descriptors and
having neither a durable external descriptor image nor permission to rescan B.
It fails first on information conservation, and then on native cross-slice
round-robin order.

The weakest sufficient contract is not “choose the right half.”  It is:

> Until every logical `i` has been issued and retired, preserve a replayable,
> immutable source of every live descriptor and enough deterministic scheduling
> state to decide the next native request group.  The source may be stable B plus
> repeated scans, or coherent descriptor records plus a bounded merge/spool.
> Every transfer and response is identified by generation and transaction
> serial, and no owner is reused before its matching response.

For exact recovery, four immutable 4K runs in coherent backing plus a bounded
merge are an honest **downstream replay substrate**, not yet a complete design.
That substrate does not eliminate 16K metadata: it moves a complete descriptor
image off chip.  Moreover, native
DX100 order includes first-insertion state, early drain boundaries, and
potential response/refill events; a plain sort by `(bank,row,line)` is not, by
itself, an exact native-order reconstruction.  An exact run must carry or derive
a canonical native issue serial under a frozen event contract.  Implementing
that serial generator is substantially equivalent to virtualizing the native
RowTable/OffsetTable scheduler.

If exact native-order recovery is not a hard requirement, the smallest honest
experiment is the already-understood four bounded B scans with a deterministic
bucket filter, a 4K Row/Offset epoch, retained C combiner state, and complete
structural counters.  Call it a four-bucket bounded schedule, not a recovered
16K reorder window.

## 1. Native source behavior at this revision

### 1.1 B becomes RowTable and OffsetTable metadata

For each ready `i`, Fill obtains 32-bit `B[i]`, forms
`vaddr = A_base + 8 * B[i]`, aligns/translates the A line, maps it to DRAM
coordinates, computes its RowTable slice and `grow_addr`, and inserts
`(grow, aligned line, i, wid)` (`src/mem/MAA/IndirectAccess.cc:833-1002`).
`wid` is one of eight FP64 words in the returned 64-byte A line.

The relation is:

```text
one RowTable line descriptor
    {aligned physical A line, Offset head, Offset tail}
             |
             v
Offset node(i0,wid0,next) -> Offset node(i1,wid1,next) -> ... -> null
```

`OffsetTableEntry` is three C++ `int` fields: original logical `itr`, word ID
`wid`, and linked-list `next_itr` (`src/mem/MAA/Tables.hh:52-56`).  Allocation
also creates a validity array and an integer free list
(`src/mem/MAA/Tables.cc:123-166`).  Each selected logical iteration consumes
one Offset node even when many B values hit the same A line.

Insertion first searches row slots for an existing unsent `(grow,line)` and
appends its Offset chain.  Otherwise it chooses the first same-grow row with a
free line slot.  Otherwise it uses the first free row.  If no row is free, it
returns failure and forces a drain (`src/mem/MAA/Tables.cc:489-535`).  Within a
row, a new line occupies the first free line slot, so both row and line order
are **first-insertion order**, not numeric-address order
(`src/mem/MAA/Tables.cc:278-306`).

### 1.2 Slice permutation and round-robin local heads

`getRowTableIdx` modulo-folds channel, rank, bank-group, and bank into the
configured slice dimensions (`src/mem/MAA/IndirectAccess.cc:294-306`).  The
constructor then enumerates physical coordinates in the nested order

```text
for bank
  for bank-group
    for rank
      for channel
```

and retains the first occurrence of each folded slice ID
(`src/mem/MAA/IndirectAccess.cc:261-283`).  This is a bank-interleaved
permutation, not necessarily `[0,1,...]`.  The executable contract includes a
small organization whose exact permutation is `[0,2,1,3]`.

Within one slice, send chooses the first valid row's grow, walks line slots in
first-insertion order, then walks later row slots with that same grow before
selecting the next first-valid grow (`src/mem/MAA/Tables.cc:573-609`).  Build
does **not** drain an entire slice before considering another slice.  It asks
each slice in the precomputed permutation for at most one local head, reaches
the end, wraps to the first slice, and repeats
(`src/mem/MAA/IndirectAccess.cc:1452-1641`).  Abstractly:

```text
slice local queues after Fill:
  S0: a0, a1, a2
  S1: b0
  S2: c0, c1

global Build issue order:
  a0, b0, c0, a1, c1, a2
```

The word “sort” is therefore dangerous.  Native order is a deterministic walk
of insertion-created row/line queues, round-robin across a bank-interleaved
slice permutation.  Numeric `(row,line)` sorting can produce a different order.

### 1.3 Early drains are part of the trace

Fill stops and enters Build/Request when Offset occupancy reaches its configured
epoch, when RowTable insertion fails, at a partition barrier, or at logical
completion (`src/mem/MAA/IndirectAccess.cc:833-949,1290-1341`).  The insertion
that sees a full RowTable has not been admitted; it is retried after the prior
contents drain.  Consequently the 16K logical operation need not have a single
16K-resident RowTable epoch even with a 16K OffsetTable.

The ordinary request state can refill after progress frees native structures;
the bounded virtual native-order attribution path deliberately restricts
refill around incomplete source drains (`src/mem/MAA/IndirectAccess.cc:
1680-1765`).  Thus, at the strongest interpretation, “exact native issue
order” names an **event trace** under a specified response/arbitration history,
not merely a pure sort of B.  Any proposed exact comparator must freeze:

- physical address translation and DRAM mapping;
- RowTable configuration, slice permutation, rows, and line columns;
- Offset capacity/epoch and every forced drain boundary;
- request-credit and response/refill arbitration decisions; and
- tie behavior for first row, first line, and repeated A lines.

Without those inputs there is no unique native order to reproduce.

### 1.4 Responses map back to original i; A payload is not reorder metadata

One A line request owns the head of its Offset chain.  On response, the source
line is remapped to its RowTable slice/grow and the chain returns every
`(itr,wid)` destination.  The ordinary gather writes the selected returned word
to `SPD[dst][itr]` (`src/mem/MAA/IndirectAccess.cc:2039-2171`).  Duplicate B
indices therefore share an A response but still publish distinct C elements.
Response arrival order can differ from issue order without changing this
mapping.

The separation is fundamental:

- **Payload SPD** holds B input and, for ordinary `INDIR_LD`, the returned C
  values.  A two-slot 4K FP64 SPD cache holds `2 * 4096 * 8 = 65,536 B` of
  values.
- **Reorder metadata** holds A-line identity and the original `(i,wid)` owners.
  Without it, a returned A word cannot be placed at `C[i]`.

Caching or paging payload does not preserve the Row/Offset mapping.  The
current virtual retirement path makes the distinction visible: it uses bounded
source-response ownership, combines C lines, issues coherent `WriteReq`s, and
does not declare completion until responses, combiner state, and acknowledged
writes are empty (`src/mem/MAA/IndirectAccess.cc:2630-2679,3042-3065,
3237-3250`).

## 2. Precise model of the spoken proposal

Interpret “retain selected descriptors and spill the rest” as the following
finite operation.  Anything weaker loses information.

1. Allocate descriptor generation `G`; snapshot the A/B/C region identities,
   address mapping version, logical length N, word sizes, RowTable geometry,
   and native-order contract.
2. Admit B in sequential chunks of at most K words.  For each `i`, compute the
   physical A line, `wid`, slice, grow, and predicate outcome.  False predicates
   would be accounted once; this document's gather is unpredicated.
3. Build at most K active descriptors.  A descriptor has stable logical owner
   `i`; duplicates are not deleted even if their A line is coalesced.
4. Partition/sort only with a finite implementation.  Keep a selected resident
   subset.  Write every nonresident live descriptor to a scheduler-private
   coherent region with generation and write-transaction identity.  Do not
   overwrite the buffer or expose the run to merge until every matching write
   response arrives.
5. Admit later B chunks.  Before any A request is issued, either establish a
   canonical native issue serial for every record, or use a bounded procedure
   that can prove which group is next without discarded future knowledge.
6. Read/merge the resident and spilled records.  Issue each native A request
   group once.  Keep `(G, A request serial, line, destination record range)`
   until its matching A response.  Stream arbitrarily many duplicate-line
   destinations from backing under one retained 64-byte A response.
7. Insert each returned word into the unique C-line owner for original `i`.
   Publish dense C lines with response-bearing coherent writes.  Mark `i`
   complete exactly once; page readiness requires every page `i` plus every
   applicable C write response.
8. Complete only when all N positions are accounted, all run readers are
   exhausted at their exact recorded counts, all A and C transactions are
   matched, all owners are empty, and all four pages are published.

“Spill or defer to LLC” is not a single action.  If only B remains in memory,
later descriptor reconstruction is a **B rescan**.  If derived records remain,
it is an **external descriptor image**.  If neither remains, the deferred work
does not exist and exact completion is impossible.

## 3. Why greedy keep-top-half cannot be exact

“Top” is underspecified because native order is not one static numeric key.
Even granting a perfect comparator for all descriptors already seen, two
independent failures remain.

### Counterexample A: minimal information loss

Let K=1 and admit two distinct logical descriptors.  Keeping one and discarding
the other with no external record and no B rescan leaves no state from which to
issue and map the discarded result.  At most one of `C[0]` and `C[1]` can be
published.  This is the minimal conservation counterexample, N=K+1=2.

For the spoken K=4K/N=16K case, retaining K leaves 12,288 destinations with no
owner unless they are rescanned or materialized.  A policy name cannot encode
those missing `(line,i,wid)` relations.

### Counterexample B: minimal native-order failure

Let K=2, slice permutation `[S0,S1]`, and sequential admission be:

```text
chunk 0: S0:a0, S0:a1
chunk 1: S1:b0
```

All lines have one destination and fit one row.  Full native Fill followed by
round-robin Build issues `a0,b0,a1`.  A future-blind policy forced to drain the
full first chunk issues `a0,a1,b0`.  N=3 is minimal because with N<=K it can
wait without freeing a descriptor.  The focused test checks these exact traces.

The indistinguishability argument is general.  After observing chunk 0, the
controller sees the same state for two possible suffixes: one with no S1 head
and one with an S1 head.  Issuing `a1` is correct for the first suffix and wrong
for the second; waiting preserves order but cannot admit more B without an
external place for something.  No greedy choice resolves missing future
information.

### Duplicates and skew do not rescue it

Duplicates reduce A requests but not logical owners: 16K identical B values
need one A line response and 16K distinct destination records.  A K-entry
resident list cannot hold that fanout.  The exact bounded remedy is to retain
one A response and stream the destination run from backing; pretending the
fanout fits K violates the capacity contract.

Likewise, all descriptors may map to one bucket/grow.  Static four-way
`grow % 4` selection then places all 16K in one bucket.  It must make four or
more early drains or overflow.  It remains correct if drained, but it cannot
claim the monolithic native 16K order/locality window.

## 4. Weakest sufficient exact mechanisms

There are three honest families.

### Stable B plus repeated scans

Keep B immutable/coherent and rescan it.  A finite selection/range spool can
reconstruct only descriptors whose proven native serial lies in the current
range, issue them, then rescan for the next range.  This uses bounded on-chip
state and no descriptor backing, but deriving exact native serials may itself
require repeated simulations of insertion/drain state.  In the worst case it
approaches one or more full B scans per output group, not merely four scans.

Four static B-scan buckets are sufficient for **exactly-once results**, because
the bucket function assigns each i once and skew can be drained.  They are not
sufficient for exact native order across buckets.

### Immutable sorted runs in coherent backing

Create four bounded 4K runs, each with a finite in-place sorter and immutable
16-byte wire records.  Merge four heads.  This is one B scan and bounded active
state.  To recover a chosen canonical row-sorted order, sort by its complete
physical key.  To recover actual native order, records need a native issue
serial derived under the frozen event contract; merge by `(issue_serial,i)`.

This is the cleanest exact replay once serials exist, but serial generation
cannot be an implementation oracle.  It must come from either:

- an externalized Row/Offset state machine that assigns serials; or
- repeated stable-B scans that derive each next bounded serial range.

Either way, the 16K relationship has moved to backing or repeated work.

The executable test uses `oracle_materialize_native_records`: it first builds
the complete reference trace and only then labels records with issue serials.
That validates merge and lifecycle behavior, but it is **not** a <=4K hardware
algorithm and its derivation cost is intentionally absent from the replay
ledger.  No bounded serial-derivation implementation is supplied in this
artifact.  Therefore this analysis does not claim that the 51,789-B replay
subtotal is sufficient for exact recovery.

### External virtual RowTable/OffsetTable

Maintain native insertion, chain, cursor, drain, and claim state in coherent
backing, caching at most K descriptors/records on chip.  This is the most direct
way to promise exact native event semantics.  It is also plainly “16K metadata
in backing,” with additional protocol and traffic; it should be evaluated as
metadata virtualization, not metadata elimination.

The weakest sufficient contract shared by all three is a replayable immutable
descriptor source plus a bounded exact-next selector.  No particular data
structure is mandated.  Magic future knowledge is forbidden.

## 5. Explicit event timeline and finite state

### State machine

```text
ALLOC(G)
  -> ADMIT_B(chunk, cursor)
  -> SORT_ACTIVE
  -> RUN_WRITE_WAIT_ACK      (repeat for chunks/runs)
  -> FREEZE_RUNS
  -> MERGE_READ_WAIT_ACK
  -> A_SEND_RETRY / A_WAIT_RESPONSE
  -> C_OWNER_FILL
  -> C_SEND_RETRY / C_WAIT_RESPONSE
  -> PAGE_PUBLISH            (may repeat for pages 0..3)
  -> FINAL_DRAIN
  -> CHECKPOINT_BARRIER
  -> COMPLETE
```

The issue/response/C states pipeline only to the explicitly provisioned owner
count.  The lower-bound ledger below deliberately uses one A-response owner and
one C-line owner; this serializes work but proves finiteness.  More owners are a
performance/design choice and must be counted.

### Exact identities and ownership

- Descriptor identity: `(G,i)`; descriptor content includes aligned A line,
  `wid`, and canonical issue serial.
- Backing transaction: `(G,backing_serial,kind,backing_line)`.
- A transaction: `(G,A_serial,A_line,descriptor_range)`.
- C transaction: `(G,C_serial,C_line,valid_mask)`.
- Page identity: `(G,page)` for four 4K pages.

An address alone is never sufficient: the same A line may be issued in two
native drain epochs, and the same backing/C line may be reused by later
generations.  Serial plus generation disambiguates both.

### Retry rules

Allocate a serial and reserve its owner before the first send attempt.  Port
retry reuses the identical transaction and does not increment expected counts,
allocate another descriptor range, or create another C owner.  Acceptance
changes `reserved -> in_flight`; it is not completion.  Only the matching
response changes `in_flight -> completed/free`.

Unknown, duplicate, stale-generation, wrong-line, wrong-kind, or remapped
responses are fatal contract violations.  They must not free capacity or
advance page counts.  The test model exercises port retry identity, wrong-line
response rejection, stale generation rejection, and duplicate ACK rejection.

### Backing ACK and immutable-run rules

Active run buffers remain owned through the matching coherent write response.
They cannot be reused merely because a `WriteReq` was accepted.  Merge cannot
read a run until all of that run's writes have matching responses.  Once
frozen, a run is immutable.  Each reader has an exact record count; early
iterator exhaustion, an extra record, duplicate i, or a decreasing key fails
closed.

### A response and C publication

One issued A request owns its descriptor group until the response.  On a
matching response, each destination record selects `payload[wid]` and reserves
exactly one C position.  A completion bitmap rejects duplicate i.  A C-line
owner remains unique while dirty; no second owner may reserve that line.

For dense C, the owner ultimately sends one full 64-byte line for each eight
FP64 results.  Page p becomes publishable only when all its logical positions
are accounted and every C write touching the page has a matching response.
The page notification is a credit, not payload storage.  Final completion also
requires empty A/run/C owners and exact ACK conservation.

### Drain, checkpoint, and restart

The weakest restartable contract checkpoints only at a quiescent durable
barrier:

- all run writes needed for future work are acknowledged and immutable;
- there is no A response owner or C write in flight;
- completion bitmap, page counts, run cursors, next serial, and frozen-run
  headers are durable; and
- all issued C writes represented as complete have matching responses.

Restart increments generation, restores durable cursors/bitmap/counts, and
rejects any response from the old generation.  Supporting arbitrary midflight
checkpoints would require a durable transaction journal, idempotent reissue,
and response tombstones; it is not assumed here.  Drain/checkpoint is allowed
to wait, but it cannot reuse a buffer or claim completion early.

## 6. Field-by-field downstream replay ledger

This is a deliberately slow but finite implementation **after an analysis
oracle has assigned native issue serials**.  All rounding is `ceil(bits/8)`.
It is a packing contract for run sorting/merge/ownership, not a complete exact
native-order implementation and not a synthesized macro.  Exact recovery must
add or overlap a genuinely bounded serial-derivation/external-native-scheduler
ledger; that missing quantity is not assigned a fictitious number here.

### Reorder-specific on-chip state

| Component | Meaningful fields/arithmetic | Bytes |
|---|---:|---:|
| K active descriptors | `4096 * (64 line + 14 i + 3 wid + 14 issue serial + 1 live) = 4096 * 96 bits` | 49,152 |
| Four coherent run line buffers | `4 * 64 B` | 256 |
| Run-buffer tags/state | `4 * (64 backing line + 64 generation + 64 tx serial + 2 run + 10 line index + 3 state)` | 104 |
| Four merge heads | views into the current four run-line buffers; no extra descriptor record | 0 |
| Four cursors + four counts | `4 * (13 + 13) bits` | 13 |
| Global merge control | `64 generation + 64 next serial + 15 emitted count + 3 phase` | 19 |
| Finite heap-sort control | `13 length + 12 root + 13 child + 3 phase`; swap uses one reserved K-array slot | 6 |
| Exactly-once completion bitmap | `16384 bits` | 2,048 |
| Four page counts/ready/published | `4*13 + 4 + 4 bits` | 8 |
| One A response owner | `64-B payload + (64 line + 64 G + 64 serial + 15 cursor + 15 count + 2 state)` | 92 |
| One C-line owner | `64-B payload + (64 line + 64 G + 64 serial + 8 valid + 8 dirty + 3 state)` | 91 |
| **Packed downstream replay subtotal** | exact sum above | **51,789 B** |

The K-entry array is the only random-access array of active producer
descriptors in the downstream replay stage.  During run construction, moving
four records into an output line buffer frees their four array slots before the
buffer becomes live.  During merge, the construction array is empty; the four
heads alias records already present in the four input line buffers.  Heap sort
reserves one of the K array slots as its swap location.  Thus no phase has more
than K live producer records.  Transaction owners and line buffers are
separately enumerated bounded control/payload.  The one-owner choice is
sufficient for replay correctness but makes no throughput claim.  It does not
explain how native issue serials are derived.  ECC, SRAM periphery, ports,
arbiters, coherence queues, translation caches, serial-derivation state/work,
and wiring are excluded.

The 14-bit issue serial is sufficient only for at most one A request group per
logical i (0..16,383).  If the chosen event contract permits extra speculative
or replay issues, the field must widen and those issues must be semantically
defined; they cannot be hidden in a lower bound.

### Coherent backing footprint and traffic

The meaningful descriptor fields are 96 bits, but the tested wire record is
the explicit 16-byte format `<QHHBB2x>`: 64-bit line, 16-bit i container,
16-bit serial container, 8-bit wid, 8-bit flags, and two reserved bytes.

| Backing form | Footprint | Required record payload traffic |
|---|---:|---:|
| Keep K resident, spill N-K | `12,288 * 16 = 196,608 B` | `196,608 write + 196,608 read = 393,216 B` |
| Four fully immutable runs | `16,384 * 16 = 262,144 B` | `262,144 write + 262,144 read = 524,288 B` |

Spill-only is the minimum steady-state footprint when the retained K records
remain valid until merge.  Full runs are simpler for checkpoint/restart and
uniform merge.  Traffic counts are semantic record bytes crossing the coherent
interface once each way.  Cache-line write allocation, read-for-ownership,
eviction, invalidations, retries, and DRAM bytes are not implied.

### Common semantic payload and scan traffic

| Quantity | Exact count |
|---|---:|
| One B scan | `16,384 * 4 = 65,536 B = 1,024 lines` |
| Four B scans | `262,144 B = 4,096 line reads` |
| Useful logical A/C result | `16,384 * 8 = 131,072 B` |
| Dense C publication | `131,072 B = 2,048 full lines` |
| Two 4K FP64 SPD payload slots | `2 * 4,096 * 8 = 65,536 B` |

A source traffic is input-dependent.  If U is the number of issued A line
groups after the exact native drain/coalescing rules, semantic A response bytes
are `64*U`.  `1 <= U <= 16,384` for a nonempty gather, so 64 B through
1,048,576 B.  Duplicates and lines revisited after native early drains determine
U.  The common 131,072 useful result bytes must not be added to “metadata
savings” as though another design did not produce C.

### Existing source host-language representation

At this revision, `OffsetTableEntry` contains three `int`s; a typical ABI gives
12 bytes.  The source separately allocates one `bool` validity element and
reserves one `int` free-list element per capacity slot.  Under 4-byte int and
1-byte bool assumptions, the raw 16K Offset allocation/capacity subtotal is

```text
16,384 * (12 + 1 + 4) = 278,528 B
```

excluding vector object, allocator, and temporary-vector overhead.  This is a
simulator host accounting fact.  `RowTableEntry::Entry` is `Addr + int + int`
plus separate valid/claimed arrays, row headers, and all configured RowTable
organizations (`src/mem/MAA/Tables.hh:95-146`).  Its total depends on runtime
DRAM/configuration geometry.

Likewise, Python object `sizeof` is irrelevant to the contract; only the
explicit 16-byte wire encoding is stable.  C/C++ struct `sizeof`, allocator
bytes, packed bit counts, coherent bytes, and synthesized area are five
different quantities.

### Synthesis boundary

No number above is mm2, SRAM macro area, energy, latency, or frequency.  A
synthesis estimate needs chosen RAM/BCAM/register mappings, banking and port
counts, ECC, coherence interfaces, comparator/sort/merge datapaths, arbitration,
clock target, technology, and physical design.  Host-language `sizeof` cannot
be converted into synthesis area.

For context only, the repository's checked comparable configuration ledger
reports 66,688 B for a bounded-4K Row/Offset/invalidator lower bound and
254,464 B for the 4K-SPD/full-16K-metadata counterpart.  Those figures use a
different whole-configuration boundary and must not be silently summed with
the 51,789-B downstream replay subtotal here.  In particular, 51,789 B is not
a validated replacement for native serial derivation.

## 7. Mechanism comparison

| Mechanism | B work | Reorder image | Exact native issue order? | Plain conclusion |
|---|---:|---:|---|---|
| Four B-scan buckets | four scans, 262,144 B | none; K active | No in general; skew causes extra drains and bucket barriers change order | Correct bounded results with a stable exactly-once selector, not a 16K order reconstruction |
| Four immutable sorted runs | one scan, 65,536 B after serial derivation | 262,144-B full wire image, 524,288-B record R+W | Replay yes only with a valid canonical native serial/event contract; this artifact has only an oracle, and address-key sort alone is insufficient | Exact replay relocates 16K metadata; complete derivation cost remains open |
| Full 16K on-chip metadata | one scan | native Row/Offset state on chip | Yes by definition for the implemented event scheduler | Simplest exact reference; costs the full metadata lifetime |
| Two-slot SPD payload cache | consumer-dependent; four 4K pages | no A reorder image | No | Caches 65,536 B of values; payload caching does not map responses to original i |
| Greedy retain/spill without replay/image | one partial admission until forced | only K records | Impossible | Loses destinations or issues before unseen slice heads |

Four sorted runs can deliberately implement a *new* global
`(bank,row,line,i)` order, often a sensible design.  It should then be compared
for correctness/locality as a new order, not described as bit-for-bit native
issue order.

## 8. Smallest honest experiment

Do not start by implementing an “exact keep-top-half” simulator treatment.  The
smallest informative experiment is:

1. Freeze one deterministic B trace and address mapping.
2. Generate the native reference issue groups and exact `(i,wid)` membership.
3. Replay the existing four bounded bucket scans with K=4K, a finite nonzero
   filter throughput, retained C combiner state, and response-bearing C writes.
4. Require exact C, exact iteration conservation, no capacity violation, exact
   request/response/ACK conservation, and structural counts for B words, A
   source groups, inserted rows/lines, C writes, and drain barriers.
5. Report whether it improves A line/row grouping relative to a one-pass 4K
   epoch.  Do not claim native 16K issue-order recovery.

If exact order remains valuable after that result, prototype four immutable
runs in a standalone trace replay first.  The gate is exact equality of the
entire native issue-group sequence and destination membership, including
adversarial skew/duplicates and iterator exhaustion.  Only then is a coherent
gem5 implementation justified.  At that point the design should be named
“externalized 16K reorder metadata,” because that is what it is.

## 9. Executable contract and evidence limit

`experiments/tests/test_professor_retain_spill_contract.py` contains only the
Python standard library.  It checks:

- exact integer ledger values and explicit 16-byte record encoding;
- the bank-interleaved slice permutation;
- first-insertion row/line order and cross-slice round robin;
- Offset- and Row-capacity early drains;
- duplicates and response-to-original-i mapping under reversed responses;
- the minimal K=2/N=3 greedy issue-order counterexample;
- information loss and iterator exhaustion for keep-top-half;
- oracle-labelled immutable-run exact merge, truncation, finite range-spool
  bounds, and skew (not bounded serial-derivation evidence);
- retry identity, remap/stale-generation rejection, ACK conservation, page
  publication, and no owner reuse before matching response.

The model intentionally omits clocks, cache timing, DRAM timing, and gem5.
Its `NativeEpoch` reference covers complete fill/forced-drain structural epochs;
it does not reproduce cycle-level response-driven refill.  Consequently its
oracle serials are conditional on that stated schedule and are not a universal
gem5 trace oracle.  Passing the tests is not cycle, speedup, energy, area,
synthesis, or complete serial-derivation evidence.
