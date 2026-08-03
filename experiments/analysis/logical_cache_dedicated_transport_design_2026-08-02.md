# Dedicated finite transport for the logical SPD cache

Date: 2026-08-02

Baseline: clean lead `9fcb18c4cabb782975c68b6a8f484364f8987637`

Status: architecture contract and executable safety model; no production gem5
integration, simulator run, or performance claim.

## Recommendation

**GO dedicated transport.** Do not continue incremental repair of the shared
native `Port.cc` response machinery for logical-cache traffic. The dedicated
option has one bounded owner table, one request FIFO, one retry owner, and one
response-credit ledger. It can be added beside the native path and can preserve
native behavior by construction. Repairing the shared path would still require
redesigning ownership, response order, cancellation, counters, and all native
unit callbacks at once.

This is a GO for a separately reviewed production prototype, not for immediate
integration or performance promotion. Before production work, a fresh reviewer
must approve this contract. The prototype must then pass pointer-lifetime,
retry, response-routing, drain, and native-regression gates listed below.

## Audited baseline facts

The recommendation is grounded in the clean baseline, not in the rejected
patch series.

- `CacheSidePort::recvTimingResp` unconditionally calls the shared
  `MAA::recvTimingResp`, decrements a shared outstanding count, and then deletes
  the packet ([CacheSidePort.cc:30](../../src/mem/MAA/CacheSidePort.cc#L30)).
  Its `recvReqRetry` also unblocks the shared cache path
  ([CacheSidePort.cc:84](../../src/mem/MAA/CacheSidePort.cc#L84)).
- `MAA::sendPacket` starts with `pkt->req->getPaddr()`, coalesces by address,
  records native unit-owner vectors, and may enter the native deferred map
  ([Port.cc:49](../../src/mem/MAA/Port.cc#L49)). The cache scheduler later
  mutates native outstanding counters and unit state
  ([Port.cc:499](../../src/mem/MAA/Port.cc#L499)).
- The response path accepts only a small native command set, dereferences
  `pkt->req` before proving ownership, finds an address-keyed shared record,
  erases it, and fans out through native owner vectors
  ([Port.cc:698](../../src/mem/MAA/Port.cc#L698)).
- Cache and retirement ports are the same `CacheSidePort` type and both call
  the same shared callback ([MAA.hh:228](../../src/mem/MAA/MAA.hh#L228)); they
  are separately constructed at [MAA.cc:188](../../src/mem/MAA/MAA.cc#L188)
  and exported by `getPort` at [MAA.cc:328](../../src/mem/MAA/MAA.cc#L328).
- gem5 provides the unavoidable timing APIs `RequestPort::sendTimingReq`,
  `recvReqRetry`, and `recvTimingResp`; its packet API permits stacked mutable
  `SenderState` objects ([packet.hh:459](../../src/mem/packet.hh#L459)). The
  proposed bridge uses the timing callbacks but not `QueuedRequestPort` or a
  sender wrapper as logical authority.
- The baseline logical controller already fixes at least two descriptors,
  four pages per descriptor, two physical slots, and finite miss/lease state
  ([LogicalSPDCacheController.hh:37](../../src/mem/MAA/LogicalSPDCacheController.hh#L37)).
  It already requires atomic reservation before overwrite
  ([LogicalSPDCacheController.hh:384](../../src/mem/MAA/LogicalSPDCacheController.hh#L384))
  and exact fill/writeback completion
  ([LogicalSPDCacheController.hh:615](../../src/mem/MAA/LogicalSPDCacheController.hh#L615)).
- The hidden layout is exactly two private slots, two FP32 lanes per FP64 slot,
  and 4,096 elements per lane
  ([LogicalSPDHiddenPayload.hh:30](../../src/mem/MAA/LogicalSPDHiddenPayload.hh#L30)).

## Boundary and non-goals

The transport handles only logical-cache full-page fills and acknowledged
dirty writebacks. It does not handle native indirect, stream, Row/Offset,
retirement, snoop, or unit-request-table traffic. It never calls:

- `MAA::sendPacket`, `sendOutstandingCachePacket`, or `MAA::recvTimingResp`;
- `my_outstanding_pkt_map`, `my_deferred_pkt_map`, any native outstanding
  counter, or any native owner vector;
- `IndirectAccessUnit::{recvData,retirementWriteComplete}`;
- Row/Offset claim, lookup, cleanup, or retirement methods.

The model does not model ALU arithmetic, cache latency, coherence protocol
details, area, energy, or performance. `bind_dirty_destination` is an explicit
validated boundary event for a compute-produced full-page payload; it is not an
external history or an arithmetic oracle.

## Mechanism

```text
 Logical cache controller (2 descriptors, 4 pages each, 2 private slots)
              | exact PageAction; one action at a time
              v
 +---------------- dedicated transport ----------------+
 | 512b issued + 512b ACK sets   action/generation ID  |
 | fixed 8-entry request FIFO -> one pending/retry owner|
 | fixed 8 transaction records -> 4 response credits   |
 +-----------------------+------------------------------+
                         | dedicated RequestPort only
                         | sendTimingReq / recvReqRetry
                         v
             existing cache-side xbar/cache API
                         |
                         | recvTimingResp on same endpoint
                         v
       fixed record lookup -> exact validate -> ACK or abort-drain

 Native Port.cc maps/counters/queues/Row/Offset state: never entered
```

### Authoritative states

There is one action and eight transaction records per MAA. Every constructed
logical `PacketPtr` is named by exactly one transaction record.

| Record state | Sole owner | Permitted next state | Packet rule |
| --- | --- | --- | --- |
| `FREE` | free list encoded by record state | `QUEUED` | no packet |
| `QUEUED` | dedicated FIFO cell | `PENDING_SEND`, terminal abort | no `PacketPtr` constructed yet |
| `PENDING_SEND` | single pending register | `WAIT_RETRY`, `IN_FLIGHT`, terminal abort | same pointer; no clone |
| `WAIT_RETRY` | single pending register | `PENDING_SEND` only from exact port retry | same pointer is retried |
| `IN_FLIGHT` | response-credit cell | terminal response or `ABORT_DRAIN` | MAA may not delete it |
| `ABORT_DRAIN` | response-credit cell | terminal abort when its response returns | abort never abandons it |

The FIFO stores fixed record indices, not packets or Python objects. A record
must occur in exactly one owner structure. The model checks this after every
transition. There is no list of past transactions, tombstone history, external
oracle, native aggregate count, or inferred owner.

The slot state machine is:

```text
Empty -> Filling --512 exact ReadResp--> Clean
Clean --pin/full overwrite--> Dirty
Dirty --unpinned start--> Writeback --512 exact WriteResp--> Empty

Clean -> Empty is allowed only while unpinned.
Filling/Dirty/Writeback and every pinned state forbid reuse.
```

### Exact identity

Each record stores all of the following before packet construction:

```text
TransactionKey = {
  descriptor:u8, generation:u32, slot:u8, page:u8,
  line:u16, operation:u8
}
RecordIdentity = {
  record:u8, recordEpoch:u16, packetID:u32, pageActionID:u32
}
ExpectedWire = {
  address:u64, requestCommand:u8, responseCommand:u8,
  size:u8 (=64), port:u8
}
```

The supported coordinate ranges are descriptor `0..1`, page `0..3`, slot
`0..1`, line `0..511`, operation `{Fill,Writeback}`, and port `0..255`.
Generation, action, packet, and record-epoch counters are unsigned,
non-wrapping identities. Page admission reserves enough remaining packet and
record-epoch identities for all 512 lines. If that cannot be proved, admission
returns exhaustion with no mutation. There is no reset path that resets
generations or makes a stale identity current.

Addresses are page-base plus `line * 64`; page bases are 32-KiB aligned. A
fill uses `ReadReq -> ReadResp`; a dirty writeback uses response-bearing
`WriteReq -> WriteResp`. `WritebackDirty` and response-less write acceptance
are deliberately excluded.

### Callback routing and ownership order

Production must add a distinct `LogicalCacheSidePort : RequestPort`. It is
connected to the same cache-side crossbar/cache fabric but is not an existing
`CacheSidePort` object. Consequently its callbacks cannot decrement the native
`outstandingCacheSidePackets` counter or call shared unblock code.

Issue order:

1. Validate the complete page action and reserve all non-wrapping identities.
2. Allocate one free fixed record, reserve its bounded packet ID, commit every
   expected field, and put its index into the fixed FIFO. A queued record owns
   metadata but no `PacketPtr`.
3. Only with a free response credit, move the head index to the pending
   register, construct its `Request`, `Packet`, and dedicated sender state, and
   record their exact pointers. At most one pending object exists, so the four
   line buffers cover three in-flight plus one refused pending packet, or four
   in-flight packets.
4. Call `sendTimingReq(record.packet)` with that exact pending pointer.
5. On `false`, retain the identical constructed pointer in `WAIT_RETRY`. The exact
   `LogicalCacheSidePort::recvReqRetry` moves only that record back to
   `PENDING_SEND`. On `true`, bind one credit and move it to `IN_FLIGHT`.

Response order is intentionally the reverse of the rejected native ordering:

1. The dedicated endpoint establishes the domain. Scan the fixed eight records
   for exact `PacketPtr` equality **without reading `pkt->req`, address, command,
   size, data, or dereferencing `senderState`**. Packet identity is the primary
   lookup. A sender-state object may be attached only to satisfy downstream
   gem5 stacking; it is owned and deleted with its exact record and is never an
   ownership oracle.
2. If no record owns the pointer, classify foreign/stale, increment only a
   saturating dedicated diagnostic, and mutate no record, slot, credit, or
   native state. The production prototype must establish the downstream
   response-packet ownership convention with an integration test before it can
   safely consume an unknown pointer.
3. For the found record, first verify record state, record epoch, exact
   `RequestPtr`, key, address, command, size, port, and issued/unACKed line.
4. Only after every check succeeds, copy fill data into the named slot line or
   accept the write ACK, set one ACK bit, return one response credit, destroy
   sender state/data/packet in the documented order, and free the record.
5. A corrupted response carrying a pointer owned by a live record consumes and
   terminates only that exact record, changes its page action to
   `ABORT_DRAIN`, deletes all never-sent siblings, and retains every sent
   sibling until its callback. It never steals a sibling or native aggregate
   counter.

The pointer-preservation premise is a production integration gate. If the
chosen cache path legally replaces the response `PacketPtr`, option 2 must not
fall back to address, `RequestPtr`, or sender-wrapper ownership. Instead add a
dedicated immutable route token supported by that path, or STOP integration.

### Completion, abort, drain, reset, and teardown

The action owns two exact 512-bit sets. Issuing a line sets `issued[line]`;
accepting its response sets `acked[line]`. Completion requires all of:

```text
nextLine == 512
ackedCount == 512
issued == acked == ((1 << 512) - 1)
FIFO empty, no pending owner, no transaction record for the action
```

Thus readiness cannot be vacuous. Fill completion alone changes `Filling` to
`Clean`. Writeback completion alone sets the destination page ACK and releases
the slot. A four-page descriptor is complete only when all four page ACK bits
are set, which requires exactly 2,048 `WriteResp`s. A four-page source fill
requires exactly 2,048 read responses. The complete four-page transform has
4,096 response events; high-level completion cannot precede the last one.

Abort is a state, not permission to forget packets. It deletes only queued or
pending packets, changes sent records to `ABORT_DRAIN`, and completes only after
every sent packet returns. An aborted fill releases its slot after drain. An
aborted writeback returns its slot to `Dirty`; it cannot be discarded or
reused. If a responder never returns a sent packet, drain and teardown remain
blocked, which is the safe outcome.

Reset requires: transport drained, no pins, and no dirty/writeback slot. It may
discard clean payload and allocations but preserves every generation and record
epoch. Teardown additionally requires free descriptors and empty slots, then
seals the transport permanently. No destructor may silently delete an
in-flight packet.

## Finite capacities and state ledger

The executable point fixes one action, eight transaction/FIFO entries, four
response credits, two descriptors, four pages each, and two slots. These are
architecture constants, not Python collection growth. C++ must use
`std::array`, bounded indices, and `static_assert` on packed hardware layouts;
host allocator/object overhead is reported separately and is not hardware.

| Charged hardware state per MAA | Bytes | Basis |
| --- | ---: | --- |
| Two private FP64 4K slots | 65,536 | `2 * 4096 * 8` |
| Two descriptor records | 32 | `2 * 16` packed |
| Two slot records | 32 | `2 * 16` packed |
| One page action plus issued/ACK bitmaps | 160 | includes two 64-byte bitmaps |
| Eight transaction records | 320 | `8 * 40` packed |
| Request FIFO and head/tail/pending control | 16 | eight byte indices + control |
| Four-credit owner ledger | 8 | byte owners + control/padding |
| Four dedicated 64-byte line buffers | 256 | fill/write data staging |
| Saturating fault/control counters | 32 | no wrapping native counters |
| **Total / MAA** | **66,392** | **65,536 payload + 856 metadata** |

At four MAAs the fixed point is 265,568 bytes. This ledger does not count
gem5-only `Packet`, `Request`, virtual dispatch, allocator, or debug-stat host
bytes as hardware. A production simulator implementation must separately
report `sizeof` and peak host allocations for those objects. It may not use an
unbounded `std::map`, vector, deque, packet queue, or event history in place of
the charged arrays.

## Likely timing charges (not performance evidence)

The model has no clock and makes no speedup claim. A timed implementation must
charge at least:

| Event | Mandatory charge to model |
| --- | --- |
| Page admission | one controller lookup/reservation; no zero-cycle mutation |
| Line materialization | one record/FIFO allocation and one 64-byte slot read for writeback |
| Arbitration | logical port competes normally at the existing xbar/cache; no priority bypass |
| Send refusal | retains the pending entry until real `recvReqRetry`; no polling success |
| Response | eight-entry owner CAM, exact-field compare, one line-buffer transfer, bitmap update |
| Page completion | bitmap reduction and controller transition after response 512 |
| Four-page operation | 2,048 fill responses plus 2,048 write ACKs, with at most four in flight |

With four credits, there are at least 128 response-credit waves per page if all
four credits are usable. That is a concurrency bound, not a cycle prediction:
cache latency, arbitration, coherence, controller clocking, and backpressure
are unknown until a later authorized gem5 implementation and validation.

## Option comparison

| Question | Continue shared native repair | Dedicated logical bridge |
| --- | --- | --- |
| Owner authority | Must replace address map/vector semantics for every native unit | One fixed record is authoritative only for logical packets |
| Retry/backpressure | Entangled with native block reasons, scheduling, and counters | One pending owner and exact retry endpoint |
| Response validation | Must reorder shared erase/counters/unit callbacks | Validate exact record before any release |
| Abort/drain | Must unwind Row/Offset, deferred maps, coalescing, and aggregate counts | Cancel unsent; drain only exact sent records |
| Native regression surface | All indirect/stream/cache/memory paths | Connection hooks plus new classes; native functions untouched |
| Coalescing | Native address coalescing must be made generation-safe | Forbidden for logical traffic |
| Exhaustion | Multiple existing counter/vector/map policies | Pre-reserved non-wrapping fixed identities |
| Residual risk | High: redesign while preserving legacy corner cases | Moderate: pointer lifetime, port wiring, cache protocol, coherence |
| Recommendation | **STOP shared repair** | **GO prototype after independent review** |

### Seven rejected blockers: avoided or hidden?

| Prior blocker | Dedicated result | Why this is structural |
| --- | --- | --- |
| Panicking Row-owner probes before cleanup | Avoided | no Row API or owner probe is reachable |
| Unbounded/unsafe Offset-owner probing | Avoided | no Offset table is reachable; fixed eight-record scan |
| Abort abandons corrupt claims | Avoided | sent packets remain `ABORT_DRAIN` owners until callback |
| Abort steals unrelated aggregate counters | Avoided | credits are per-record; only the exact credit is returned |
| Validation after `PacketPtr` erasure | Avoided | all exact checks precede ACK, delete, credit return, and free |
| Native deferred-counter wrap/exhaustion | Avoided | no native deferred queue/counter; identities pre-reserve 512 uses and never wrap |
| Non-authoritative native vectors and early `pkt->req` dereference | Avoided | fixed record is sole authority; pointer lookup precedes all packet/request field reads |

These are not merely hidden behind a tag: the logical callback has no call edge
to the implicated native structures. The remaining pointer-preservation and
unknown-response ownership questions are new, narrow gem5 API integration
risks and are explicit STOP gates, not claims that the model proves gem5.

## Production changed-file surface

No production file is changed by this work. A minimum later implementation is
expected to touch:

| Path | Minimum change |
| --- | --- |
| `src/mem/MAA/LogicalSPDCacheTransport.hh` (new) | packed records, arrays, state machine, invariants |
| `src/mem/MAA/LogicalSPDCacheTransport.cc` (new) | packet construction, exact callbacks, abort/drain |
| `src/mem/MAA/MAA.hh` | own transport and dedicated port vector only |
| `src/mem/MAA/MAA.cc` | construct/destroy, `getPort`, schedule logical actions; no native response edit |
| `src/mem/MAA/MAA.py` | new `VectorRequestPort logical_cache_sides` |
| `src/mem/MAA/SConscript` | compile new source |
| `configs/common/MAAConfig.py` | connect dedicated ports to existing cache-side fabric |
| new C++ unit/integration tests | ownership, retries, bad replies, pointer preservation, drain |

The preferred implementation does **not** edit `Port.cc`,
`CacheSidePort.cc`, `MemSidePort.cc`, `IndirectAccess.*`, `StreamAccess.*`, or
`Tables.*`. If the integration unexpectedly requires any of those, re-open the
architecture review: native preservation is no longer by construction.

Unavoidable shared gem5 APIs are `Request`, `Packet`, `RequestPort`,
`sendTimingReq`, `recvReqRetry`, `recvTimingResp`, event scheduling, the
cache-side xbar connection, requestor ID allocation, and cache coherence. API
use is allowed; shared MAA owner containers and callbacks are not.

## Correctness risks and STOP gates

1. **Response pointer preservation.** Prove the selected cache path returns the
   same `PacketPtr`, including miss, hit, retry, and write response. Otherwise
   provide a truly immutable dedicated route token or stop.
2. **Unknown-response disposal.** Prove who owns an unknown response packet at
   a `RequestPort` callback. Do not double-delete a synthetic duplicate or leak
   a foreign packet.
3. **Coherence and commands.** Demonstrate that direct 64-byte `WriteReq`
   receives one `WriteResp` and has the required coherent write semantics.
4. **Private SPD access.** Generated fill/writeback movement must address only
   the hidden slots and must not make them CPU-visible.
5. **Reset/destructor ordering.** gem5 drain/checkpoint/destructor sequencing
   must honor the stronger no-in-flight teardown rule.
6. **Fairness.** The extra requestor must not bypass normal xbar arbitration or
   starve native MAA/CPU traffic.
7. **Counter saturation.** Diagnostics saturate and cannot become authority;
   overflow must not alter functional state.

Any failure of gates 1--5 is a STOP, not permission to route the packet through
the native maps.

## Verification plan

The executable model covers finite capacity, response-credit backpressure,
same-packet retry, generation/record exhaustion, wrong port/size/command/
address/key, duplicate/stale/foreign response, exact 512-response fill and
writeback sets, pin/dirty replacement, abort drain, reset, teardown, exception
atomicity, four pages, both slots, two descriptors, and duplicate deterministic
replay. Its fixed-state digest is observability only; no history feeds a
transition.

A production prototype needs these additional gates, in order:

1. C++ state-machine unit tests with allocation failure injection and
   `static_assert` sizes; ASan/UBSan/LSan for every terminal/abort path.
2. A mock timing peer that covers hit/miss, response replacement, reordered
   responses, send refusal/retry, duplicate callback injection, abort during
   every state, and responder silence during drain.
3. A cache-xbar integration test proving exact callback routing and pointer/
   sender-state lifetimes. No native MAA map/counter may change under logical
   traffic; snapshot them before/after adversarial tests.
4. Exact data oracle for four 32-KiB fills and four acknowledged writebacks,
   including the 511/512 boundary and final high-level completion.
5. Full native regression with logical mode disabled and enabled-but-idle,
   plus code-coverage evidence that logical packets never enter native
   `Port.cc` ownership functions.
6. Only after correctness approval, separately authorize timed gem5 evidence.
   This design and model provide none.

## Current-work validation and handoff

The deliverable consists only of this document, the executable model, and its
tests. Required local validation is focused and full Python unit tests,
`py_compile`/`compileall`, style/diff checks, two identical `--demo` replays,
and a fresh independent architecture review. No gem5 build, link, or run is
authorized or informative for this checkpoint.
