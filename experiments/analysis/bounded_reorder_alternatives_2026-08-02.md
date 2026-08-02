# Bounded Reorder Alternatives for `A[B[i]]`

## Recommendation

The simplest credible first experiment is the already-wired four-way
`grow_addr % 4` rescan with an explicitly finite filter and a 4K Row/Offset
epoch.  It is the only candidate below that needs neither a new coherent
metadata format nor a new retained-data structure.  It must be described as a
bounded, row-bucketed 4K schedule, **not** as a recovered 16K reorder window.

The experiment should be rejected as a route to 16K locality if, after exact
correctness and traffic checks, it does not strictly reduce both A source-line
requests and inserted A-row descriptors relative to the one-pass bounded4
control.  A treatment that pays four B scans but improves only C retirement or
only elapsed time has not established the mechanism being tested.

If a mechanism must reproduce the global 16K A-row order, the sorted-run
alternative is the honest bounded design: it moves a complete descriptor image
to coherent backing.  That is simpler to reason about than retaining 16K
Row/Offset state on chip, but it does not eliminate the metadata; it relocates
it.  A small retained A-line cache is cheaper state and deserves a later test,
but it recovers reuse only and cannot promise global row order.

This is a source and arithmetic audit at simulator commit
`87230d797ce885f1c31cede6ebdc78ef62def917`.  It is not a gem5 result.  No
simulator source was changed and no timing, area, energy, or speedup claim is
made.

## Scope and accounting boundary

The concrete operation is an unconditioned FP64 gather
`C[i] = A[B[i]]` with:

- `N = 16,384` logical results;
- `K = 4,096` active entries/physical-page elements;
- 4-byte `B` indices, 8-byte A/C words, and 64-byte cache lines;
- one indirect unit and the default 32 4-byte SPD lane tiles used by the
  checked storage ledger.

One full B traversal is therefore `16,384 * 4 = 65,536 B` or 1,024 cache
lines.  One logical FP64 result is `16,384 * 8 = 131,072 B`.  The tables below
count reorder backing separately from that result payload: every alternative
must eventually produce the same 131,072 result bytes, while the transparent
path additionally virtualizes those payload bytes through coherent backing.

The checked reference ledger in
`experiments/analysis/hybrid_reorder_cost_analysis_2026-08-02.md:119-128`
defines a comparable fully bounded 4K design as 653,138 B, including 512 KiB
physical SPD, the bounded B feeder, A-response pool, C combiner, virtual
control, and 66,688 B of 4K Row/Offset/invalidator metadata.  The corresponding
4K-SPD/full-16K-metadata ledger is 842,482 B with 254,464 B of retained
Row/Offset/invalidator metadata.  The exact arithmetic difference is 189,344 B.
These are bit-packed/configuration lower bounds, not physical area estimates.

For the bounded4 dimensions, the 66,688 B split is exact: 9,728 B of Offset
entries, 48,640 B of Row entries, 4,224 B of Row headers, and a 4,096-B
invalidator bitmap.  A scheduler may replace the first 62,592 B; it cannot
silently delete the independent invalidator state.

Alternative-specific state below either adds to the 653,138-B ledger or
explicitly replaces its 66,688-B metadata component.  Shared ports, queues,
combiner state, and the 4K SPD payload are not silently counted twice.

## What the current mechanisms actually do

### The labels are overloaded

The source has two different notions commonly called “direct4”:

1. In `experiments/scripts/run_virtual_tile_consumer_case.sh:92-123`,
   `native_direct_16k` makes one 16K native direct-index call, while
   `native_direct_4k` makes four native 4K calls.  The benchmark loop at
   `benchmarks/API/test_virtual_tile_consumer.cpp:149-174` gives those calls
   four disjoint B ranges.  Both collectively traverse B once, but only the
   16K call has one 16K Row/Offset lifetime; the four 4K calls cannot reorder A
   requests across call boundaries.
2. The FLAG/XRAGE `direct_index_4k` arm configures a 16K logical operation with
   4K physical SPD.  `MAA.py:17-22,34-39` and `MAA.cc:57-71` resolve physical
   payload capacity independently from Offset capacity and epoch; zero Offset
   parameters resolve to the 16K logical capacity.  Thus that arm can be 4K in
   payload while retaining a 16K metadata lifetime.  A genuinely bounded4
   control must explicitly set both Offset capacity and drain epoch to 4K (and
   use its matched Row geometry).

The API also distinguishes native direct index opcode 14 from virtual direct
index opcode 13 (`src/mem/MAA/IF.hh:35-53` and
`benchmarks/API/MAA_gem5.hpp:372-393,448-469`).  Comparisons must name the
opcode, logical size, physical size, Offset capacity, and Offset epoch rather
than relying on “direct4.”

### SPD payload virtualization is not reorder-metadata virtualization

`SPD::SPD` allocates exactly
`num_tiles * physical_tile_elements * sizeof(uint32_t)` payload bytes
(`src/mem/MAA/SPD.cc:237-277`).  For the default 32 lane tiles, changing the
physical element count from 16K to 4K changes the SPD payload from 2 MiB to
512 KiB.  It does not change the independent Row/Offset allocation passed to
each indirect unit (`src/mem/MAA/MAA.cc:391-415`).

The virtual direct-index producer names a coherent backing address in its API
(`benchmarks/API/MAA_gem5.hpp:372-393`).  Its indirect retirement path emits
response-bearing `WriteReq` packets, tracks outstanding lines, and completes
them on the returned response (`src/mem/MAA/IndirectAccess.cc:2530-2544,
2608-2678,3238-3250`).  This is payload handoff; the producer can still keep
one 16K Row/Offset epoch while writing gathered values pagewise.

The transparent consumer is a second mechanism layered after that producer.
`TransparentSPDController.hh:22-39` fixes 16K logical elements, 4K physical
elements, four pages, and one fill/compute/store chain at a time.  `MAA.cc:
676-688,800-815,832-867` enforces that geometry and emits four native chains.
It contains no A-reorder table and does not create the producer's 16K A-row
locality.  The existing matched matrix's source-request signature is consistent
with the producer retaining the 16K epoch
(`experiments/analysis/transparent_spd_matched_matrix_2026-08-02.md:43-54`).

There is also a correctness boundary: transparent final `STREAM_ST` completion
currently follows packet acceptance, not a true write response
(`src/mem/MAA/MAA.cc:1145-1171` and
`experiments/analysis/logical_spd_cache_gem5_integration_plan_2026-08-02.md:
31-46`).  A new reorder design must use the response-bearing indirect-write
contract for state release; the current transparent store cannot serve as a
durability oracle.

### Professor notes and retain/spill artifacts

The July 7 notes describe the intentionally simple first virtualization model
as 4K payload chunks handed to LLC, followed by program service from LLC
(`/data1/nier/worktrees/dx100-research-architecture-design-lead-20260717/docs/
meetings/meeting_notes_2026-07-07.md:106-119`).  The same notes require finite
transfer width and no resource reuse before the real completion edge
(`:157-174`).  They virtualize payload; they do not assert that 16K reorder
metadata disappears.

The checked retain/spill analysis through commit `57eea77` reaches the same
boundary for metadata: a 4K selected subset needs at least four B scans; a
balanced selector needs a fifth scan or materialized external records; and a
skewed bucket must drain rather than overflow.  The current partition code
implements the static bounded version, not a balanced global reconstruction.

## Alternative 1: repeated B scans with range/bucket filters

### Event diagram

```text
for bucket p = 0..3:
    scan all 16K B words
          |
          +-- grow(A[B[i]]) % 4 != p --> discard this pass
          |
          `-- selected --> 4K Row/Offset --> A issue/response --> C owner
                                      ^             |              |
                                      `-- drain ----'       matching WriteResp
after bucket barrier: retain partial C owners; clear only active Row/Offset
```

This is the bounded form of the existing implementation.  When one partition
ends, `fillRowTable` increments the partition, resets the B cursor to zero, and
forces a drain (`IndirectAccess.cc:833-871`).  Selection is exactly
`grow_addr % partitions` (`:904-983`).  Offset occupancy at the configured
epoch triggers a drain rather than overflow (`:933-941`).  A finite filter
counts every examined word and can add modeled latency
(`:1005-1021`).  `virtual_partition_keep_combiner` preserves C combiner state
across the partition barrier (`:1685-1724`).

### Exact bounded state

Use the 653,138-B bounded4 reference ledger unchanged, plus one 31-bit filter
control word rounded to 4 B:

| Field | Bits |
|---|---:|
| current bucket, four values | 2 |
| B scan cursor, values 0 through 16,384 | 15 |
| selected-entry count, values 0 through 4,096 | 13 |
| drain/barrier pending | 1 |
| **Total** | **31 bits = 4 B packed** |

The comparable bounded total is therefore **653,142 B**.  No 16K selector-label
array or completion bitmap is required: the static modulo function assigns
every true iteration to exactly one bucket, and the existing code accounts a
false predicate once in partition zero (`IndirectAccess.cc:991-999`).

### Passes, bytes, locality, and bottleneck

- **B passes:** exactly 4.
- **B scan bytes:** `4 * 65,536 = 262,144 B`; the arithmetic overhead versus a
  one-pass design is 196,608 B.  All 4,096 B cache lines are examined even when
  the selected bucket is sparse.
- **Reorder backing:** 0 B.  B itself must remain coherent and readable across
  the four scans; LLC residency is not guaranteed by the mechanism.
- **Global 16K row locality:** no guarantee.  A row group that fits in its
  bucket can remain together, but a bucket with more than 4K live entries must
  drain.  The adversarial single-bucket case becomes four or more 4K epochs
  inside one scan and cannot equal one global 16K schedule.
- **Expected bottleneck:** repeated B/LLC reads and finite filter work, followed
  by skew-driven Row/Offset drains.  This is a work/traffic statement, not a
  timing prediction.
- **Does it recreate 16K metadata elsewhere?** No.  It gives up the guarantee
  instead.

### Ownership and correctness

The bucket function, physical address mapping, and predicate result must be
stable across all scans.  Each selected A response retains logical `i` until it
reaches the unique C-line owner.  Partial C owners survive partition barriers.
An owner is released only by the matching `(descriptor generation, C line,
transaction serial)` `WriteResp`, never by send acceptance.  Completion means
all 16K iterations are accounted once, all A responses are consumed, the final
C combiner is empty, and no response-bearing write remains outstanding.

## Alternative 2: four sorted 4K runs in coherent LLC

### Event diagram

```text
one sequential B pass
    |
    +-- build 4K x 16-B records -- fixed in-place sort -- WriteReq run 0 -- ACK
    +-- build 4K x 16-B records -- fixed in-place sort -- WriteReq run 1 -- ACK
    +-- build 4K x 16-B records -- fixed in-place sort -- WriteReq run 2 -- ACK
    `-- build 4K x 16-B records -- fixed in-place sort -- WriteReq run 3 -- ACK

four immutable run streams --> 4-way merge by (bank,row,line,i)
                                  |
                         one equal-A-line group
                                  |
                    A request/response, then stream i records
                                  |
                       unique C owners --> WriteResp
```

Each 16-B record stores a 64-bit aligned physical A-line address and a second
64-bit field containing 14-bit logical `i`, 3-bit source word ID, live/control
flags, and reserved bits.  A fixed in-place heap sort gives a bounded sort over
exactly 4,096 records; it is not modeled as an instantaneous host-language
`sort`.  The four runs use one canonical total order so a 4-way merge reproduces
the same global A row/line order independent of original 4K page boundaries.

An equal-line group can be arbitrarily large without an unbounded on-chip list:
issue one A-line request, retain its one 64-B response in the existing bounded
response pool, and stream the consecutive destination records before advancing
the merge.  Backpressure from the C combiner simply stalls the merge heads.

### Exact bounded state

This alternative replaces the ledger's 62,592-B 4K Row/Offset portion with the
following 66,013-B run state.  The independent 4,096-B invalidator remains:

| Component | Arithmetic | Bytes |
|---|---:|---:|
| active record array | `4,096 * 16` | 65,536 |
| four coherent line buffers | `4 * 64` | 256 |
| buffer tags/state | `4 * (64-bit backing line + 64-bit generation + 64-bit transaction serial + 2-bit run + 9-bit line index + 3-bit state)` | 103 |
| four 16-B merge heads | `4 * 16` | 64 |
| four 13-bit cursors plus four 13-bit counts | 104 bits | 13 |
| global generation, next serial, 15-bit emitted count, 3-bit phase | 146 bits | 19 |
| heap-sort length/root/child, 16-B swap register, 3-bit phase | 170 bits | 22 |
| **Run state** |  | **66,013** |

The comparable total is therefore
`653,138 - 62,592 + 66,013 =` **656,559 B**.  Equivalently, reorder/invalidator
state is `66,013 + 4,096 = 70,109 B`.  This is a packing contract only; it says
nothing about implementation area or sort throughput.

### Passes, bytes, locality, and bottleneck

- **B passes:** exactly 1.
- **B scan bytes:** 65,536 B.
- **Reorder backing footprint:** `16,384 * 16 = 262,144 B`.
- **Reorder backing traffic:** 262,144 B of run writes plus 262,144 B of run
  reads = **524,288 B**, excluding cache-protocol overhead and the common
  131,072-B result payload.
- **Global 16K row locality:** yes, by construction, provided all four runs use
  the same complete physical bank/row/line key and the merge does not issue a
  later key early.  Duplicate A lines across runs become one consecutive group.
- **Expected bottleneck:** fixed sort/merge work, 512 KiB of coherent metadata
  traffic, and deliberate stalling while an equal-line A response or C-owner
  credit is unavailable.
- **Does it recreate 16K metadata elsewhere?** Yes, exactly: the 256-KiB backing
  image is 16K records.  This is external metadata virtualization, not metadata
  elimination.

### Ownership and correctness

The run region is scheduler-private for the descriptor generation.  Every
64-B run write carries a unique transaction serial; a buffer/run region cannot
be reused and merge reads cannot begin until all matching write responses have
arrived.  Runs are immutable during merge.  The merge checks nondecreasing keys
and emits exactly the recorded live count.  A responses match generation,
physical source line, and transaction serial.  C retains the same unique-owner
and true-`WriteResp` rule as alternative 1.  A duplicate, missing, stale, or
out-of-order run record is a fatal correctness failure, not a retry hint.

## Alternative 3: small retained A-line cache

### Event diagram

```text
page 0 B (4K) --> 4K row schedule --> A miss --> retain A line in 64-entry cache
page 1 B (4K) --> 4K row schedule --> A hit? --+--> C owner --> WriteResp
page 2 B (4K) --> 4K row schedule --> A hit? --+
page 3 B (4K) --> 4K row schedule --> A hit? --+
                                      `-- miss/refill with exact transaction tag
```

This intentionally small design caches returned read-only A payload lines
across the four disjoint 4K epochs.  It is not a prefetch oracle: a future B
entry is unknown until its page is scanned.  It therefore recovers only exact
cross-page A-line reuse whose reuse distance fits the finite cache; each page's
misses remain ordered within that page's 4K Row/Offset window.

### Exact bounded state

Use a 64-line, 16-set, four-way cache and four miss-status entries.  Add to the
653,138-B bounded4 ledger:

| Component | Arithmetic | Bytes |
|---|---:|---:|
| A-line payload | `64 * 64` | 4,096 |
| per-line tag/state | `64 * (64-bit line + 64-bit descriptor generation + valid + filling)` = 8,320 bits | 1,040 |
| round-robin replacement | `16 sets * 2 bits` | 4 |
| four MSHRs | `4 * (64-bit line + 64-bit generation + 64-bit transaction serial + 6-bit target + valid)` = 796 bits | 100 |
| next transaction serial | 64 bits | 8 |
| **Added state** |  | **5,248** |

The comparable total is therefore **658,386 B**.  Payload and tags are shown
separately because the 4-KiB line data is not Row/Offset metadata.

### Passes, bytes, locality, and bottleneck

- **B passes:** one traversal made of four disjoint 4K ranges.
- **B scan bytes:** 65,536 B.
- **Reorder backing:** 0 B.
- **Global 16K row locality:** no.  Hits remove repeated A reads, but page 2
  cannot move an unseen miss ahead of page 0, and rows cannot be globally
  sorted across all B entries.
- **Expected bottleneck:** the cross-page A-line reuse working set, set
  conflicts, four-MSHR backpressure, and coherent lookup/refill work.  With a
  reuse distance above 64 retained lines it converges to bounded4 behavior.
- **Does it recreate 16K metadata elsewhere?** No.  It retains 64 A payload
  lines and their tags, not 16K destination descriptors.

### Ownership and correctness

This first design is scoped to read-only A during the gather.  Admission is
after the program's ordering fence; entries are tagged by logical descriptor
generation and invalidated at descriptor retirement.  If concurrent writers to
A are allowed, the cache must be placed on a coherent snoop path or the design
is incorrect.  A fill completes only on a response matching line, generation,
and unique transaction serial.  Duplicate/late responses cannot install data
or free an MSHR.  C writes retain the unique-owner/true-`WriteResp` rule.

## Why PFCC/CHSO owner retention is not the small alternative above

The PFCC-64 artifact at commit `54f712a` and CHSO-384 artifact at commit
`ea02c04` retain **full 16K Row/Offset visibility before first A issue**.  CHSO's
own specification says it performs a 16K build barrier and does not count the
existing full Row/Offset storage again; its 384 destination owners replace the
normal combiner role.  It is therefore a bounded hybrid scheduler, but not an
answer to “remove the 16K reorder metadata.”  It retains that metadata and adds
owner/protocol policy around it.

The published CHSO `ea02c04` contract reported 129,840 B of policy state, but
independent review session
`corrected-hybrid-review-20260802-155703-bb796da7` rejected that number and the
replay as promotion evidence.  Reproduced defects included incomplete source
response identity, combined-credit violation, incorrect row-quantum rotation,
uncharged scans/sorts, no payload oracle, omitted selector/protocol state, and a
14-bit source-line field even though archived inputs require 18 bits.  The
replay's request/order counts are consequently evidence only for the code as
written, not a validated hardware or performance contract.  A later repaired
artifact must receive a fresh independent review before superseding this
qualification.

The small retained-line cache above is deliberately different: it does not know
future destinations, owns no future C line, performs no 16K preissue build, and
cannot claim global 16K row locality.  If a future design instead retains C-line
owners, an owner must be unique from first reservation through matching write
response; no normal combiner may own the same line concurrently.  That was the
correct architectural lesson from the CHSO review even though the artifact was
rejected.

## Side-by-side decision table

| Alternative | Exact reorder-specific on-chip state | B passes / bytes | Reorder backing footprint / traffic | Global 16K A-row locality | Expected bottleneck | Recreates 16K metadata? |
|---|---:|---:|---:|---|---|---|
| Four grow buckets | 66,688-B bounded4 metadata + 4 B control | 4 / 262,144 B | 0 / 0 B | No guarantee; skew drains | B scans, filter work, skew drains | No; gives up guarantee |
| Four sorted runs | 66,013-B run state + 4,096-B invalidator, replacing 62,592-B Row/Offset | 1 / 65,536 B | 262,144 / 524,288 B | Yes, with canonical merge | Sort/merge work and coherent metadata traffic | Yes, in LLC |
| 64 retained A lines | 66,688-B bounded4 metadata + 5,248 B cache | 1 / 65,536 B | 0 / 0 B | No; exact reuse only | Reuse distance, conflicts, refill credits | No; retains payload |
| Existing direct4/full metadata | 254,464 B retained metadata | 1 / 65,536 B | 0 reorder metadata bytes | Yes for its 16K epoch | Full on-chip metadata | Yes, on chip by definition |
| PFCC/CHSO owner family | full 16K Row/Offset plus owner policy; published 129,840-B add-on is rejected | 1 / 65,536 B | 0 in published model | Attempts a hybrid, not identical global order | owner pressure and policy work | Yes, retained on chip |

The B byte counts are semantic data bytes.  They do not predict cache hits,
cache-protocol traffic, elapsed cycles, or DRAM bytes.  The backing traffic for
sorted runs is the exact record payload crossing the coherent interface once in
each direction; write allocation, eviction, and coherence messages must be
measured separately.

## First gem5 experiment and fail-closed reject criterion

Use the existing partition machinery on one frozen, correctness-checking gather
that exposes a direct4-versus-full-row A-request gap.  Run four matched arms:

1. direct-index 16K physical/full 16K metadata;
2. direct-index 4K physical/full 16K metadata;
3. direct-index 4K physical/4K Offset capacity and epoch with one partition;
4. arm 3 plus four grow partitions, finite nonzero
   `virtual_index_filter_words_per_cycle`, and
   `virtual_partition_keep_combiner=true`.

Freeze the same binary, input, address mapping, cache hierarchy, logical size,
Row geometry, response pool, C combiner, and write limit.  The treatment must
show exactly 65,536 examined B words, four partition transitions, 262,144 B of
B scan data, no Offset/Row capacity overflow, no combiner flush at an
intermediate partition, exact output, and zero missing/duplicate iterations.
Trace every A request identity and every C write request/response identity.

**Reject the treatment as a 16K-locality recovery mechanism if it fails to
strictly reduce both A source-line requests and inserted A-row descriptors
relative to arm 3.**  Also reject immediately on output mismatch, an uncharged
filter, any state above its configured bound, reuse before matching response,
or a missing/duplicate lifecycle event.  Only after this structural gate should
`simTicks` be examined; elapsed time alone cannot rescue a mechanism that did
not recover the claimed A-side locality.

If the treatment passes, the next experiment should implement the sorted-run
format and validate exact run bytes/order/ACKs before using timing.  The small
retained-line cache follows only if trace replay shows enough cross-page A-line
reuse to exercise 64 entries; CHSO/PFCC should wait for a repaired and
independently accepted state/protocol contract.

## Evidence index

- Baseline and parameter resolution: `src/mem/MAA/MAA.py:14-39`,
  `src/mem/MAA/MAA.cc:45-145`, and `src/mem/MAA/SPD.cc:237-277`.
- Row/Offset structures: `src/mem/MAA/Tables.hh:52-198` and
  `src/mem/MAA/IndirectAccess.cc:833-1021`.
- Partition barriers and combiner lifetime:
  `src/mem/MAA/IndirectAccess.cc:1685-1724`.
- Response-bearing virtual retirement:
  `src/mem/MAA/IndirectAccess.cc:2530-2678,3238-3250`.
- Transparent lifecycle: `src/mem/MAA/TransparentSPDController.hh:19-359` and
  `src/mem/MAA/MAA.cc:648-944,1145-1171`.
- Hardware/accounting audit:
  `experiments/analysis/spd_hardware_accounting_2026-08-02.md` and
  `experiments/scripts/report_maa_storage.py`.
- Retain/spill arithmetic:
  `experiments/analysis/hybrid_reorder_cost_analysis_2026-08-02.md` and
  `experiments/analysis/hybrid_reorder_cost_model.py` through `57eea77`.
- Transparent mechanism signature:
  `experiments/analysis/transparent_spd_matched_matrix_2026-08-02.md`.
- Corrected-hybrid candidate/review: commits `54f712a`, `ea02c04`, and
  coordination review `corrected-hybrid-review-20260802-155703-bb796da7`.
