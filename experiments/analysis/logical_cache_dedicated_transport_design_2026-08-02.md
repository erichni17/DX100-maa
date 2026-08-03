# Dedicated finite transport for the logical SPD cache

Date: 2026-08-02

Checkpoint repaired: `f455a4e978c531802f47fc2678b3f2fa893c24fa`

Status: executable architecture/safety contract only. No production C++ was
changed, gem5 was not built or run, and this work supplies no latency, area,
energy, or performance evidence.

## Verdict

**READY FOR FRESH REVIEW, NOT APPROVED.** The executable contract now addresses
the prior review's four STOP classes: callback endpoint identity is separate
from `Packet`; fill delivery retains its record, credit, and charged buffer
through exact slot-copy commit; each logical page has one authoritative role;
and materialization allocates both controller-created identities before FIFO or
credit mutation. This is repair evidence that still requires independent
review.

This is not approval to integrate or simulate. The prototype remains gated on
real gem5 token/RequestPtr lifetime across snoop copies, unknown-token panic
behavior, cacheable full-line `WriteReq -> WriteResp`, arbitration/fairness,
drain/checkpoint/destructor behavior, and native-regression isolation. Failure
of any gate is STOP; it is never permission to use native MAA owner maps.

## Source-grounded correction to checkpoint 2354407

The old exact-`PacketPtr` premise was invalid. gem5 says that a response must
return the sender state attached to the request even when a new response packet
is created ([packet.hh:533](../../src/mem/packet.hh#L533)). Packet copy
construction preserves both `req` and `senderState`
([packet.hh:937](../../src/mem/packet.hh#L937)). The cache snoop-supply path may
construct a new response packet and then copy a full read block into it
([cache.cc:983](../../src/mem/cache/cache.cc#L983)). Therefore request and
response `PacketPtr`s are separate incarnations in this contract.

`ReadReq` normally maps to `ReadResp`, but gem5 declares both `ReadResp` and
`ReadRespWithInvalidate` as read responses carrying data
([packet.cc:69](../../src/mem/packet.cc#L69)). The cache replacement path can
change a `ReadResp` to `ReadRespWithInvalidate`
([cache.cc:1005](../../src/mem/cache/cache.cc#L1005)). A fill therefore accepts
exactly that two-command set with identical authority/data checks. `WriteReq`
maps to response-bearing `WriteResp` in the same command table; no other write
response is admitted.

gem5 sender states form a stack whose predecessor must be restored on return
([packet.hh:459](../../src/mem/packet.hh#L459)). The dedicated endpoint requires
its exact token object to be the one and only returned top-level state. It does
not call `findNextSenderState`, search a mutable predecessor chain, accept an
equal copied token, or tolerate an unpopped wrapper.

The clean MAA baseline maps a cache packet to `core_addr(pkt->getAddr())`
([CacheSidePort.cc:115](../../src/mem/MAA/CacheSidePort.cc#L115)); `core_addr`
removes the transaction/line offset and selects the low configured core bits
([MAA.cc:459](../../src/mem/MAA/MAA.cc#L459)). The executable point fixes the
baseline four ports (`num_cores = 4` at
[MAA.py:139](../../src/mem/MAA/MAA.py#L139)) and implements
`port = (address >> 6) & 3` independently for every 64-byte line.

## Isolation boundary

Logical traffic uses a new `LogicalCacheSidePort : RequestPort` connected to
the existing cache-side fabric. Its callbacks are dedicated. It never calls or
mutates:

- `MAA::sendPacket`, `sendOutstandingCachePacket`, or
  `MAA::recvTimingResp`;
- native outstanding/deferred maps, native aggregate counters, owner vectors,
  or shared unblock paths;
- `IndirectAccess`, `StreamAccess`, retirement, Row, Offset, or native unit
  state.

The unavoidable shared surface is gem5 `Request`, `Packet`, `SenderState`,
`RequestPort`, timing callbacks, event scheduling, requestor IDs, the
cache-side xbar, and cache coherence. Logical mode disabled and enabled-but-idle
must be native-regression identical before simulation evidence is considered.

## Fixed descriptor, slot, and address contract

There are two descriptors, four pages per descriptor, and two private 32-KiB
slots. Allocation atomically validates and stores in each descriptor:

```text
allocated:u1, generation:u32 (non-wrapping)
backingBase:u64, backingSpan:u18 (= 131,072 exactly)
backingReady:u4, writebackAcked:u4
```

`backingBase` is exactly 128-KiB aligned. The inclusive end
`base + span - 1` must fit u64 without overflow. Any two allocated descriptor
ranges must be distinct and non-overlapping. A page address is derived only as
`descriptor.backingBase + page * 32KiB`; an optional caller claim is merely an
equality assertion and a wrong claim fails atomically. The same is true of a
claimed generation. Every derived line and its 64-byte end are rechecked in
u64.

The slot state/role machine is:

```text
Empty/None
  -> Filling/Source --512 exact legal read responses/copies--> Clean/Source
  -> pinned Dirty/Source
  -> explicit atomic destination binding -> Dirty/Destination
  -> unpinned Writeback/Destination --512 exact WriteResp--> Empty/None
```

A source-filled slot cannot be written back. Binding must name a different
live descriptor, exact live generation, unclaimed page, and pinned dirty source
slot; only then does one atomic transition change descriptor/page/generation
and role to destination-dirty. Dirty, filling, writeback, or pinned slots cannot
be evicted or reused. Aborted writeback returns to destination-dirty; aborted
fill clears the slot only after drain.

For each exact `{descriptor, generation, page}`, at most one live slot owner
exists. A Source owner requires `backingReady=1` and `writebackAcked=0`; a
Destination owner requires both bits zero; an acknowledged page has no slot
owner and cannot be republished. `publish_backing`, source fill, and destination
binding validate this rule before mutation. In particular a destination-bound
page cannot be published and filled as Source into the other slot.

Each slot contains its charged fixed 32-KiB payload array. Both legal read
response commands carry exactly one transient 64-byte Packet payload. After
authority and wire validation, the response replaces the contents of the same
credit-owned fixed line buffer and the record enters `DELIVERING`. The callback
returns a bounded payload-free `{record, epoch, actionID}` ticket. The
controller validates that ticket and the record-derived slot identity, copies
directly from that charged buffer to
`slot.payload[line * 64 : (line + 1) * 64]`, and only then commits ACK,
record/credit/buffer release, refill, and possible action completion. There is
no `ReceiveResult.data` or fifth line representation. Malformed, unknown,
copied-token, stale, duplicate, failed/reentrant ticket, and abort-drain paths
never commit a payload copy. Writeback copies one exact slot line into a
credit-owned line buffer only when that line is materialized.

The lifetime guarantee comes from the explicit `DELIVERING` state and ownership
checks, not from Python's atomic call sequencing. The model's convenience
`receive` method immediately invokes the separate commit, while
`begin_receive`/`commit_delivery` expose delayed and failed delivery. A
production implementation must preserve the same state boundary across its
controller scheduling mechanism.

## Record authority and Packet incarnations

One page action owns eight fixed transaction records, an eight-index FIFO, and
four response credits. Each record embeds one persistent logical route-token
object whose address never changes:

```text
RouteToken : Packet::SenderState {
    record:u3, epoch:u16, actionID:u32
}
```

The record index plus record epoch, action ID, and exact token object identity
are the sole route authority. Token fields may be reinitialized only while the
record is `FREE`; they are immutable for the complete live epoch. The embedded
object is neither dynamically replaced nor searched by value. Consequently an
old response can retain the same token address after record reuse and observe
the new epoch fields; exact old `RequestPtr` mismatch then rejects it before
ACK, data, or drain mutation. No retired-token set or tombstone exists.

The record separately stores the exact `RequestPtr`, transaction key, expected
wire fields, credit owner, and one 64-byte buffer after materialization:

```text
TransactionKey = {
  descriptor:u1, generation:u32, slot:u1, page:u2,
  line:u9, operation:u1
}
ExpectedWire = {
  address:u64, requestCommand:u2, responseCommandSet:u2,
  size:u7 (=64), callbackPort:u2
}
```

`TransactionKey` is record-side model/hardware state. gem5 `Packet` has no such
field, and it also has no callback-port field. `PacketIncarnation` deliberately
has neither `key` nor `port`, and this proposal adds no packet extension. The
exact `LogicalCacheSidePort` method receiving the callback supplies a separate
`callback_port` context. The callback first authenticates the top token, then
validates that endpoint against the record's address-derived port, and only
then inspects the remaining Packet fields. Descriptor, generation, slot, page,
line, and operation come only from the record.

The callback may receive a different `PacketPtr`. A legal replacement succeeds
only when it carries:

1. exactly the embedded token object as the sole top returned sender state;
2. exact token record/epoch/action fields;
3. the exact original `RequestPtr` object;
4. callback on the exact address-derived `LogicalCacheSidePort`, followed by
   command in exactly `{ReadResp, ReadRespWithInvalidate}` for fill or exactly
   `WriteResp` for writeback, exact u64 address, 64-byte size, and payload
   shape. Exact address plus the authenticated record proves its line.

An equal copied token fails. Missing token, token under a wrapper, token with a
residual predecessor/wrapper, reused stale token, duplicate returned token, or
unknown/non-owned token causes a production STOP/panic before request, address,
command, size, port, or payload inspection and before any model/native
mutation. No address or `RequestPtr` lookup is a fallback authority.

The exact token with a wrong `RequestPtr` or other real Packet field names a
record but is malformed. Production performs the complete validation without
mutation and immediately panics. There is no quarantine queue, returned-packet
storage, deletion-and-recovery rule, or expectation of a later good response.
Process termination owns cleanup. The Python test may catch `ProductionStop`
and inject a later good response only to inspect the pre-panic proof that the
malformed callback caused no ACK, payload copy, owner release, or false drain;
that continuation is not a production recovery path.

## Fail-atomic issue, ownership, and deletion

Credit availability is checked without mutation. The two controller-created
host identities and exact `Request`/request `Packet` plus line-buffer contents
are then prepared before the credit owner or FIFO changes. Only an infallible
commit reserves the selected credit and pops the exact FIFO head. Identity
exhaustion therefore leaves the queued record, FIFO, credit ledger, and all
counters bit-identical. With no free credit, accepted and refused attempts are
also bit-identical no-ops. Pending, in-flight, and delivering records together
own at most four 64-byte buffers; queued records own none.

The state and ownership transitions are:

| State | Packet/buffer owner | Transition |
| --- | --- | --- |
| `QUEUED` | fixed FIFO owns metadata; no Request, Packet, credit, or buffer | reserve credit, then materialize |
| `PENDING_SEND` | dedicated transport owns exact Request, Packet, credit, buffer | call `sendTimingReq` |
| `WAIT_RETRY` | dedicated transport retains the identical objects and credit | exact rejected-line port retry only |
| `IN_FLIGHT` | memory system owns Packet incarnation; fixed record owns RequestPtr, token, response obligation, credit, buffer | exact response or abort-drain |
| `DELIVERING` | fixed record retains RequestPtr, token, credit, and the same charged buffer; bounded ticket carries no payload | exact controller slot-copy commit, reentry STOP, or local abort |
| `ABORT_DRAIN` | same split ownership; no payload/ACK side effect | exact fully validated response only |

Deletion/lifetime rules are explicit:

- refused packet: never deleted or reconstructed; retained and retried exactly;
- pending packet canceled before acceptance: dedicated transport deletes its
  Packet/Request and releases its credit/buffer;
- accepted original packet: memory system owns it; if a cache creates a new
  response, that cache/path deletes the replaced original under gem5's normal
  convention;
- valid returned fill packet: dedicated callback validates and stages into the
  same buffer, then the exact controller copy commits before ACK/release; a
  valid write response has no payload-copy stage;
- malformed packet bearing an owned token: immediate production panic after
  mutation-free validation; no quarantine or recovery storage is modeled;
- duplicate, stale, copied-token, missing-token, and unknown-token packet:
  production panic before deletion or unrelated mutation; process termination
  avoids both guessed ownership and double deletion;
- queued/pending siblings on abort: locally destroyed; accepted siblings remain
  exact abort-drain obligations; a locally owned `DELIVERING` record is
  canceled without invoking the controller copy.

Unknown-token disposal is intentionally a pre-simulation integration gate:
the mock and xbar tests must prove the callback/panic ownership convention.

## Completion, abort, reset, and teardown

The action uses exact 512-bit `issued` and `acked` sets. Page completion
requires all of:

```text
nextLine == 512
ackCount == 512
issued == acked == (1 << 512) - 1
FIFO empty, no pending owner, no live record for the action
```

Every fill therefore consumes 512 unique data-bearing read responses from the
two-command legal set. Four source pages consume exactly 2,048 read responses.
Every destination page consumes 512
response-bearing full-line `WriteReq -> WriteResp` transactions; the four-page
destination bitset becomes complete only after exactly 2,048 `WriteResp`s.
Identities do not wrap: descriptor generations, action IDs, record epochs, and
host request/packet incarnation IDs stop at their maxima. Page admission
reserves 512 record epochs and `2 * 512` controller-created host incarnation
IDs (Request and request Packet) before mutation. Test-peer replacement
responses use a disjoint bounded host-only identity space, so public response
construction cannot consume the reserved controller budget.

Abort is a bounded enum (`NONE`, `CALLER`), not an unbounded string. It deletes
only never-accepted work and marks every accepted
record `ABORT_DRAIN`. Even during abort, callback release requires the exact
top token and RequestPtr, plus command, address, size, payload shape, and exact
callback port for the token-derived record line. A malformed abort callback
immediately panics and cannot make drain true. Delayed valid active fills remain
`DELIVERING` with their record, credit, and buffer owned and unacknowledged.
Abort cancels such locally owned delivery without invoking the controller copy.
Delayed callbacks for records already in flight when abort began release exact
abort-drain obligations without entering `DELIVERING` or writing slot data.

Reset requires drained transport, zero pins, and no dirty/writeback slot. It
may discard clean payload/allocations but preserves generation and record-epoch
anti-stale state. Teardown requires free descriptors, empty slots, and drained
transport, then permanently seals both model and transport. Every public
mutating model entry point rejects after teardown; a destructor may not erase
in-flight state.

## Finite storage ledger

The executable Python shapes are bounded representations, not synthesized
objects: descriptor/slot/record lists have lengths 2/2/8; FIFO and credits have
lengths 8/4; Python big integers represent exactly two 512-bit sets; every
payload/line buffer is exactly 32 KiB/64 B. `RouteToken`, `RequestPtr`,
`PacketIncarnation`, `DeliveryTicket`, and `ReceiveResult` are bounded host
objects. The ticket is a transient 51-bit control value and contains no
payload. `TransactionKey` exists only in a fixed record. The SHA-256 digest is
transient host-only observation and never feeds a transition. No event history,
tombstones, strings, maps, vectors, or growing diagnostic lists exist.

### Packed logical hardware state

| Component | Exact bits | Basis |
| --- | ---: | --- |
| Two private slot payloads | 524,288 | `2 * 32KiB * 8` |
| Two descriptor correlators | 246 | `2 * 123` from the explicit formula below |
| Two slot correlators | 164 | `2 * 82` from the explicit formula below |
| Page action plus two 512-bit sets | 1,184 | bounded fields including both sets |
| Eight transaction correlators | 1,416 | `8 * 177` including 3-bit eight-state record field |
| Request FIFO/control | 38 | eight 3-bit indices, head/tail/count, pending sentinel |
| Four credit owners | 16 | four 4-bit record/sentinel owners |
| Four 64-byte line buffers | 2,048 | shared across pending, in-flight, and delivering |
| Bounded global control | 41 | action/exhaustion/lifecycle plus exact 1-bit delivery-reentry guard |
| **Packed total per MAA** | **529,441** | **66,181-byte whole-state ceiling** |

The exact per-object formulas are:

```text
Descriptor = allocated:1 + generation:32 + backingBase:64
           + backingSpan:18 + backingReady:4 + writebackAcked:4 = 123 bits

Slot = phase:3 + role:2 + descriptor{-1,0,1}:2 + generation:32
     + page{-1..3}:3 + pins:8 + actionID:32 = 82 bits

PageAction = state:2 + actionID:32 + operation{none,fill,writeback}:2
           + descriptor{-1,0,1}:2 + generation:32 + page{-1..3}:3
           + slot{-1,0,1}:2 + baseAddress:64 + nextLine{0..512}:10
           + issued:512 + acked:512 + ackCount{0..512}:10
           + abortCode:1 = 1,184 bits

TransactionKey = descriptor:1 + generation:32 + slot:1 + page:2
               + line:9 + operation:1 = 46 bits
TransactionRecord = state:3 + epoch:16 + actionID:32 + key:46
                  + address:64 + requestCommand:2 + responseCommandSet:2
                  + size:7 + callbackPort:2 + credit{-1..3}:3 = 177 bits

FIFO/control = 8 * recordIndex:3 + head:3 + tail:3 + count{0..8}:4
             + pending{none,0..7}:4 = 38 bits
Global = nextActionID:32 + actionIDsExhausted:1 + sealed:1
       + activeSlot{none,0,1}:2 + activeOperation{none,fill,writeback}:2
       + deliveryCommitActive:1 + lastAbort:1 + tornDown:1 = 41 bits
```

The FIFO's unused array positions are don't-care under `count`, and a record's
callback port is meaningful only in non-`FREE` states, so those state-dependent
encodings do not need extra sentinels. `DELIVERING` is the seventh record value
and still fits the 3-bit state field. The fixed record index is physical and the
record-side epoch/action fields above back the host SenderState token; the
gem5 polymorphic token object itself is in the host-object category, not counted
again. The payload-free 51-bit ticket is a transient callback wire, not stored
state. The persistent one-bit reentry guard is counted in `Global`.

### Representation categories that must not be conflated

| Category | Result | Meaning |
| --- | ---: | --- |
| Packed logical hardware | 66,181 B/MAA | exact 529,441-bit state and whole-state byte ceiling above |
| Proposed naturally aligned fixed-width C++ hardware projection | 66,324 B/MAA | explicit fixed-width projection excluding gem5 objects; production `sizeof`/`static_assert` still required |
| gem5-only host objects | no claimed byte total | eight embedded polymorphic SenderState tokens, bounded `RequestPtr`/`PacketPtr` objects, copied response packets, vptr/shared_ptr/allocator overhead |
| Python-only host representation | no claimed byte total | lists, Enum/dataclass objects, arbitrary-precision 512-bit integers, JSON/SHA helpers |
| Synthesized area/timing | **not measured** | no RTL synthesis, clock, area, latency, energy, or throughput result |

The aligned projection is the explicit sum
`65,536 + 48 + 32 + 160 + 256 + 16 + 8 + 256 + 12 = 66,324` bytes for
payloads, descriptors, slots, action, records, FIFO, credits, line buffers, and
global control respectively. It is a proposed layout, not a measured C++
`sizeof` result; integration must replace it with declarations and
`static_assert`s rather than preserving the number with artificial padding.

At four MAAs the packed logical-state arithmetic is 264,724 bytes, but this is
only a storage sum—not a silicon area or performance claim.

## Timing obligations, not timing evidence

A later timed implementation must charge page admission, fixed-record/FIFO
allocation, credit arbitration before materialization, 64-byte buffer movement,
normal xbar arbitration, real refusal/retry, eight-entry token-owner compare,
full wire validation, staged delivery, exact slot copy, then ACK/bitmap/release
and completion reduction. Four credits imply no more than four materialized
lines and at least
128 credit waves per page. Neither fact predicts cycles or speedup.

## Option comparison

| Question | Shared native repair | Dedicated logical prototype |
| --- | --- | --- |
| Authority | redesign address maps/vectors for every native unit | one fixed record plus persistent token, live-epoch fields, exact RequestPtr |
| Packet replacement | native callback assumptions remain entangled | legal new Packet accepted with exact RequestPtr/token |
| Retry/buffers | shared block reasons/counters | one exact pending owner; fail-atomic identities/FIFO/credit commit |
| Response safety | shared erase/counter/callback ordering | token, callback endpoint, wire validation, `DELIVERING`, exact copy, then ACK/release |
| Abort/drain | Row/Offset/deferred unwind | cancel unsent; validate and drain exact sent owners |
| Native regression surface | broad | new endpoint/classes and connection hooks only |
| Recommendation | **STOP** | **READY FOR FRESH REVIEW**, gated below |

## Pre-simulation production gates

1. C++ unit tests and `static_assert`s reproduce token identity, finite arrays,
   packed/aligned ledgers, allocation failure atomicity, and all terminal paths
   under ASan/UBSan/LSan.
2. A mock timing peer proves same-Packet and replacement-Packet responses,
   exact copied `senderState`/RequestPtr lifetime, refusal/retry on all four
   ports, response reorder, malformed abort callbacks, duplicate/stale/unknown
   panic ownership, and responder silence blocking drain.
3. Cache/xbar integration proves cacheable 64-byte `ReadReq -> ReadResp` data
   and response-bearing coherent `WriteReq -> WriteResp`, including snoop-copy
   creation and deletion ownership.
4. Arbitration evidence proves the new requestor uses normal xbar fairness and
   cannot starve CPU/native MAA traffic.
5. Drain, checkpoint/restore, reset, and destructor tests preserve all live
   token/RequestPtr obligations and non-wrapping identities.
6. Native map/counter/queue/Row/Offset snapshots and full regressions pass with
   logical mode disabled, enabled-idle, and active; coverage proves logical
   callbacks never enter native owner machinery.
7. Only after those correctness gates receive fresh approval may a separately
   authorized gem5 timing experiment begin. This checkpoint makes no
   performance claim.

## Current checkpoint verification scope

The executable suite covers all four callback endpoints, exact rejected-port
retry, legal Packet replacement, both legal read response commands,
copied/missing/non-top/residual/reused tokens, same RequestPtr with wrong token,
same token with wrong RequestPtr, wrong wire/data fields, mutation-free wrong
callback endpoint, four retained credits/buffers through delayed delivery,
copy-before-release/final-clear ordering, failed/reentrant delivery,
abort-without-consumer, fifth materialization refusal, exact four-page payload,
exactly 512 responses/page and 2,048 write ACKs, descriptor
base/page/generation/overlap/u64 atomicity, single per-page role ownership, the
conflicting two-slot publication sequence, near-exhaustion materialization
atomicity, disjoint peer-response identities, reset, terminal teardown,
non-wrapping identities, and deterministic replay.

The checkpoint modifies only this document, its executable model, and its
Python test. It does not authorize or perform a gem5 build, link, or run.
