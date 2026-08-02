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
Logical sender-state discovery inspects the packet stack rather than only its
top. A state may be released only when it is the top frame, its own predecessor
chain does not alias it, every map-owned packet's full sender-state chain is
disjoint, and each scan terminates within the fixed 64-frame proof bound.

## Terminal wrapper disposition contract

`MAA::recvTimingResp` now returns exactly one bounded disposition which both
`CacheSidePort::recvTimingResp` and `MemSidePort::recvTimingResp` pass through
the same `invokeTimingResponseWrapper` helper:

| Disposition | Exact map entry | Logical sender state | Cache-side credit | Packet | Result |
|---|---|---|---|---|---|
| `Retired` | erased once | exact top state popped/deleted once | decremented once | wrapper deletes once | return `true` |
| `DroppedExtra` | unchanged | extra packet's non-aliased top state popped/deleted once | unchanged | wrapper deletes once | return `true` |
| `FatalOwnedCorruption` | exact entry erased before return | popped/deleted when non-aliased | decremented once | wrapper deletes once, then panics | no retry |
| `FatalUnownedExtra` | unchanged | retained when unsafe/aliased | unchanged | wrapper deletes once, then panics | no retry |

A tagged packet which is not the exact map-owned pointer can therefore never
retire or settle the active same-address request, even if it copied a valid
tag. A stale, forged, or duplicate extra is droppable only when its logical
sender state is the packet's current stack top and is not referenced by any
map-owned packet. The drop may update a bounded rejection counter, but does
not mutate the outstanding entry, stream credit, cache-side response credit,
or deferred queue. An untagged or sender-state-aliased extra fails closed.

An exact pointer with corrupt command, tag, address, route, response size, or
owner cannot later receive a second valid response: gem5 mutates and returns
the request packet itself. `Port.cc` therefore records the rejection where an
owner can be identified, aborts response-bearing Write counter ownership,
releases a safely owned logical sender state, erases the map entry, and returns
`FatalOwnedCorruption`. The wrapper destroys the packet before panicking. No map
entry can retain the pointer that the wrapper destroys, and fatal cleanup does
not promote a same-address deferred packet.

Accepted logical responses perform ledger/data delivery before popping the
exact logical sender state, erasing the exact entry, and finally promoting one
same-address deferred packet. Accepted ordinary responses likewise complete
their owner callback, erase the exact entry, and only then promote. This keeps
promotion outside all extra-packet and fatal paths.

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
- A fatal exact logical `WriteResp` uses the separate `ResponseAborted`
  transition to relinquish its retained stream count before the map pointer is
  removed. This transition is never used for a different packet.
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
credit lifetimes, and command-specific stream counter ownership. Counter tests
exercise zero and maximum boundaries, fatal abort, and explicit no-wrap checks.

Production compile validation builds `build/X86/mem/MAA/{Port,
CacheSidePort,MemSidePort}.o` only. ASan and UBSan host replays are separate;
ASan uses `detect_leaks=0` because LeakSanitizer is not reliable under the
ptrace-style agent/sandbox environment, so no LSan claim is made. The logical
ABI, controller, transparent-controller, response-path scripts, and the full
dependency-light Python virtualization suite are also replayed. No gem5
simulation is run.

Recorded validation for this repair:

- Working branch: response host binary PASS plus 5/5 source contracts;
  logical ABI PASS plus 7/7 ABI and 11/11 transparent contracts; logical
  controller PASS plus 9/9 contracts; transparent controller PASS plus 11/11
  contracts; full dependency-light Python discovery 199/199.
- Production compile: `Port.o`, `CacheSidePort.o`, and `MemSidePort.o` PASS.
- Sanitizers: ASan PASS with leak detection disabled as stated above; UBSan
  PASS with halt-on-error and stack traces enabled.
- Repaired-ABI replay: response-path commits plus this repair applied cleanly
  to `a65374c`; all four unit scripts PASS, full Python discovery 220/220, and
  `git diff --check` PASS.
- gem5 modification style and `git diff --check` PASS. No gem5 simulation was
  launched.

## Integration boundary and base status

This patch does not extend public ABI helpers, MMIO decoding, or logical
scheduler policy. Compatibility is replayed by applying the response-path
series to repaired ABI commit `a65374c`; that replay is a compatibility check,
not release acceptance. A follow-up scheduler patch must supply monotonically
increasing, never-reused transaction IDs and generation IDs for the lifetime
in which callbacks can arrive. This transport layer does not invent a
scheduler identity lifecycle. The scheduler must also make its final
controller completion only after this path reports all matching write
responses. A fresh independent reviewer must decide acceptance.
