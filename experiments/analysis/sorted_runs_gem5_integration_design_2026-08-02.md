# Four sorted runs: response-timed gem5 integration design

Date: 2026-08-02
Source basis: `b6f000cdffe0bbdab534fadd9157320bdb95c239`
Status: design only; no production source change, gem5 run, or performance claim

## Decision

Four sorted 4K runs are the better **next bounded-reorder mechanism to make
response-timed** than range spool, but they are not ready for implementation
promotion. Sorted runs read the 256-KiB descriptor image once during merge;
range spool reads that image four times and still needs bounded sorting windows.
The price is a new fixed heap-sort/four-way-merge controller and a response-safe
metadata transport that cannot reuse the current Row Table lookup path.

This document rejects two stronger claims:

1. The `70,109 B` reorder number is a useful bit-packed mechanism lower bound,
   but it is not yet a complete gem5 implementation ledger. The Python replay
   omits or host-represents packet sender state, MMU/page-boundary progress,
   finite C-owner containers, checkpoint/drain state, and a real response
   disposition contract. Those objects must be fixed-size and charged before
   the `656,559 B` total can be called implementation-complete.
2. The replay at `b6f000c` is rejected as acceptance evidence. An independent
   review reproduced its numerical trace output but found aliased/mutable
   `TransferTag` identity, bool-forged equality, no charged 64-bit exhaustion
   enforcement, and mutable caller-pattern aliasing. The trace counts therefore
   remain pending structural observations, exactly as required by the task.

The independently accepted hidden-SPD substrate at
`3c7cb3ae15daa54619404a4ae9b6015e28b79f41` is a separate dependency: it
allocates and releases two private FP64 4K slots per MAA, or 262,144 B for four
MAAs, but deliberately wires no controller. The first sorted-runs slice below
publishes directly to coherent C backing and does **not** use those hidden
slots. Enabling both would add the hidden 262,144 B to the sorted-runs ledger;
the two equal-sized 256-KiB objects must never be conflated.

## Evidence boundary

| Input | Use here | Status boundary |
| --- | --- | --- |
| [Professor-inspired bounded policies](professor_bounded_reorder_policies_2026-08-02.md) and its Python model at `b6f000c` | Mechanism, 16-B records, 4K bound, traffic definitions, and lower-bound ledger. | Rejected for identity/alias/exhaustion/coverage defects. Reproduced counts are pending structural observations, not acceptance or timing evidence. |
| [Baseline reorder/storage audit](baseline_dx100_reorder_storage_audit_2026-08-02.md) | B/C separation and Row/Offset/A-response semantics. | Baseline source audit; no inference that current C++ allocations are synthesized area. |
| [Logical SPD cache vertical-slice design](logical_spd_cache_vertical_slice_design_2026-08-02.md) | Generation/serial discipline, response-versus-acceptance boundary, finite slots, publication, drain/checkpoint rules. | Design contract. Its hidden payload substrate was later accepted separately at `3c7cb3a`; its controller remains unwired. |
| Current `b6f000c` source | Concrete API, IF, IndirectAccess, Tables, StreamAccess, ports, SPD, configuration, and stats insertion points. | Authoritative integration basis for this document. |

No trace count, cache hit, proxy transition, or accepted hidden-payload test is
treated as gem5 timing, application speedup, DRAM-command, RTL, area, or power
evidence.

## Audited current-source boundary

The following are facts in `b6f000c`, not proposed behavior.

- `INDIR_LD_VIRTUAL_INDEX` already names A, a direct B-index stream, a C
  backing range, and a completion token in
  [MAA_gem5.hpp:419](../../benchmarks/API/MAA_gem5.hpp#L419). Native direct
  index is the distinct opcode 14 at
  [MAA_gem5.hpp:495](../../benchmarks/API/MAA_gem5.hpp#L495); the opcode table
  is in [IF.hh:35](../../src/mem/MAA/IF.hh#L35).
- Direct B ingestion is bounded in cache lines, translates each line, and
  keeps pending/ready words in maps at
  [IndirectAccess.cc:589](../../src/mem/MAA/IndirectAccess.cc#L589). Those maps
  are simulator containers, not a finite hardware ledger.
- A descriptors are presently created from B by translating the aligned A
  line and mapping it to channel/rank/bank-group/bank/row before insertion in
  Row/Offset state at
  [IndirectAccess.cc:917](../../src/mem/MAA/IndirectAccess.cc#L917).
- `OffsetTableEntry` is the destination/word/next link, while Row Table state
  owns the physical line and row grouping
  ([Tables.hh:52](../../src/mem/MAA/Tables.hh#L52),
  [Tables.hh:95](../../src/mem/MAA/Tables.hh#L95)). A normal response remaps
  its address back through Row Table and then consumes Offset links at
  [IndirectAccess.cc:2039](../../src/mem/MAA/IndirectAccess.cc#L2039).
- The bounded virtual response path copies one 64-B A response into a retained
  response slot and drains its Offset chain into the destination combiner
  ([IndirectAccess.cc:2088](../../src/mem/MAA/IndirectAccess.cc#L2088),
  [IndirectAccess.cc:2726](../../src/mem/MAA/IndirectAccess.cc#L2726)). Its
  `packed_words` vector and source-reservation map are not acceptable as new
  sorted-runs hardware state
  ([IndirectAccess.hh:80](../../src/mem/MAA/IndirectAccess.hh#L80)).
- The destination combiner already rejects a duplicate destination word and
  applies finite line/word capacity, then creates response-bearing `WriteReq`
  retirement packets
  ([IndirectAccess.cc:2866](../../src/mem/MAA/IndirectAccess.cc#L2866),
  [IndirectAccess.cc:2991](../../src/mem/MAA/IndirectAccess.cc#L2991),
  [IndirectAccess.cc:2630](../../src/mem/MAA/IndirectAccess.cc#L2630)). Page
  ready is currently derived from scanned/expected/issued/completed word counts
  at [IndirectAccess.cc:2530](../../src/mem/MAA/IndirectAccess.cc#L2530).
- Normal stream stores use response-less `WritebackDirty`, and
  `writePacketSent` counts send acceptance as completion
  ([StreamAccess.cc:388](../../src/mem/MAA/StreamAccess.cc#L388),
  [StreamAccess.cc:465](../../src/mem/MAA/StreamAccess.cc#L465)). Neither is
  legal for spill or C publication.
- The port layer coalesces and orders outstanding work by physical address in
  dynamic maps/queues
  ([MAA.hh:799](../../src/mem/MAA/MAA.hh#L799)). `MAA::recvTimingResp` erases
  the address entry before the unit accepts the response
  ([Port.cc:698](../../src/mem/MAA/Port.cc#L698)); cache and memory ports then
  unconditionally consume/delete the packet
  ([CacheSidePort.cc:30](../../src/mem/MAA/CacheSidePort.cc#L30),
  [MemSidePort.cc:30](../../src/mem/MAA/MemSidePort.cc#L30)). The separately
  repaired logical-response wrapper must be independently accepted before this
  design can use it; packet acceptance is not action completion.
- There is exactly one stream unit per MAA in the current construction and its
  request table resets at instruction completion
  ([MAA.cc:149](../../src/mem/MAA/MAA.cc#L149),
  [StreamAccess.cc:203](../../src/mem/MAA/StreamAccess.cc#L203),
  [StreamAccess.cc:356](../../src/mem/MAA/StreamAccess.cc#L356)). Sorted-run
  transfers therefore share that one unit; they do not receive a free port.
- Current configuration separates logical tile elements, physical SPD
  elements, Offset capacity, and Offset epoch
  ([MAA.py:17](../../src/mem/MAA/MAA.py#L17),
  [MAA.py:34](../../src/mem/MAA/MAA.py#L34)). Existing virtual response,
  combiner, write-credit, and B-line capacities are also explicit
  ([MAA.py:63](../../src/mem/MAA/MAA.py#L63)).
- Statistics already distinguish port packets, indirect requests, unique
  words/lines/rows, fill/build/request/response cycles, and virtual traffic in
  [MAA.hh:528](../../src/mem/MAA/MAA.hh#L528). Sorted-run events need new
  non-overlapping counters; existing counters must not be relabeled.

The baseline storage audit remains the semantic authority for B versus C:
Row/Offset records carry address and return placement, while returned A data is
written to a distinct destination. Payload caching does not retain a 16K
ordering proof. The logical-SPD design reaches the same conclusion and requires
true write responses before destination publication.

## Exact operation and descriptor ABI

The first slice accepts only an unconditioned, dense FP64 gather:

```text
C[i] = A[B[i]],  i = 0..16383
B element = uint32_t, A/C element = 8 B, cache line = 64 B
```

It reuses the current `INDIR_LD_VIRTUAL_INDEX` high-level shape with an
internal/configured reorder policy; it does not add a new public opcode. In
sorted mode, instruction word 5 carries `descriptorBase`. The instruction
aperture is already 64 B, while the current API initializes only words 0--4
([MAA_gem5.hpp:114](../../benchmarks/API/MAA_gem5.hpp#L114)) and the current
decoder rejects any word beyond 4
([CpuSidePort.cc:449](../../src/mem/MAA/CpuSidePort.cc#L449)). Legacy forms
continue to dispatch at word 2 or 4; only a separately accepted sorted helper
waits for word 5. The operation owns that aligned 256-KiB private
descriptor-backing span, disjoint from A, B, C, visible SPD MMIO, and any other
live operation. A, B, C, and the descriptor span are validated with
checked-add arithmetic before mutation.

Each record is exactly 16 B and is value-copied:

```text
uint64_t aLinePaddr;          // 64-B aligned translated A line
uint64_t meta;
  meta[13:0]   destination;  // 0..16383
  meta[16:14]  sourceWord;   // 0..7 within the FP64 A line
  meta[17]     valid;        // exactly 1 in an archived descriptor
  meta[63:18]  reserved;     // exactly 0, checked on reload
```

The run number is implicit in backing position, not duplicated in the record.
The source index is exactly reconstructible as
`(aLinePaddr - translated_A_base_line)/8 + sourceWord` only when physical A is
contiguous, so correctness never relies on that reconstruction. At record
creation the controller instead proves that `A + 8*B[i]` is within the A
virtual span, records the independently translated aligned physical line, and
retains destination `i` and word offset. Translation is performed line by line;
physical contiguity across a 4-KiB page boundary is not assumed.

All runs use one canonical total key derived from the current `map_addr()`
result:

```text
(channel, rank, bank-group, bank, row,
 aLinePaddr, sourceWord, destination)
```

The replay's archived 11-bit row proxy is not stored or treated as the live
DRAM identity. Rank happened to be fixed in that replay; gem5 uses the complete
configured mapping. The physical line and final destination tie breakers make
the order total. A configuration/mapping change while an operation is live is
forbidden.

The descriptor image is exact:

```text
descriptorBase                       64-B aligned
runBase(r) = descriptorBase + r*65536, r in 0..3
line(r,l)  = runBase(r) + l*64,       l in 0..1023
record(r,j)= runBase(r) + j*16,       j in 0..4095
```

Thus each 64-B spill line holds four descriptors, each run is 64 KiB / 1,024
lines, and all four runs occupy exactly 262,144 B / 4,096 lines. No request
crosses a 4-KiB page when the base is 64-B aligned, but every line is translated
independently so noncontiguous physical pages are legal.

## Macro timeline

This is an ordering contract, not a latency prediction.

| Time | Required transition |
| --- | --- |
| T0 admission | Validate the exact FP64/dense form and all spans; acquire one indirect unit, the one shared stream unit, one destination-combiner context, the descriptor region, generation `g`, and prove nonwrapping serial headroom for the operation's worst case. Generation zero is never live. No state changes on failure. |
| T1 run 0 B scan | Read the 256 64-B lines covering `B[0..4095]` exactly once. Each exact B response produces its 16 descriptors in destination order in the fixed 4K active array. A B request is not complete at cache-port acceptance. |
| T2 run 0 sort | Run fixed in-place binary heap sort on all 4,096 records. The only persistent array is the active array; one 16-B swap register is charged. Every comparison and swap advances the timed local-sort FSM. |
| T3 run 0 spill | For line 0 through 1,023, copy four sorted records into run buffer 0, issue one coherent 64-B `WriteReq`, and do not reuse the buffer or advance the line until its exact `WriteResp`. |
| T4 run 0 barrier | After exactly 1,024 distinct matching write completions, assert no run-0 transfer is live and mark run 0 immutable. Only now may the active array be cleared/reused. |
| T5--T16 runs 1--3 | Repeat T1--T4 over `B[4096..8191]`, `B[8192..12287]`, and `B[12288..16383]`. Each is a disjoint 4K scan, in-place sort, 1,024-write/ACK spill, and exact per-run barrier. Total B payload is 65,536 B, not four scans of all 16K. |
| T17 merge prime | Issue at most one tagged 64-B `ReadReq` for line 0 of each immutable run. Responses may reorder; a run becomes merge-eligible only after its own exact response installs four validated records in its dedicated line buffer/head. |
| T18 four-way merge | When every nonempty run has a valid head, select the least canonical key. After four records from a run line are consumed, invalidate that buffer, request its next line, and stall global emission until all nonempty runs again have a head. This prevents a temporarily absent head from being skipped. |
| T19 A-line coalescing | For the first descriptor of a new `aLinePaddr`, reserve the one fixed A-response slot and issue exactly one tagged A `ReadReq`. Do not advance to another A line until the response arrives and every consecutive descriptor with that physical line has been accepted by the C combiner. An equal-line group may span all 16K records; it is streamed, never collected in a host vector. |
| T20 destination retirement | For each descriptor, select `sourceWord` from the retained 64-B A response and insert it at `C[destination]`. If the finite combiner cannot accept it, preserve the current head/A response and drain C owners; do not consume the descriptor. Full or partial C writes are response-bearing `WriteReq`s with unique identity. |
| T21 page publication | A C page's ready bit changes only after all 4,096 destinations in that page were generated, issued to owners, and covered by matching destination `WriteResp`s. Send acceptance, A response, merge emission, or a full combiner line is not publication. |
| T22 completion | Complete the high-level instruction only after all four run cursors equal their counts, exactly 16,384 descriptors were emitted, all run/A/B response slots are free, the C combiner is empty, every C write is ACKed, all four pages are published, and the stream unit/descriptor region can be released. |

Exact semantic traffic for one full operation is 1,024 B-line reads, 4,096
descriptor writes plus 4,096 matching responses, 4,096 descriptor reads, at
most 16,384 coalesced A-line reads, and 131,072 useful C bytes. Cache retries,
coherence messages, write allocation, evictions, and memory fills are additional
measured traffic; they are never inferred away.

## Finite state machine

```text
Idle -> Admit -> ScanB -> HeapBuild -> HeapExtract -> SpillLine -> RunBarrier
                    ^                                      |
                    `-------- next run (0..3) <------------'

RunBarrier(run 3) -> PrimeHeads -> NeedHead -> NeedA -> DrainEqualLine
                                        ^          |             |
                                        |          v             v
                                        +------ MergePick <- DrainC
                                                             |
                                              FinalCDrain <-+'
                                                   |
                                             Publish/Complete -> Idle
```

Persistent phase rules:

- `ScanB` owns exactly one B feeder line and appends only into
  `active[0..runCount)`. It cannot observe a future B word.
- `HeapBuild/HeapExtract` own the active array exclusively. Sort state is one
  root/child/limit machine; there is no library sort or second sorted image.
- `SpillLine` has at most one live write globally. `RunBarrier` is satisfied by
  terminal responses, never sends.
- `PrimeHeads/NeedHead` have at most one read per run buffer. Responses may
  reorder, but merge cannot pass an unavailable nonempty run.
- `NeedA/DrainEqualLine` have exactly one current A line and at most one A
  request. `DrainC` may run while that response is retained, avoiding a
  combiner/A circular wait.
- `FinalCDrain` stops new A and run reads but continues C write retries and
  response retirement. Completion has no outstanding credit or sender state.

## Persistent-state ledger and accounting verdict

### Reconciled mechanism lower bound

The input's exact `66,013 B` run-state arithmetic is:

| Run mechanism state | Bytes | Contract |
| --- | ---: | --- |
| Active record array | 65,536 | `4096 * 16 B`; only on-chip descriptor array. |
| Four coherent line buffers | 256 | One per run; reused for sequential spill and merge-head reload. |
| Four packed buffer tags/state | 103 | Each holds translated backing line, 64-bit generation, 64-bit serial, 2-bit run, and finite state. The run-relative line is derived from the cursor and expected address, avoiding the published insufficient 9-bit field for 1,024 lines. |
| Four merge heads | 64 | One copied 16-B value per run; invalid if its line buffer is not installed. |
| Four cursors + four counts | 13 | Eight 13-bit values, including the terminal value 4096. |
| Global generation, next serial, emitted count, phase | 19 | `64 + 64 + 15 + 3 = 146` bits. |
| Heap-sort indices, swap register, phase | 22 | Published 170-bit fixed sort state. |
| **Run state** | **66,013** | Bit-packed architectural lower bound. |
| Independent invalidator state | 4,096 | Not removed with Row/Offset replacement. |
| **Reorder state** | **70,109** | `66,013 + 4,096`. |

The 9-bit line-index field described in the earlier table cannot directly name
0..1023. This design does not enlarge it to 10 bits; it removes the redundant
field. A tag already carries the exact translated 64-B backing line, and the
buffer's 13-bit cursor/run deterministically derives the expected virtual line
and run-relative index. A response must match both. If an implementation keeps
an explicit line index instead, the run state grows by at least one byte after
packing and the old total is invalid.

The comparable lower-bound total remains:

| Boundary | Bytes |
| --- | ---: |
| Visible 4K physical SPD payload | 524,288 |
| Common non-reorder lower bound from the input model | 62,162 |
| Sorted-run reorder/invalidator | 70,109 |
| **Candidate on-chip lower bound** | **656,559** |
| Private coherent descriptor image, off chip | **262,144** |

The descriptor image is four immutable 64-KiB runs. It is external metadata,
not saved metadata, SPD capacity, or guaranteed LLC residency.

### State omitted or host-represented by the Python replay

| Omitted/host state | Resolution in this design |
| --- | --- |
| Four Python run lists and `heapq.merge` random access | Rejected. Replace with one 4K array, the coherent image, four 64-B buffers, and four heads only. |
| Python `list.sort` work/scratch | Rejected. Fixed in-place heap sort and its 22-B control/swap state are mandatory and timed. |
| Replay calls its aggregate spill ACK loop before constructing/sorting the Python runs | Rejected as a causal timeline. The gem5 FSM sorts one run, writes exactly its 1,024 lines, and crosses that run's ACK barrier before scanning the next run. |
| Aliased/mutable/unbounded `TransferTag` values | Rejected. Tags are value-copied fixed-width fields; generation/serial are nonzero `uint64_t`; equality is field-by-field with strongly typed direction/state; reserved bits must be zero. |
| B request/MMU/page progress | Use a fixed one-line B feeder and one translation callback/identity record. It belongs to the common ledger but must receive an explicit target `sizeof` charge. No B map/vector may remain in the promoted slice. |
| Packet/sender-state ownership for four merge reads, spill, A, and C | Conceptually use the repaired response-wrapper disposition and a fixed pool after independent acceptance. The final wrapper ABI/size is not accepted as of this design, so its delta over the packed 103-B buffer-tag allowance is unresolved. |
| A response and destination combiner | Reuse the common finite capacities/64-B payload algorithms only after replacing new-path vectors/maps/sets with fixed arrays. The input's 62,162-B common number does not prove current C++ object size. |
| Completion bitmap in `CoverageProof` | Evidence-only in host tests. Hardware exact-once follows disjoint destination ranges at generation, immutable run bounds, monotonic per-run cursors, and exactly 16,384 successful emissions. A test-only 2,048-B bitmap checks the invariant but is not runtime policy state. |
| MMU/TLB faults and noncontiguous pages | Translate every B, descriptor, A, and C transfer line; store the one live translation identity and fail the operation on a fault. No contiguous-physical oracle. |
| Drain/checkpoint state | The FSM phase, fixed records, nonwrapping allocators, and quiescence assertions are persistent. Checkpoint is quiescent-only in the first slice; a live operation refuses drain completion. |
| Metrics/high-water objects | Simulator statistics, not hardware policy SRAM. They still require named gem5 stats and may not be used as hidden functional state. |

**Accounting verdict:** the mechanism layout is internally reconciled at
70,109 B, but the full design remains rejected as a `656,559 B`
implementation until the response-wrapper size and all common fixed arrays have
an explicit target-ABI ledger. At minimum, replacing the four 103-B packed
buffer tags with four 48-B sender states from the pending logical-response
concept would grow run state by 89 B unless the sender state is proven to alias
the already charged fields safely. That alias proof does not exist today.

The accepted hidden-SPD payload is an independent row:

| Optional simultaneously enabled substrate | Bytes |
| --- | ---: |
| Two private FP64 4K slots per MAA, four MAAs | 262,144 |
| Candidate lower bound plus hidden payload | 918,703 |

Controller metadata, hidden-lane element tracking, allocator overhead, and
packet payload objects are additional simulator costs. The first sorted-runs
slice keeps the hidden substrate disabled/unmerged so it cannot claim the
smaller total while allocating both.

## Response identity and ownership

One operation per MAA uses its nonzero 64-bit generation as its operation ID.
Before admission, checked arithmetic proves that the global serial allocator
has headroom for the bounded worst case without wrapping: 1,024 B reads + 4,096 spill
writes + 4,096 run reads + 16,384 A reads + 16,384 destination writes =
41,984 response-bearing actions. Actual A/C counts may be lower, but serials
are allocated monotonically and never reused. Exhaustion rejects admission; it
does not wrap or wait for an old generation to disappear.

Every logical request has a value identity:

```text
{ generation:u64, serial:u64, action:enum,
  maa:u16, run:u8, expectedVLine:u64, expectedPLine:u64,
  destinationPage:u8, command:enum }
```

Fields irrelevant to an action are canonical zero, not wildcards. Identity is
stored in a fixed owner slot before the request becomes visible to a port. The
packet sender state points to that exact live slot and carries a copied tag;
address maps may arbitrate but never establish identity.

| Action | Unique owner and terminal transition |
| --- | --- |
| B read | The one B-feeder slot owns `{g,serial,BRead,B virtual/physical line}` until exact `ReadResp`; only that response may append its 16 indices. |
| Spill write | Buffer `run` owns `{g,serial,SpillWrite,run,line}` and its 64 B until exact `WriteResp`; acceptance/retry does not advance the cursor. Exactly 1,024 ACKs close that run's barrier. |
| Run-head read | Buffer `run` owns `{g,serial,RunRead,run,line}` until exact `ReadResp`; response data installs four records only after reserved-bit/run-range/key validation. |
| A read | The single A slot owns `{g,serial,ARead,aLinePaddr}` until exact `ReadResp`; it remains owned while all equal-line descriptors drain. A later identical line in the same canonical group reuses the payload, not another request. |
| Destination write | A unique C-line owner owns `{g,serial,CWrite,C line,page,mask}` and payload until exact `WriteResp`; same-address later writes receive a new serial and cannot be mistaken for the old response. |

Conceptually the repaired wrapper returns one of `Retired`, `DroppedExtra`, or
`FatalOwnedCorruption` to the cache/memory port. A stale/duplicate/forged packet
that owns no current slot is consumed once, settles only its transport credit,
increments a diagnostic, and mutates no FSM/owner. A matching current response
retires exactly one owner and is then consumed once. A response whose sender
pointer claims a current owner but whose copied tag/command/address disagrees
is internal corruption and fails closed; returning `false` forever is not a
liveness strategy. The exact names may follow the accepted repair, but these
ownership semantics are mandatory.

## Timing and bandwidth charges

No locality or cache-residency event is free.

| Component | Exact charge and required stats |
| --- | --- |
| B | Four disjoint 4K scans, one semantic pass: 16,384 words, 65,536 B, 1,024 line requests/responses. Count translation, cache lookup, retries, LLC hits/misses, memory fills, and response latency. |
| Descriptor construction | One bounds check, A-address checked add, word extraction, line translation, and mapping operation per B word. Charge translation/mapping time; do not precompute a 16K host vector. |
| Local sort | One fixed binary heap sort per run. One comparator and one swap datapath in the first slice; increment actual comparison/swap counters and schedule their configured fixed latencies. No instantaneous `std::sort`. |
| Spill | Exactly 4,096 64-B coherent writes and 4,096 matching responses, 262,144 useful bytes. Count port wait, cache backpressure, write allocation/coherence, LLC/memory traffic, and run-barrier cycles. |
| Merge reload | Exactly 4,096 64-B reads, 262,144 useful bytes. Count head-empty stalls, port wait, LLC hits/misses, memory fills, and response time. Assume all 4,096 may miss LLC. |
| Merge comparisons | A deterministic four-head selection (at most three key comparisons per emitted descriptor) plus head-refill stalls. Count actual comparisons, emitted descriptors, and unavailable-head cycles. |
| A | One request per distinct consecutive physical A line in canonical merge order; between 1 and 16,384. Count cache/memory placement, LLC misses, response latency, equal-line group size, and response-slot/combiner stalls. |
| Shared stream/ports | Sorted-run B/spill/reload/C actions arbitrate with the one existing stream unit and ordinary cache/memory traffic. Count acquisition wait and per-action port-blocked/retry cycles; do not overlap actions merely because the replay did. |
| C | Exactly 131,072 useful bytes become architecturally visible. Count full-line, masked-line, and word writes separately, every response, owner conflicts, and final-drain cycles. Fair arms use identical combiner/write policy. |
| Completion | Instruction cycles end only at the final C ACK and empty-ledger condition. Report B, sort, spill, barrier, reload, merge, A-wait, C-wait, and final-drain buckets whose sum equals total operation cycles. |

New stats should include `sortedRunBWords`, `sortedRunSortComparisons`,
`sortedRunSortSwaps`, `sortedRunSpillLines/ACKs`,
`sortedRunReloadLines/Responses`, `sortedRunHeadStallCycles`,
`sortedRunMergeComparisons`, `sortedRunDescriptorsEmitted`,
`sortedRunALineRequests`, `sortedRunEqualLineRecords`,
`sortedRunCWriteACKs`, per-phase cycles, every bad-response class, and all
capacity high waters. Existing port and DRAM statistics remain authoritative
for placement. Proxy row transitions are not DRAM activate/precharge commands.

## Deadlock and liveness rules

1. **Finite admission:** one sorted operation per MAA, one active 4K array,
   four run buffers, one A slot, and configured finite C owners. Full means
   retry without mutation; no vector/map growth is an escape path.
2. **Resource order:** acquire operation/descriptor region -> shared stream
   action -> response owner -> port credit. Release in reverse on exact terminal
   response. Never hold a port credit while waiting to allocate its owner.
3. **Progress priority:** retire responses first; then retry already-issued C
   writes; drain C owners; refill a missing run head; issue the current A read;
   then issue B/spill work. This permits C to drain while an A payload is held
   and prevents a full combiner from blocking the response that frees it.
4. **Response reordering:** independent run-head buffers may complete in any
   order. Merge waits for every nonempty head; it never chooses around a missing
   head. Spill is deliberately one line at a time, so its per-run barrier cannot
   be miscounted.
5. **Cache/port backpressure:** a refused `sendTimingReq` leaves the same packet,
   owner, serial, and data live for retry. No second packet is created. Action
   acceptance and packet acceptance are separate from completion.
6. **Stale/duplicate/forged responses:** consume transport ownership exactly
   once, increment the precise class, and do not free or advance any current
   owner. A bad extra response cannot wait forever for a condition that will
   never become true.
7. **Serial/generation exhaustion:** all allocators are nonzero `uint64_t` and
   use checked addition. Admission fails closed if the 41,984-action reserve or
   next generation would overflow. Restore never decrements an allocator.
8. **Skew:** one A line may own all 16K records. Hold one A payload, stream one
   descriptor at a time, and allow C drains between descriptors. No equal-line
   list or group-size allocation exists.
9. **Page boundaries:** every 64-B B/descriptor/A/C transaction is separately
   translated. Backing spans use checked virtual arithmetic; response identity
   includes both expected virtual and translated physical lines. Aliased private
   descriptor backing is rejected by the API contract.
10. **One shared stream unit:** sorted actions do not wait while owning a normal
    stream instruction and vice versa. The MAA scheduler advertises one action,
    acquires the idle unit atomically, and retries through the existing issue
    event. C drain has priority within the sorted operation.
11. **Drain/checkpoint:** drain blocks admission and waits for every owner,
    response credit, combiner entry, and action to clear. First implementation
    checkpoints only when `Idle`; a live checkpoint is a clean refusal, never a
    best-effort serialization of packet pointers.
12. **Cancellation/fault:** before any spill, an MMU fault may abort after
    releasing owned finite state. After the first acknowledged spill, the first
    slice drains/fails the simulation rather than silently abandoning coherent
    metadata or publishing partial C.

## Correctness invariants

- Run `r` is generated only from destinations `[4096r,4096(r+1))`; its count is
  exactly 4096 before sorting and spill.
- The active array is a permutation of those 4,096 value descriptors before
  and after heap sort; the final keys are nondecreasing.
- A run becomes immutable only after 1,024 matching `WriteResp`s. No run read
  precedes all four run barriers.
- Reload validates `valid=1`, reserved zero, destination in the run's range,
  word `<8`, aligned A line within the admitted translated A mapping, and
  nondecreasing per-run keys.
- Four-way merge never emits a key smaller than the prior key and increments
  exactly one run cursor per accepted C-combiner insertion.
- A physical line is requested once per maximal equal-line merge group. The
  retained A response tag equals every descriptor served from it.
- Each destination is generated once by disjoint scan position, carried in an
  immutable descriptor, accepted once by a duplicate-detecting C owner, and
  covered by exactly one completed C write. Host tests additionally use an
  independent 16,384-bit observer.
- A page is published iff generated=issued=ACKed=4096 and no live C owner can
  modify it. The instruction completes iff all four pages are published and
  every finite owner is free.
- Resetting statistics changes no functional state. Retry changes no identity,
  payload, cursor, count, or ordering decision.

## Source insertion map and staged implementation

| File/class/function | Minimal future change |
| --- | --- |
| `src/mem/MAA/SortedRunController.hh` (new) | Header-only/pure C++ fixed FSM, record ABI, heap sorter, four buffer/head records, strong identities, checked allocators, and host-test hooks. No gem5 packet or STL dynamic container in the functional core. |
| `tests/maa/sorted_run_controller_test.cc` (new) | Exhaustive small geometries plus 4K boundary, adversarial identities, skew, retries, reordered responses, serial exhaustion, and exact byte assertions before any gem5 wiring. |
| `IF.hh` / `IF.cc` | Add `reorderBackingAddr` and an internal reorder-policy field to the existing virtual-index instruction representation; do not renumber opcodes or reinterpret logical-SPD high bytes. Preserve current logical operand initialization at [IF.hh:159](../../src/mem/MAA/IF.hh#L159). |
| `CpuSidePort.cc:218-480` | Decode only a separately accepted config-selected sorted form. Normal opcode-13 still completes at word 4; sorted mode waits for word 5, validates the complete descriptor span before dispatch, and rejects word 5 for every other form. |
| `MAA.hh:394-435`, `MAA.py:14-143`, `configs/common/MAAConfig.py:10-205`, `configs/common/Options.py:217-390` | Add a disabled-by-default `sorted_runs` policy and fixed capacities/latencies. Freeze one comparator, one swap path, one spill in flight, four reload buffers, and one A request for the vertical slice. Do not introduce an unlimited value. |
| `MAA.cc:149-171`, `MAA.cc:487-587`, `MAA.cc:912-1000` | Allocate one controller per indirect unit, arbitrate the existing stream unit, and add `tryIssueSortedRunAction` beside—not inside—the transparent logical-SPD scheduler. An action is owned by exactly one scheduler. |
| `IndirectAccess.hh:28-186`, `IndirectAccess.cc:589-730` | Add a disjoint sorted-run mode that feeds one B line into the fixed controller. The existing map-backed direct-index feeder is useful semantic reference but cannot be the promoted finite implementation. |
| `IndirectAccess.cc:917-990` | Reuse checked A address calculation, translation, `map_addr`, and row-key derivation. Insert a 16-B record instead of Row/Offset state only in sorted mode. |
| `Tables.*` | No sorted-mode allocation or lookup. Row/Offset cannot be reused for the archived four runs without either retaining 16K state or adding the replaced 62,592 B back. Keep legacy/direct arms unchanged. |
| `IndirectAccess.cc:2039-2142` | Reuse A-response latency accounting and the fixed 64-B payload concept, but add a sorted response callback keyed by the strong tag. Existing `recvData` requires Row/Offset reservations and is therefore not directly reusable. |
| `IndirectAccess.cc:2530-3045` | Reuse page-count equations, duplicate-word check, combiner insertion/drain, and retirement-write semantics only after fixed-array owners replace maps/sets/vectors for this mode. Keep all current paths unchanged. |
| `StreamAccess.hh/.cc` | Add bounded descriptor `ReadReq`/`WriteReq` actions on the one stream unit. Do not call normal store `recvData` or `writePacketSent`; spill waits for `WriteResp`. |
| `MAA.hh:799-853`, `Port.cc:30-725`, `CacheSidePort.cc`, `MemSidePort.cc` | Integrate only the independently accepted response-wrapper contract. Logical actions prohibit address coalescing, carry sender-state identity, preserve retry ownership, and settle packet/credit exactly once. |
| `SPD.*` / `LogicalSPDHiddenPayload.hh` from accepted `3c7cb3a` | No first-slice use. If later combined, charge the additional 262,144-B hidden payload and wire only controller-owned internal access; public tile IDs stay rejected. |
| `benchmarks/API/MAA_gem5.hpp:75-124` | No first host-controller-stage change. Later name instruction word 5 and add a sorted helper only after ABI review; it accepts a caller-provided aligned 256-KiB private descriptor span and never allocates or exposes a 16K host descriptor vector. |
| `MAA.hh` statistics / `MAA.cc` registration | Add the named counters and mutually exclusive phase-cycle buckets. Do not derive performance from the Python proxy. |

Minimal order:

1. Pure fixed controller/record/identity host tests and exact `sizeof` ledger.
2. Response-wrapper rebase plus pure packet-owner executor tests.
3. B scan -> heap sort -> one run spill/ACK -> reload loopback, with A/C
   disabled; this is the narrowest response-timed vertical slice.
4. Four barriers + four-way merge into an observer sink, still without A/C.
5. One A slot and existing combiner algorithm through fixed owner arrays;
   publish C only on exact write responses.
6. Only then expose a disabled-by-default config/API selector and build gem5.

Row/Offset reuse is legal for address calculation, mapping, comparator
definition, and the direct control arms. It is impossible for sorted response
placement without defeating the storage replacement: current `recvData`
requires a Row/Offset reservation keyed by the returning A line. The narrow
slice therefore reuses A payload/combiner algorithms but owns destination
records in the run stream itself.

## Verification matrix and promotion gates

No gem5 or workload stage is authorized by this document.

| Stage | Test | Pass gate | Immediate rejection |
| ---: | --- | --- | --- |
| 0 | Pure host state machine | Small N/K exhaustive permutations; 4K/1,024-line boundaries; heap result equals an independent canonical sort; skewed all-one-line group; no allocation after construction. | Host vector/list of 16K descriptors, `std::sort`, missing phase timing, cursor overflow, or byte assertion drift. |
| 1 | Identity/ownership host tests | Value-copy tags; reordered good responses; stale, duplicate, forged bool/enums, mutated copies, wrong command/address/run/generation/serial; exhaustion at `UINT64_MAX`; exactly one disposition/credit settlement. | Acceptance completes an action, bad response advances state, alias mutation changes owner, wrap/reuse, or an unretirable packet. |
| 2 | Source contracts | Only new fixed arrays; sorted mode no Row/Offset allocation; no logical coalescing; one stream unit; response-bearing spill/C writes; hidden IDs inaccessible; exact target `sizeof` ledger including wrapper pool. | Dynamic map/set/vector in new functional state, simultaneous hidden payload omitted from bytes, or claimed total above an unamended budget. |
| 3 | Event-trace unit integration | Exactly four `scan_begin/sort_done/spill_acked(1024)` barriers, then 4,096 exact reload responses; nondecreasing merge keys; 16,384 emissions; A group/request equality; page ready only after C ACKs; phase cycles sum to total. | Run read before barrier, missing head skipped, free LLC residency, early page/instruction completion, or unmatched live owner. |
| 4 | One synthetic FP64 microbenchmark, **only after explicit approval** | Scalar byte/guard oracle, B read once, exact descriptor traffic, bounded high waters, delayed/reordered/retried responses, drain clean. | Any data/guard mismatch, hang, growth, stale mutation, or traffic/cycle bucket inconsistency. |
| 5 | XRAGE and all 14 FLAG gathers, **only after separate explicit approval** | Frozen input identities; exact scalar oracle; predeclared structural and timing metrics; every arm complete/correct; no proxy relabeled as DRAM command. | One missing fixture, changed hash, aggregate hiding a per-source failure, or trace/replay acceptance inferred from the rejected model. |

### Fair comparison arms

Every report must spell out opcode, logical elements, physical SPD elements,
Offset capacity/epoch, B range, C path, combiner geometry, force-cache policy,
clock/memory/cache configuration, binary/config hashes, and exact completion
gate. Labels alone are forbidden.

| Arm | Exact meaning |
| --- | --- |
| `direct16` | One native opcode-14 direct-index gather of 16,384 elements, 16K physical SPD destination, 16K Offset capacity/epoch, followed by the matched C store/publication path. |
| `direct4` | Four native opcode-14 gathers over disjoint 4,096-element B/C ranges, 4K physical SPD and 4K Offset capacity/epoch for each call. No cross-call Row/Offset lifetime. |
| `bounded4-control` | One 16K logical current virtual-index operation, 4K physical SPD, explicitly 4K Offset capacity and drain epoch, one partition/B pass, same virtual response/combiner/C-write settings as sorted runs. |
| `current-hybrid` | Current opcode-13 virtual-index operation with 16K logical work and 4K physical SPD but the explicitly reported current Row/Offset capacity/epoch (often full 16K). This is not called `direct4`. |
| `sorted-runs` | Same opcode-13 A/B/C spans, 4K physical point, response/combiner/write settings, and cache/memory configuration as `bounded4-control`; only Row/Offset reorder is replaced by the controller and private descriptor span. |

End-to-end native versus virtual comparisons are invalid if C retirement paths
differ. Either normalize all arms through the same acknowledged backing writer
or restrict the claim to separately reported A request/order metrics. C
combiner contents must survive all four sorted runs; flushing at a run barrier
changes traffic and is a rejection.

Exact rejection gates for any promotion are:

- scalar output and guards are not exact, or any live destination is missing or
  duplicated;
- B semantic payload differs from one 65,536-B pass for sorted runs;
- descriptor payload differs from 262,144-B writes and 262,144-B reads, or
  transaction/terminal-response counts differ;
- an action completes at packet acceptance, a stale/duplicate mutates current
  state, a serial/generation wraps, or drain/checkpoint loses an owner;
- high water exceeds 4,096 active records, four run buffers/heads, one A slot,
  the declared C owners, or the declared port credits;
- merge order decreases, an unavailable head is skipped, or A requests differ
  from maximal equal-line groups;
- any source uses an uncharged 16K host container, oracle order, unlimited
  throughput, free LLC residency, or hidden-SPD alias;
- the complete target ledger exceeds the predeclared budget without an explicit
  budget revision;
- for later structural promotion, sorted runs fails to strictly reduce both A
  requests and the predeclared absolute row-transition metric versus the fair
  bounded4 control on even one approved source;
- for later timing promotion, correctness-complete matched results fail the
  separately predeclared per-source timing/DRAM-command gate. Structural proxy
  counts alone never establish speedup.

## Recommendation and handoff

Proceed with sorted runs before range spool **only as a host-tested,
response-safe controller vertical slice**. It has the stronger mechanism:
one B pass, one descriptor write, one descriptor read, deterministic global
four-run order, and exact cross-run A-line coalescing. Range spool's simpler
selection logic does not justify four complete descriptor reloads unless the
sort/merge controller or its revised byte ledger fails.

Do not yet merge production code or run gem5. First independently accept the
logical response ownership repair, implement the fixed controller and wrapper
tests, and replace the input model's common-state placeholder with a complete
target-ABI ledger. If that ledger cannot fit the agreed budget—or if fixed
sort/merge timing and descriptor traffic erase the mechanism under the later
approved microbenchmark—reject sorted runs and reconsider range spool. The
reproducible but contract-rejected XRAGE/FLAG counts do not change that gate.
