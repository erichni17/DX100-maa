# Logical stream response-path patch

Date: 2026-08-02
Patch: 3 of `logical_spd_cache_gem5_integration_plan_2026-08-02.md`
Base used for this slice: `b79f136606fddf03a93c909ed54f1f0ed836de66`

## Scope

This patch supplies the bounded response-bearing transport mechanism needed by
a later logical SPD cache scheduler. It adds no logical descriptor admission,
SPD allocation, hidden-slot mapping, indirect-producer conversion, scheduler,
or benchmark/API behavior. In particular, it is not wired to the logical
scheduler yet.

`logicalResponseManaged` is a new instruction-internal opt-in bit, false by
default. A future scheduler must set it only for one aligned 4096-element,
contiguous controller page micro-op and populate the existing logical IDs,
generation, transaction ID, page, and controller slot fields. The stream
unit then derives a full `{maa, transaction, action, logical, page,
generation, slot}` tag:

- A controller fill uses tagged `ReadReq` responses. The fixed ledger is
  completed only after every issued line has returned.
- A controller writeback still performs the stream unit's source `ReadExReq`
  step, but its written lines use `WriteReq`, are forced through the
  retirement-side cache path, and remain outstanding until their individual
  `WriteResp` callbacks.  Each fixed line state records its source
  `ReadExResp` exactly once before its `WriteResp` can be armed; a duplicate
  source callback is counted as `Duplicate` and cannot alter a terminal
  acknowledgement.
- Every controller page has a preallocated `std::array` ledger of at most 512
  64-byte lines (4096 eight-byte elements). A 32-bit page uses exactly 256
  entries; an eight-byte page uses exactly 512. The ledger rejects duplicate
  issues and cannot grow.

The port copies the full tag into both outstanding and deferred metadata. Its
logical ownership decision is a small pure finite helper exercised by the host
replay. Exact address ordering remains, but an address never authenticates a
callback: `Port.cc` first searches the outstanding map for the exact
map-owned `PacketPtr`, then cross-checks its response sender state against the
full metadata tag, line address, response kind, request address, packet
address, cached route, command, response size, owner, and active ledger.
The port snapshots the exact sender-state predecessor chain at admission.
Logical sender-state discovery inspects the packet stack rather than only its
top. A state may be released only when it is the top frame, the full chain is
acyclic and unchanged within the fixed 64-frame proof bound, and no different
packet or saved ownership snapshot in the outstanding map, deferred-address
queues, or eight cache/memory send/retry queues aliases any node. Scheduled
send events and blocked retry flags contain no `PacketPtr`; they rescan those
queues. The request ports use direct `sendTimingReq` calls and do not enqueue
these requests in their generic packet queues.

## Terminal wrapper disposition contract

`MAA::recvTimingResp` now returns exactly one bounded disposition which both
`CacheSidePort::recvTimingResp` and `MemSidePort::recvTimingResp` pass through
the same `invokeTimingResponseWrapper` helper:

| Disposition | Exact map entry | Logical sender state | Cache-side credit | Packet | Result |
|---|---|---|---|---|---|
| `Retired` | erased once | exact top state popped/deleted once | decremented once | wrapper deletes once | return `true` |
| `DroppedExtra` | unchanged | extra packet's non-aliased top state popped/deleted once | unchanged | wrapper deletes once | return `true` |
| `FatalOwnedCorruption` | exact entry erased before return | popped/deleted when non-aliased | decremented once | wrapper deletes once, then panics | no retry |
| `FatalOwnedNoPortCredit` | every exact production alias erased | popped/deleted when non-aliased | no callback-port debit; an exact different cache owner is settled directly | wrapper deletes once, then panics | no retry |
| `FatalUnownedExtra` | unchanged | retained when unsafe/aliased | unchanged | wrapper deletes once, then panics | no retry |

A tagged packet which is not the exact map-owned pointer can therefore never
retire or settle the active same-address request, even if it copied a valid
tag. A stale, forged, or duplicate extra is droppable only when its logical
sender state is the packet's current stack top and neither the packet nor any
sender-state node is referenced by an outstanding, deferred, or pending-send
owner. The drop may update a bounded rejection counter, but does not mutate
the active outstanding entry, stream credit, cache-side response credit, or
deferred queue. An untagged or aliased extra fails closed; any exact aliases
belonging to that fatal extra are detached before wrapper destruction.

An exact pointer with corrupt command, tag, address, route, response size, or
owner cannot later receive a second valid response: gem5 mutates and returns
the request packet itself. `Port.cc` therefore records the rejection where an
owner can be identified, aborts its exact response-ledger line, settles the
command-specific counter ownership, detaches the packet from every production
map/queue alias, and only then releases a safely owned logical sender state.
An unsent exact `ReadReq`, `ReadExReq`, or `WriteReq` uses
`UnsentPacketAborted`; it decrements the enqueue count after a checked nonzero
transition but does not claim a port credit it never acquired. The wrapper
destroys the packet before panicking. Fatal cleanup does not promote a
same-address deferred packet.

Cache credit authority is the exact sending `CacheSidePort`, including the
distinction between ordinary and retirement-cache ports for the same core.
The response callback passes its port identity into the owner check. A sent
exact response arriving through memory or a different cache port settles its
recorded cache owner directly after alias detachment and returns
`FatalOwnedNoPortCredit`, so the callback wrapper cannot debit an unrelated
credit. Unsent and memory-side requests own no cache credit.

Accepted logical responses first detach every exact production alias and
settle the stream count, then pop only the owned logical top while preserving
the exact legacy predecessor, perform ledger/data delivery, and finally
promote one same-address deferred packet. Ordinary responses require the
unchanged bounded predecessor snapshot and preserve it. Retirement-write
completion is armed only after structural validation, ownership erasure, and
counter settlement; the wrapper settles its credit and destroys the packet
before invoking `retirementWriteComplete`, so an internal owner rejection can
panic only after packet lifetime has ended safely.

Every `ReadResp`, `ReadExResp`, and `WriteResp` must carry the exact request
size. Logical and line-read requests therefore require 64 bytes. Ordinary
retirement writes may be explicit 4- or 8-byte gem5 requests, so their
`WriteResp` must match that recorded request size exactly; zero, short, long,
greater-than-line, and overflow-adjacent sizes are rejected.

### Why `recvTimingResp` cannot return `false`

This path has no safe retry disposition. In gem5, `ResponsePort::sendTimingResp`
directly returns `TimingRequestProtocol::recvTimingResp`; its documented
contract says a `false` result makes the responder retain the packet and wait
for `recvRespRetry` before reissuing the same response. MAA has no resource
transition after which a stale, duplicate, forged, or internally corrupt
response becomes valid, and neither wrapper schedules `sendRetryResp` for
such a callback. Returning `false` would therefore retain the same packet
forever and deadlock the responder. Every nonfatal wrapper invocation consumes
the callback and returns `true`; terminal corruption destroys the callback and
panics instead of manufacturing a retry.

## Ordinary stream compatibility

The normal STREAM_LD/STREAM_ST paths are unchanged. Ordinary stream stores retain
their response-less `WritebackDirty` behavior. In particular, a normal store
still creates `WritebackDirty`, completes through `writePacketSent` on
successful send, and never receives the new sender state or retirement-cache
route. The existing transparent controller also leaves
`logicalResponseManaged` false, so its response-less completion behavior is
unchanged.

## Stream packet counter ownership

`my_num_outstanding_stream_pkts` counts each request once when `Port.cc`
admits it to the outstanding map. A packet waiting in the exact-address
deferred queue is not counted until it is promoted, so promotion is its sole
increment point. Its command then determines the sole decrement point:

- Ordinary and logical `ReadReq` and `ReadExReq` relinquish counter ownership
  only when the corresponding send attempt is accepted. Their outstanding-map
  entries remain until the data response, but an accepted response does not
  decrement the counter again.
- An ordinary response-less `WritebackDirty` retains its established behavior:
  its one accepted send removes the outstanding entry and decrements once.
- A response-bearing logical `WriteReq` relinquishes counter ownership only
  after the fail-closed route accepts its matching `WriteResp`. Its accepted
  send marks the packet sent but deliberately leaves the count unchanged.
- A fatal exact sent logical `WriteResp` uses the separate `ResponseAborted`
  transition. A fatal exact unsent request of any logical kind uses
  `UnsentPacketAborted`. Both validate before decrement and are never used for
  a different packet.
- Rejected send attempts retain both the queued packet and its count for retry.
  Dropped extra stale, duplicate, wrong-command, wrong-address, wrong-packet,
  or wrong-identity responses have no active-request counter authority and
  cannot decrement. Exact-pointer corruption instead follows the fatal abort
  transition above.

The packet-free `decideLogicalStreamCounterUpdate` transition implements this
ownership table and is called by `Port.cc` for logical enqueue, accepted send,
and accepted or fatal exact response events. Its zero and maximum boundaries
leave the input unchanged and report an invalid transition, which the Port
treats as fatal without performing unsigned arithmetic.

## Validation

`experiments/scripts/run_logical_stream_response_unit.sh` compiles and runs a
dependency-light C++17 replay plus Python source contracts. The replay executes
the exact disposition classifier and exact wrapper executor used in production.
It covers delayed and reordered fills, exact final completion, duplicate
nonterminal `ReadExResp` callbacks, accepted Read/ReadEx/Write dispositions,
unowned tagged responses, same-address wrong packets, duplicate extras,
sender-state alias rejection, exact-pointer corruption, old-tag address reuse,
post-reset stale callbacks, fixed 512-line capacity, wrapper deletion and
credit lifetimes, exact response-ledger abort, real deferred/cache/memory queue
alias shapes, legal predecessor preservation, cyclic/over-depth/arbitrary/
logical/aliased predecessor rejection, post-delete retirement owner rejection,
and command-specific stream counter ownership. Size tests cover zero, short,
long, and overflow-adjacent `ReadResp`, `ReadExResp`, and `WriteResp` values on
both wrapper forms. Counter tests exercise zero and maximum boundaries, exact
unsent read/read-exclusive/write abort, fatal sent abort, foreign no-op, and
explicit no-wrap checks.

Production compile validation builds `build/X86/mem/MAA/{Port,
CacheSidePort,MemSidePort}.o` only. ASan and UBSan host replays are separate;
ASan uses `detect_leaks=0` because LeakSanitizer is not reliable under the
ptrace-style agent/sandbox environment, so no LSan claim is made. The logical
ABI, controller, transparent-controller, response-path scripts, and the full
dependency-light Python virtualization suite are also replayed. No gem5
simulation is run.

Recorded validation for this repair:

- Working branch: response host binary PASS plus 6/6 source contracts;
  logical ABI PASS plus 7/7 ABI and 11/11 transparent contracts; logical
  controller PASS plus 9/9 contracts; transparent controller PASS plus 11/11
  contracts; full dependency-light Python discovery 200/200.
- Production compile: `Port.o`, `CacheSidePort.o`, and `MemSidePort.o` PASS.
- Sanitizers: ASan PASS with leak detection disabled as stated above; UBSan
  PASS with halt-on-error and stack traces enabled.
- Current-lead replay: response-path commits plus this repair applied cleanly
  to detached `9fcb18c`; all seven no-gem5 virtualization gates PASS, including
  the hidden-payload gate, full Python discovery 238/238, the dedicated ASan
  and UBSan response replays PASS, the three production objects build cleanly,
  and `git diff --check` PASS.
- gem5 modification style and `git diff --check` PASS. No gem5 simulation was
  launched.

## Integration boundary and base status

This patch does not extend public ABI helpers, MMIO decoding, or logical
scheduler policy. Compatibility is replayed by applying the response-path
series to current lead `9fcb18c`; that replay is a compatibility check, not
release acceptance. A follow-up scheduler patch must supply monotonically
increasing, never-reused transaction IDs and generation IDs for the lifetime
in which callbacks can arrive. This transport layer does not invent a
scheduler identity lifecycle. The scheduler must also make its final
controller completion only after this path reports all matching write
responses. A fresh independent reviewer must decide acceptance.
