# SoA/JIT RMW: eight-context and value-line coalescer design

Date: 2026-08-14

Source audit baseline: `c579c6b8`

Scope: design only; no shared MAA source is changed here.

## Decision

Extend the correctness-first logical-16K/physical-4K SoA/JIT RMW from one to
eight A-line contexts. Put a four-entry, fully associative value-line
cache/coalescer in the indirect unit. Each entry has an eight-bit waiter mask,
so at most the eight A contexts can wait for one fill. The cache sends at most
one `ReadReq` for a `(generation, value-line paddr)` and serves at most one
value word per cycle. A context still applies its own Offset chain strictly
serially and retains its modified 64-byte A line until the exact `WriteResp`.

Do not extend `MAA::OutstandingPacket` to represent these waiters. The current
global port is keyed only by `paddr`; it may merge reads from different
`(FuncUnitType, maaID)` pairs, but deliberately rejects a second read from the
same pair. Local coalescing is therefore the only unambiguous place to merge
same-value-line requests from eight contexts.

## Current contracts that constrain the design

- `MAA::sendPacket()` indexes `my_outstanding_pkt_map` by physical address,
  checks equal commands, rejects duplicate writes, and rejects a repeated
  `(INDIRECT, maaID)` read owner. It serializes retirement traffic and its
  deferred queue by exact address (`src/mem/MAA/Port.cc:30-175`).
- A read becomes `sent` only after `sendTimingReq()` succeeds. Cache or memory
  refusal leaves the same packet owned by the port queue; `recvReqRetry()`
  unblocks that port and reschedules it (`Port.cc:396-575`,
  `CacheSidePort.cc:95-168`, `MemSidePort.cc:80-87`). A context must not create
  a replacement packet on retry.
- Responses carry no local context tag. `MAA::recvTimingResp()` authenticates
  only command class, `paddr`, sent state, and cache route, erases the unique
  address owner, then calls `recvData(paddr, ...)` or
  `retirementWriteComplete(paddr, ...)` (`Port.cc:705-735`).
- A Row entry is unique for an unsent `(grow, A-line)` and appends Offset
  records through `last_itr`; the linked list therefore records fill/logical
  order (`Tables.cc:147-194`, `Tables.cc:348-376`, `Tables.cc:559-609`). A
  committed virtual claim invalidates the Row entry but leaves its Offset
  chain live until consumption (`Tables.cc:406-429`).
- The existing one-context branch owns one at-most-128-byte context with states
  `Free`, `AwaitARead`, `AwaitValueRead`, and `AwaitAWriteResp`. It performs one
  `ReadExReq` for A, one `ReadReq` per value, consumes one Offset only after
  that value arrives, and frees the context only on `WriteResp`.
- Existing finite patterns are worth retaining: descriptor-spool reads have
  fixed slots and reject duplicate paddr; descriptor-spool and four-run writes
  have fixed address scoreboards and ACK closure; virtual retirement also
  forbids reuse of a live write line (`IndirectAccess.hh:379-445`,
  `BoundedDescriptorSpool.hh:327-400`, `BoundedFourRunMerge.hh:243-304`,
  `IndirectAccess.cc:6310-6388`).
- The normal cache side already provides the needed coherent request path and
  finite response capacity. It counts a response-bearing request only after
  acceptance and distinguishes downstream retry from local response-capacity
  refusal (`CacheSidePort.cc:102-180`). No new cache-side port or payload queue
  is required.

## Exact ownership

All keys include the indirect-unit identity implicitly; the implementation
must include it in assertions and trace records.

| Resource | Sole ownership key | Release point |
|---|---|---|
| A context | `(instruction_generation, context_id)` | Matching A `WriteResp` |
| Mutable A line | `(generation, context_id, a_line_paddr)` | Matching A `WriteResp`; no second context may have the same `a_line_paddr` |
| Offset cursor | `(generation, context_id, next_offset)` | One successful value delivery consumes exactly that entry; never on issue or retry |
| Value entry | `(generation, value_line_paddr)` | Ready, waiter-free entry chosen by bounded round-robin replacement, or terminal reset |
| Value fill | `(generation, value_entry_id, value_line_paddr)` | One matching read response; a Filling entry cannot be replaced |
| Value waiter | `(generation, value_entry_id, context_id, logical_itr, value_word)` | Its one word is delivered and its mask bit is cleared |
| A write | `(generation, context_id, a_line_paddr, AwaitAWriteResp)` | Matching response routed through `retirementWriteComplete()` |
| Global MAA packet | Existing `paddr` plus vector of distinct `(FuncUnitType, maaID)` owners | Existing port response or non-response send completion |

Because the shared response API exposes only `paddr`, correctness depends on a
bijection: within one indirect unit there is at most one live read role for a
given paddr. A value fill is unique by construction. Full Fill must have
drained index and predicate reads before any A context is admitted. The
guarded ABI must require A, value, index, and optional predicate regions to be
non-overlapping immutable/mutable roles; encountering a cross-role physical
alias is a fatal contract error before issuing that conflicting request. Do
not let a value-cache hit hide an A/value alias.

## State machines and bounded scheduling

### A context: eight fixed slots

`Free -> AwaitARead -> NeedValue -> WaitValue -> NeedValue ... ->
AwaitAWriteResp -> Free`.

1. A round-robin allocator claims one complete Row entry only into a `Free`
   context. It records the A paddr, generation, Offset head, and chain count,
   then enqueues one normal cache-forced `ReadExReq`.
2. The matching A response copies exactly 64 bytes into that context and moves
   it to `NeedValue`. It does not consume an Offset.
3. `NeedValue` peeks, but does not consume, `nextOffset`; derives
   `(logical_itr, A word, value-line paddr, value word)`; and performs the cache
   lookup below. It then becomes `WaitValue`.
4. One round-robin waiter is served per cycle. Delivery validates all cached
   identity fields, performs ADD/MIN/MAX on the context's A line, then consumes
   exactly the peeked Offset. A nonterminal chain returns to `NeedValue`; a
   terminal chain enqueues one response-bearing 64-byte `WriteReq` and enters
   `AwaitAWriteResp`.
5. Only a unique matching A `WriteResp` clears the context. Request acceptance,
   cache retry, and value-chain completion are not context-release events.

Issue at most one new A claim, one value-cache lookup/allocation, and one value
delivery per indirect unit per cycle. Use independent round-robin context
cursors so a blocked miss or long alias chain cannot starve another context.
The fixed hit/delivery latency is one MAA cycle; misses retain ordinary timed
cache/network latency. A final value delivery may enqueue its A write in that
cycle, subject to the existing port scheduler.

### Four value-line entries

Each entry is `Invalid`, `Filling`, or `Ready`.

- Match `Ready`: set the requester's waiter bit and count a ready hit.
- Match `Filling`: set the bit and count a coalesced waiter; issue no packet.
- Miss with an Invalid entry: install `(generation, paddr)`, set the waiter,
  enter `Filling`, and enqueue exactly one cache-forced `ReadReq`.
- Miss without Invalid: replace only a `Ready` entry whose waiter mask is zero,
  using a two-bit round-robin victim cursor. If every entry is Filling or has
  waiters, leave the context in `NeedValue` and backpressure locally.
- Fill response: require exactly one matching Filling entry, copy 64 bytes,
  enter `Ready`, and wake the value-delivery arbiter. Unknown, duplicate, or
  wrong-generation responses fail closed.
- A context has at most one waiter bit set at a time. An entry has at most eight
  waiters and needs no growing vector or overflow path. Ready data may remain
  until bounded replacement because the value region is immutable for the
  whole generation. Terminal reset requires no Filling entries and zero masks.

The cache does not retry downstream requests itself. Once a miss calls
`MAA::sendPacket()`, the existing outstanding packet and cache-side blocked
state retain the packet across `MAX_XBAR_PACKETS` or `CACHE_FAILED`. Local
progress resumes on a read response, value delivery, A `WriteResp`, or the next
scheduled one-cycle arbitration event. No polling event may run while all
progress is owned by a downstream response/retry.

## Alias order and correctness

The 16K Fill remains complete before Build; a capacity drain is still fatal.
For one A line, Row insertion appends Offsets in increasing logical fill order.
Exactly one context owns that whole chain and never has two value operations
in flight. Thus aliases to the same A word are applied in original logical
order, including FP32 non-associative cases. Eight contexts may interleave only
different A lines; those operations do not alias and may complete or write
back in any order.

Required invariants, asserted after every transition in debug builds and at
terminal closure:

1. Each non-Free context has the active nonzero generation; A paddrs are unique
   across contexts; `remaining` equals the still-live chain length.
2. `WaitValue` has exactly one waiter bit in exactly one entry, and the entry's
   `(generation,paddr)` matches the context. Other states have no waiter bit.
3. At most one Filling entry has a paddr, and no local A/index/predicate read
   owns that paddr. Therefore the unit never submits an ambiguous duplicate
   read to the address-keyed MAA port.
4. One value delivery consumes one Offset with the same `itr` and A word; chain
   consumption never occurs on lookup, fill issue, refusal, or fill response.
5. `AwaitAWriteResp` has no Offset or waiter and retains the sole modified A
   payload. A paddr cannot be reclaimed before its response.
6. `value_lookups == value_deliveries == aliases_applied == selected`; physical
   fill issues equal fill responses and may be smaller. A reads equal A
   responses equal A writes equal A write responses at terminal closure.
7. Completion additionally requires all rows claimed, Offset occupancy zero,
   all eight contexts Free, every value entry non-Filling with zero waiters,
   predicate/index state empty, selected plus rejected equal 16K, and the
   existing global outstanding count for the unit equal zero.
8. Reset, abort, drain, or checkpoint is illegal while any context, fill,
   waiter, A write, or global packet owner is live. Generation cannot wrap to
   zero or be reused while state from the prior generation exists.

## Fixed storage cost

Use fixed arrays only. The following is conservative `sizeof`-style charging,
matching existing DX100 accounting rather than an optimistic packed-bit SRAM
estimate.

| Structure | Fields | Per entry | Count | Bytes |
|---|---|---:|---:|---:|
| A context | 64-byte A payload; three 64-bit fields (`aPaddr`, `valuePaddr`, generation); three 32-bit fields (Offset, remaining, logical itr); two 16-bit words; 8-bit cache entry; 8-bit state; 64-bit alignment | 112 | 8 | 896 |
| Value entry | 64-byte payload; 64-bit paddr; 64-bit generation; 8-bit waiter mask; 8-bit state; 8-bit replacement age; alignment | 88 | 4 | 352 |
| Arbiters | 8-bit waiter/context cursor and 8-bit victim cursor | — | — | 2 |
| **Candidate fixed state** | excludes existing predicate line, Row/Offset metadata, port queues, and statistics | | | **1,250 B** |

The field sum before host padding is 106 bytes/context and 83 bytes/value
entry, so each A context remains below the 128-byte requirement. Relative to
the padded one-context/no-value-cache implementation, the fixed increment is
`7 * 112 + 4 * 88 + 2 = 1,138 B` per indirect unit. Only 256 bytes of that
increment are new value payload. Report both the fixed-state charge and the
unchanged full-16K Row/Offset allocation; do not call this an iso-area result.

## Statistics

Keep the existing selected/rejected, predicate, A read, alias, A write,
context-high-water, context-stall, generation, and terminal-completion stats.
Make the value distinction explicit:

- `IND_SoaJitValueLookups`, `ValueDeliveries` (semantic; each must equal
  selected), and `ValueDeliveryCycles`;
- `ValueLineFillIssues`, `ValueLineFillResponses`, `ValueLineFillBytes`;
- `ValueReadyHits`, `ValueFillMerges`, `ValuePacketsAvoided` where avoided is
  hits plus merges;
- `ValueEntryHighWater`, `ValueWaiterHighWater` (bounded by four and eight),
  `ValueNoVictimStallCycles`, and `ValueEvictions`;
- `ContextOccupancyCycles[0..8]`, `ContextFullStallCycles`, and per-state
  occupancy cycles;
- `PaddrRoleConflicts`, `DuplicateValueFillAttempts`, `UnknownValueResponses`,
  and `UnknownAWriteResponses`, all required to be zero in a valid run;
- existing cache-port read/write packets, refusals/retries, and A
  issue/response counters, reported beside the new local stats.

Do not continue describing `IND_SoaJitValueReadIssues` as both logical values
and physical packets. Either retire that name in favor of the two groups above
or define it as physical fills and add the semantic lookup/delivery pair.

## Validation and matched experiment

Before performance use, add a pure state-machine test with: eight contexts
waiting on one value line (one fill, eight deliveries); four concurrent fills
plus a fifth miss (bounded stall); ready hit and round-robin eviction; reordered
fill and A responses; cache request refusal and response-capacity refusal;
same-paddr cross-role rejection; duplicate/stale/unknown response rejection;
and context reuse only after `WriteResp`. Retain the two-generation 16K test's
duplicate indices, false poison predicates, and bit-exact FP32 order-sensitive
sequence.

Checkpoint `f02c66be17a1` from the independent backed-RMW slice is a useful
full-scope integer oracle: reuse its 16K permutation, predicate pattern, exact
hash check, timed publication boundary, and issue/ACK closure as an additional
cross-check. Do not import its 32-byte AoS records, 64-record/eight-line
response window, 64-entry write scoreboard, 4K diagnostic value epoch, or
non-promotable performance interpretation into this SoA design.

Expose `soa_jit_contexts={1,8}` as an admission-checked runtime parameter, but
instantiate the fixed maximum arrays above. Hold `soa_jit_value_cache_lines=4`
in both primary arms; the active prefix changes no storage, routing, cache,
row-table, or value-cache policy. From one common restored checkpoint run:

| Arm | Logical metadata | Physical SPD | Active A contexts | Value entries |
|---|---:|---:|---:|---:|
| C1 | 16K | 4K | 1 | 4 |
| C8 | 16K | 4K | 8 | 4 |

Use the identical binary, benchmark inputs, two instruction generations,
registered address ranges, cache/DRAM configuration, clocks, Row/Offset
geometry, and checkpoint. Change only `soa_jit_contexts`. A separate
one-context/no-cache run may bridge to the current implementation, but it is
diagnostic and must not be used to attribute the C1-versus-C8 speedup.

Both matched arms must have identical final byte hash and logical stats,
distinct nonzero generations, exact terminal closure, zero error stats, and
the same value-cache fill count; otherwise the timing comparison is invalid.
Report `simTicks`, SoA instruction begin-to-final-WriteResp ticks, context
occupancy/full-stall cycles, fill/hit/merge/eviction/no-victim counts, A
read/write response latencies, cache-port retry/capacity stalls, physical
packet counts, and all high waters. Quote `C1 simTicks / C8 simTicks` only after
those gates pass; the small cache's packet reduction is a separate measured
quantity, not inferred from logical lookups.

## Implementation handoff

The implementation belongs with the separate SoA/JIT branch. It should remain
local to `IndirectAccess.hh/.cc` plus bounded parameters/stats and focused
tests unless an authenticated response tag is deliberately added end to end.
Do not weaken or bypass `MAA::sendPacket()` address serialization, cache-side
retry ownership, Row/Offset ordering, response-bearing A writes, or terminal
outstanding-packet closure.
