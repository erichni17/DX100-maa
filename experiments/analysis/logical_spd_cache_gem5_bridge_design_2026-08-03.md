# Logical SPD-cache to gem5/DX100 bridge design

Date: 2026-08-03

Accepted standalone baseline: `46f36d632b8af9b20e365962656d089feaec9262` (`mem: finalize standalone logical SPD cache contract`)

Accepted branch: `codex/transparent-virtual-tile-20260725`

Scope: implementation design only; no gem5 production source was changed, built, or run while producing this report.

## Executive decision

The accepted standalone Runtime can be connected to gem5 without sending a
logical packet through the native indirect/stream/RMW transport, but not by a
thin `Packet` cast alone. The first bridge must add four pieces of bounded state
outside the 66,785-byte Runtime lower bound:

1. a page-translation map for each registered 128-KiB virtual backing span;
2. fixed gem5 `Packet` sender-state and response/delivery queues;
3. timing and retry ownership for the four coherent cache-side ports; and
4. MMIO completion, drain, and checkpoint bookkeeping.

The first vertical slice is deliberately narrow: exactly one FP64 scalar
multiply (`ALU_SCALAR`, datatype 5, operation 2), one registered logical source,
one destination, four 4K-element pages, and one completion-bearing instruction
write. One `LogicalSPDCacheRuntime` lives per logical `maa_id` inside the MAA
SimObject. Its two typed 32-KiB arrays are the sole private logical payload.
The currently appended hidden SPD lanes must be removed from allocation when
the Runtime is instantiated; retaining both would allocate 128 KiB per MAA and
would double-count the intended 64-KiB private store.

Logical memory traffic uses a dedicated branch in the existing coherent
`cache_sides` RequestPorts. It never calls `MAA::sendPacket`, never enters
`my_outstanding_pkt_map`, `my_deferred_pkt_map`, a Request/Row/Offset table, or
the native indirect/stream aggregate counters. A fixed logical sender state is
the response discriminator and exact identity bridge.

Two contract extensions are prerequisites rather than optional polish:

- the Runtime needs an atomic, versioned, quiescent export/import API before
  logical state may participate in checkpoints; and
- MAA needs real drain/serialize overrides, because its inherited drain says
  “Drained” and its inherited serialization contains no MAA state.

## Evidence convention and baseline

- **FACT** is a source fact at commit `46f36d6`; every fact has a current
  `file:line` anchor.
- **DECISION** is the required first-bridge implementation choice.
- **QUESTION** is intentionally unresolved beyond the first vertical slice.
- **REJECT** is a condition under which review must stop rather than silently
  broaden or weaken the adapter.

Before this document was created, `HEAD`, the local accepted branch point, and
`origin/codex/transparent-virtual-tile-20260725` all resolved to
`46f36d632b8af9b20e365962656d089feaec9262`, and `git status --short --branch`
showed a clean isolated session branch.

The authoritative standalone surface is:

- `src/mem/MAA/LogicalSPDCacheRuntime.hh:17-25,281-321,918-927` — the Runtime
  owns Slice, Transport, Datapath, and exactly two private 32-KiB payload slots;
- `src/mem/MAA/LogicalSPDCacheSlice.hh:24-33,90-167` — two descriptors, four
  pages, two slots, 4096 FP64 elements/page, 32 KiB/page and 128 KiB/backing;
- `src/mem/MAA/LogicalSPDCacheTransport.hh:17-29,145-180,369-385,464-479` — four
  ports, eight records/FIFO entries, four response credits, and fixed request
  identity/token storage;
- `src/mem/MAA/LogicalSPDCacheController.hh:278-344,391-505,531-622` — bounded
  descriptor/slot/lease ownership and atomic full-overwrite reservation;
- `src/mem/MAA/LogicalSPDCacheDatapath.hh:14-24,45-109` — six page-wide FP64
  scalar operations performed by a synchronous C++ loop.

The packed semantic ledger is a lower bound, not a C++ object size and not a
checkpoint format. It explicitly excludes instrumentation counters and encodes
host pointers as selectors (`LogicalSPDCacheRuntime.hh:34-43`). Its accepted
total is 534,275 bits / 66,785 bytes, including exactly 65,536 payload bytes
(`LogicalSPDCacheRuntime.hh:239-255`).

## 1. Ownership topology

### Source facts

**FACT.** `MAA` is one `ClockedObject` with a configurable `num_maas`, but it
currently owns one shared SPD plus raw arrays of native functional units
(`src/mem/MAA/MAA.hh:54-55,271-285,393-440`; construction at
`src/mem/MAA/MAA.cc:52-114,149-183`). Instruction assembly assigns a requestor
to `maa_id = core_id % num_maas` (`src/mem/MAA/CpuSidePort.cc:193-215`). There
is no logical Runtime member today.

**FACT.** Runtime is the only production completion authority. It starts the
Transport action before Slice accepts it and records the exact correlation
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:810-849`); completion must match action,
descriptor, generation, page, slot, and controller serial
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:852-868`). Direct Slice publication by a bridge is
forbidden by the Runtime contract (`src/mem/MAA/LogicalSPDCacheRuntime.hh:17-25`).

**FACT.** Controller descriptor allocation and generation are bounded and
atomic (`src/mem/MAA/LogicalSPDCacheController.hh:278-295`). A full overwrite reserves
distinct source/destination slots, two leases, and compute/writeback serials as
one operation (`src/mem/MAA/LogicalSPDCacheController.hh:391-468`). Dirty data is published
only by an exact writeback response (`src/mem/MAA/LogicalSPDCacheController.hh:677-715`).

### First-bridge ownership table

| Object or obligation | Sole owner before/while live | Transfer/release rule |
|---|---|---|
| Per-MAA logical context | `MAA`, as `vector<unique_ptr<LogicalSPDCacheGem5Bridge::Context>>` of length `num_maas` | Construct Runtime storage with MAA, but bind/enable it only after `addRamulator` supplies address geometry; destroy only through the guarded teardown below. |
| Runtime, Slice, Controller, Datapath, two payload slots | The corresponding context's one `LogicalSPDCacheRuntime` | Never exposed as independent gem5 owners. Runtime destructor terminates unless destruction-safe (`src/mem/MAA/LogicalSPDCacheRuntime.hh:286-290`). |
| Logical descriptor and generation | Slice/Controller | Created only by `registerSource`/`admit`; bridge stores only a non-authoritative virtual-to-physical `BackingMap`. |
| Page slot, phase, leases, action, dirty/clean obligation | Runtime's Slice/Controller | Only Runtime calls may advance it. Exact fill/writeback completion releases the action; dirty always reaches an acknowledged writeback or prevents drain. |
| Fixed `Transport::RequestPacket`, `RequestIdentity`, `RouteToken`, line credit | Transport record/credit arrays | C++ storage never leaves Transport. `packetOwned` is the protocol ownership bit; credit survives a read response until delivery commit. |
| Provisional translation `RequestPtr` | Bridge translation slot | One 4-KiB-page translation at a time per context; release it after `finish`. It is never sent as a data Packet. |
| Data-Packet `RequestPtr` | Bridge before send, then shared by gem5 `Packet` | Construct from committed vaddr/paddr mapping; Packet deletion releases it. |
| gem5 `Packet` before successful `sendTimingReq` | Bridge fixed request slot | A false send retains the exact Packet. A true send transfers it to the hierarchy until response. |
| gem5 `Packet` after response | Bridge response queue/event | Runtime receives while its payload is valid; then bridge calls `deleteData()` and deletes Packet. |
| `LogicalPacketSenderState` | Fixed bridge pool indexed by Transport record | Pushed before send, returned on response, verified/popped by the logical branch, scrubbed and returned to the same record slot. It is never heap-owned by a cache. |
| Write data | Transport's response-credit line buffer | Packet uses a non-owning static pointer; the Transport cannot release the credit before WriteResp authentication. |
| Read response data | gem5 Packet until `Runtime::receive` copies 64 bytes into its credit buffer | Packet may be deleted after `receive` returns `DeliveryPending`; delivery ticket, not Packet, retains the line. |
| Delivery ticket and CopyHook permit | Fixed bridge delivery queue | One ticket is committed once by the copy event; stale/duplicate ticket is an internal panic. |
| Source/destination `BackingMap` | Bridge context | Staged during translation, committed only after Runtime registration/admission succeeds, serialized only at quiescence. |
| Source-registration MMIO waiter | Bridge registration slot | Holds the incoming CPU Packet until all static checks, 32 page translations, and Runtime registration complete. |
| High-level instruction waiter | Bridge context, exactly one per active Runtime | Holds final instruction-word Packet after admission; transferred to the CPU response queue only after fourth destination writeback ACK and operation retirement. |
| Event wrappers | `MAA`/bridge object; EventQueue owns scheduling, not state | Every scheduled event has one bounded context state and is descheduled or empty before reset/teardown/checkpoint. |
| Response-port queued completion | `CpuSidePort::RespPacketQueue` after `schedTimingResp` | MAA drain must wait for `queue.size()==0`; PacketQueue is independently drainable (`src/mem/packet_queue.hh:65-92,155-173,223`). |
| Teardown | `MAA::~MAA` through bridge `teardown()` | Require complete MAA drain, no queued port response, then Runtime `teardown()` and `destructionSafe()`. Never rely on a default destructor during live work. |

### Topology decision

**DECISION.** Add `src/mem/MAA/LogicalSPDCacheGem5Bridge.hh` and
`src/mem/MAA/LogicalSPDCacheGem5Bridge.cc`. `MAA.hh` holds one bridge object;
the bridge holds one fixed context and Runtime for each `maa_id`. Add both the
bridge `.cc` and the already accepted but currently unlisted
`LogicalSPDCacheTransport.cc` to `src/mem/MAA/SConscript`, whose current source
list ends at `MAA.cc` and omits Transport (`src/mem/MAA/SConscript:3-17`).
Construct the contexts with MAA, then call a one-shot
`bindPortsAndInitialize()` at the end of `MAA::addRamulator`, after it obtains
`m_tx_offset`, channel geometry, and verifies cache-side port count
(`src/mem/MAA/MAA.cc:351-388`; the Python binding is
`src/mem/MAA/MAA.py:170-172`). Admission remains closed before this call.

No bridge method returns mutable Slice, Controller, PageSpan, or completion
authority. Audit snapshots remain read-only diagnostics
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:676-724`).

## 2. RequestPort adapter

### Source facts

**FACT.** Native cache response handling is unusable for logical packets.
`CacheSidePort::recvTimingResp` unconditionally calls native
`MAA::recvTimingResp`, decrements the native port aggregate, deletes the data,
and deletes the Packet (`src/mem/MAA/CacheSidePort.cc:30-40`). Native
`MAA::recvTimingResp` requires the physical address in
`my_outstanding_pkt_map` and routes only indirect/stream responses
(`src/mem/MAA/Port.cc:698-728`).

**FACT.** Native issue is also unusable. `MAA::sendPacket` immediately consults
and mutates the native exact-address outstanding/deferred maps
(`src/mem/MAA/Port.cc:48-79`), whose storage and aggregate counters are at
`src/mem/MAA/MAA.hh:807-825`. Logical traffic must also bypass native
Request/Row/Offset ownership (`src/mem/MAA/Tables.hh:23-50,57-93,95-147`).

**FACT.** Transport `prepare` materializes at most one pending fixed request,
allocates one of four credits, and snapshots a writeback line before returning
the handle (`LogicalSPDCacheTransport.cc:395-491`). A refused request remains
the exact pending record (`LogicalSPDCacheTransport.cc:494-520`), and retry is
legal only on the stored callback port (`LogicalSPDCacheTransport.cc:533-548`).

**FACT.** gem5 sender state is a stack explicitly returned in a response
(`src/mem/packet.hh:450-471,533-579`). `Packet::dataStaticConst` is a non-owning
data binding (`src/mem/packet.hh:1142-1175`); `deleteData` frees only dynamic
data (`src/mem/packet.hh:1322-1343`).

### Exact request conversion

**DECISION.** A fixed `LogicalPacketSenderState` contains:

```
magic, maaID, transportRecord, recordEpoch, actionID,
requestIncarnation, callbackPort, virtualLineAddress,
physicalLineAddress, RequestPacket*, BackingMap generation
```

The fields duplicate identity for independent adapter validation; Runtime still
authenticates its own fixed pointers/tokens. There are exactly eight sender
states per Runtime, matching `RecordCount`, not an unbounded allocation.

For each accepted Transport handle:

1. Resolve `handle.address` through the already validated `BackingMap` for the
   handle's descriptor. Validate that the virtual address lies in the exact
   128-KiB span, the derived physical address lies in the recorded translated
   4-KiB page, and `MAA::core_addr(paddr) == handle.callbackPort`.
2. Create a `Request(vaddr, 64, flags, requestorId, capturedPC, capturedCID)`,
   set its registered region, and call `setPaddr` with the exact address derived
   from the committed page map. Request supports simultaneous virtual and
   physical addresses (`src/mem/request.hh:486-491,556-588,617-625,795-812`).
   Do not invoke the MMU again per cache line: all 32 pages were translated and
   committed under the v1 stable-mapping contract before Runtime mutation. A
   changed mapping is outside the v1 lifetime contract.
3. Convert `Transport::Command::ReadReq` to `Packet(req, MemCmd::ReadReq)` and
   `allocate()` a 64-byte dynamic response buffer. Convert `WriteReq` to
   `Packet(req, MemCmd::WriteReq)` and bind
   `dataStaticConst(handle.data)`; require `dataSize == 64`. Use WriteReq, not
   `WritebackDirty`, because Transport requires an authenticated WriteResp.
4. Push the fixed sender state. Set header/payload delay to zero before the
   cache-side timing call; the bridge's event time, xbar, cache, and memory
   provide modeled delay.
5. Call the exact `cacheSidePorts[handle.callbackPort]->sendTimingReq(pkt)`
   through a dedicated `sendLogicalPacket` wrapper. Never call
   `MAA::sendPacket`, `sendPacketCache`, or `MemSidePort`.
6. Call `Runtime::sendPrepared(true)` only if gem5 accepted. On false, retain
   Packet/Request/sender state and call `sendPrepared(false)` exactly once.

`MemSidePort` is rejected: it has two channels by default, not Transport's four
ports (`src/mem/MAA/MAA.py:138-140`), and its response path is also
unconditionally native (`src/mem/MAA/MemSidePort.cc:30-36`). `cache_sides` has
one coherent port per CPU (`configs/common/MAAConfig.py:413-425`) and is the only
legal first-bridge path for guest backing memory.

### Retry ownership

**DECISION.** Extend `CacheSidePort`'s current `BlockReason` state
(`MAA.hh:228-264`) with `RetryOwner {None, Native, Logical}` and add a separate
`logicalOutstandingCacheSidePackets`. Capacity checks use
`nativeOutstanding + logicalOutstanding`; the existing native counter is not
incremented by logical traffic.

Before calling Runtime `prepare`, the conservative v1 arbiter requires all four
logical cache ports to be locally below capacity and to have no downstream
retry owner. This is necessary because the callback port becomes visible only
after `prepare`, and a locally unavailable port cannot truthfully promise a
gem5 retry. One Runtime is selected round-robin; after its handle identifies a
port, the dedicated wrapper makes the real timing call.

If that call returns false, the port records the sole logical retry owner and
the exact Packet. `CacheSidePort::recvReqRetry` branches on the owner:

- Logical: call `Runtime::recvReqRetry(callbackPort)`, then retry the exact
  Packet from the next bridge issue event. A second false returns it to
  `WaitRetry`; a true calls `sendPrepared(true)`.
- Native: preserve today's `setUnblocked(CACHE_FAILED)` path
  (`CacheSidePort.cc:84-88,123-127`).

A native local-capacity wake or any response schedules both the native sender
and the logical drive arbiter, but does not forge `Runtime::recvReqRetry` unless
the logical Packet actually owned the downstream refusal. At most one refused
Packet exists per RequestPort.

### Response conversion and precommit ordering

**DECISION.** `CacheSidePort::recvTimingResp` first examines the top sender
state. A logical state branches before every native map/counter access. It
validates response kind, size, request vaddr/paddr, callback port, state magic,
record, epoch, action, and incarnations; decrements only
`logicalOutstandingCacheSidePackets`; pops the exact state; and transfers the
Packet to a fixed bridge response queue. A non-logical response follows the
current native path unchanged.

The response event creates `Transport::ReturnedHandle` as follows:

- Runtime identity/token pointers and logical virtual address come from the
  fixed sender state/RequestPacket;
- token record/epoch/action and incarnations come from the immutable captured
  fields and are cross-checked with the Transport handle;
- actual gem5 command maps `ReadResp` and `ReadRespWithInvalidate` distinctly,
  and `WriteResp` exactly;
- size/data come from the returned Packet; and
- before presenting the logical virtual address to Runtime, the adapter has
  already authenticated the Packet's actual physical address against the
  page map.

This two-address check is mandatory: Transport's exact wire comparison expects
its logical/virtual line address (`LogicalSPDCacheTransport.cc:583-606`), while
gem5 routes the translated physical address. Hiding an unchecked paddr behind
the virtual identity is a reject condition.

Runtime itself performs immutable precommit before mutation on both paths:

- write response: `precommitReceive`, Runtime correlation comparison, then
  authorized receive (`LogicalSPDCacheRuntime.hh:401-425`);
- fill delivery: `precommitDelivery`, correlation comparison, then authorized
  commit (`LogicalSPDCacheRuntime.hh:427-453`).

Transport copies a read response into its owned credit before returning a
ticket (`LogicalSPDCacheTransport.cc:643-702`), so the Packet and sender state
may then be deleted. Writeback data remains the Transport credit's immutable
snapshot from prepare until WriteResp releases the record
(`LogicalSPDCacheTransport.cc:448-484,882-913`).

### Bounded CopyHook

**DECISION.** CopyHook is not a user callback and does not copy data. The bridge
copy event installs a stack-local/fixed `CopyPermit {context, ticket,
expectedEventGeneration, armed}` and passes a `noexcept` static function. The
function performs only bounded comparisons, consumes `armed` once, and returns
true. It does not allocate, schedule, call Runtime, touch a Packet, or invoke
guest code. Transport then performs the one 64-byte copy and ACK
(`LogicalSPDCacheTransport.cc:757-805`). False, exception, re-entry, wrong
ticket, or a second invocation is an internal panic; Transport already poisons
reentrant/throwing mutation (`LogicalSPDCacheTransport.cc:94-117,781-798`).

## 3. Admission and ABI

### Source facts

**FACT.** The accepted logical ABI is deliberately marked non-integrated
(`include/gem5/maa_logical_spd_cache_abi.hh:1-6`). It uses the three high bytes
of word zero for source/destination logical IDs, requires logical source 2 to
be `0xff`, preserves the all-zero legacy physical encoding, and reuses opcode 8
(`maa_logical_spd_cache_abi.hh:19-29,124-174`). Word two must be `NoAddress`;
word three carries destination backing (`src/mem/MAA/CpuSidePort.cc:311-348,
350-388`).

**FACT.** Current decode validates the generic shape and registered destination
span, then unconditionally panics before any controller/IF/SPD mutation
(`CpuSidePort.cc:389-424`). This is the exact fail-closed stub to replace.

**FACT.** The generic ABI permits six datatypes and sixteen scalar operations
(`maa_logical_spd_cache_abi.hh:19-29,176-222`), and its destination validator
requires only datatype alignment plus a complete 16K-element span in one
registered region (`maa_logical_spd_cache_abi.hh:224-253`). Runtime accepts only
datatype 5 and six FP64 operations, and requires the 128-KiB base itself to be
128-KiB aligned (`LogicalSPDCacheSlice.hh:383-388,412-460,475-533`).

**FACT.** The guest helper already writes the accepted four-word logical
ALU-scalar form (`benchmarks/API/MAA_gem5.hpp:237-277`). There is no source
descriptor registration helper. General guest memory regions are registered as
virtual `[start,end)` spans through `m5_add_mem_region`
(`include/gem5/m5ops.h:67-68`; `src/sim/pseudo_inst.cc:558-569`), and MAA stores
those virtual ranges (`src/mem/MAA/MAA.cc:287-325`).

### Smallest legal v1 ABI

**DECISION.** Keep the accepted logical instruction wire image unchanged and
add one 16-byte noncacheable `LOGICAL_SOURCE_RANGE` immediately after
`VIRTUAL_PAGE_READY_RANGE`: two 64-bit slots indexed by logical ID. Expand
`AddressRangeType` with one real enum before `MAX` (current layout at
`src/mem/MAA/IF.hh:263-289`), append its `AddrRange` in
`configs/common/MAAConfig.py` after lines 245-252, and append the guest pointer
in `alloc_MAA` after `benchmarks/API/MAA_gem5.hpp:125-128`. The global command
region limit is 32 (`src/mem/packet.hh:75`), so ID 7 is legal and user backing
regions begin at ID 8.

Add:

```
maa_register_logical_source_fp64(logical_id, const double *base)
maa_mul_scalar_logical_fp64(src_logical, dst_logical,
                            double *destination, int scalar_reg)
```

The first helper performs one 64-bit MMIO write and waits for its timing
response; only then is the source descriptor registered. The second is a typed
wrapper over the already accepted `maa_alu_scalar_logical<double>` encoding and
operation 2. Destination registration is transactional with Runtime `admit`;
there is no second destination command and no partially registered destination.

The final word-three write is the high-level completion waiter. Its timing
response is delayed until all 2048 destination WriteResp packets are
authenticated. This avoids inventing a logical tile-ready ID and keeps logical
work out of `my_ready_pkts`/`my_ready_tile_ids`, whose existing path is physical
tile/page readiness (`CpuSidePort.cc:525-583`; `MAA.cc:1188-1216`). Software
cannot issue the next dependent instruction past the blocking MMIO store and
`mfence` in the helper.

### Validation before Runtime mutation

Validation is ordered in four phases. No call to `Runtime::registerSource` or
`Runtime::admit` occurs before all applicable phases pass.

**Phase A — wire/configuration, no provisional state:**

- request is an unmasked 8-byte WriteReq in the exact command offset, from a
  known requestor/context mapped to a valid `maa_id`;
- Runtime is initialized for that `maa_id`, geometry is four ports / 64-byte
  lines, cache-side port count is four, `m_tx_offset == 6`, and
  `m_core_addr_bits == 2` (`MAA::core_addr` is at `MAA.cc:459-462`);
- logical IDs are in `[0,2)`, source and destination differ, and the source
  command names a free descriptor;
- source/destination pointer is nonzero, not `NoAddress`, exactly 128-KiB
  aligned, has no unsigned overflow, and the full 128-KiB lies in one registered
  virtual region;
- no existing committed virtual or physical backing overlaps it;
- instruction passes the accepted generic validator, then the v1 restriction
  `datatype == FLOAT64_TYPE && optype == MUL_OP`; all physical operands and
  extra/conditional registers remain absent, word two is `NoAddress`, and the
  FP64 scalar register covers exactly two valid register words;
- bridge is not draining, translating, waiting, active, poisoned, sealed, or
  exhausted. Invalid guest ABI/configuration is `fatal` with no Runtime
  mutation; internal state mismatch is `panic`.

**Phase B — provisional full-span translation:**

The bridge holds the MMIO Packet in one fixed translation slot and translates
the start of each of the 32 4-KiB pages, one `BaseMMU::translateTiming` request
per event. It records virtual page, physical page, context, region, and mapping
generation. Each result must preserve the 4-KiB offset, have no fault, fit in
`Addr`, contain no duplicate physical page within the span, and preserve the
Transport/core callback bits. Destination physical pages must be disjoint from
all source pages. The next translation is scheduled at a clock edge even if
the current MMU calls `finish` immediately; the existing Stream/Indirect code's
immediate-translation assertion (`StreamAccess.cc:481-495` and
`IndirectAccess.cc:3263-3280`) is not copied.

Translation state is provisional adapter state, not Runtime state. On a fault,
the command terminates with `fatal` before Runtime mutation because an
asynchronous MMIO write has no defined guest-fault return channel. General
full-system demand paging/remapping therefore remains outside v1.

**Phase C — dependency/quiescence check:**

- registration requires no active Runtime operation/action;
- admission requires its source map committed in the same context/address
  space, destination map staged, no prior pending scalar write overlapping the
  two FP64 RF words, and no native IF instruction that can still write them;
- read the FP64 scalar bits once and capture them into Admission; later RF
  writes need no lease because Runtime stores `scalarBits` by value
  (`LogicalSPDCacheSlice.hh:112-120,522-529`);
- the first bridge does not push a logical Instruction into IF. IF classifies
  ordinary `ALU_SCALAR` as native ALU and applies physical tile hazards
  (`src/mem/MAA/IF.cc:335-449,500-570`); its logical lifecycle fields are
  explicitly inert today (`src/mem/MAA/IF.hh:159-209`).

Add a const `IF::usesRegisterSpan(maa_id, first, words)` query and a bounded
`MAA::logicalAdmissionDependenciesReady` query; do not reuse the current
single-word `IF::canPushRegister` as an FP64 proof
(`src/mem/MAA/IF.cc:451-467`). If busy, retain the bounded instruction assembly
entry and retry via `dispatchInstructionEvent`; do not mutate Runtime.

**Phase D — one atomic Runtime call:**

- source: call `registerSource(logical, {vbase, 128KiB}, 5)`, then commit the
  staged page map and return the source MMIO response;
- operation: capture scalar bits, call `admit({src,dst,{vbase,128KiB},5,Mul,
  scalarBits})`, then commit the destination map and move the word-three Packet
  to the one high-level waiter.

Runtime already validates all source fields before descriptor allocation
(`LogicalSPDCacheSlice.hh:412-460`) and all admission fields/serial capacity
before destination allocation (`LogicalSPDCacheSlice.hh:475-533`). `Busy`,
`Draining`, or `NotReady` means bounded retry without mutation. `Exhausted`
means fatal/no-wrap. `Invalid` after bridge validation or any
`ProductionStop/Poisoned` is an internal panic.

The successful logical branch is inserted in `MAA::dispatchInstruction` before
the physical status/IF path at `src/mem/MAA/MAA.cc:980-1035`. It erases and
deletes the assembly `Instruction` only after the bridge owns the completion
Packet, mirroring the current vector ownership cleanup at `MAA.cc:1079-1087`.

## 4. Scheduling

### Source facts

**FACT.** MAA already uses `EventFunctionWrapper` for issue/dispatch and native
port sends (`MAA.hh:485-519,834-835`; constructor bindings at
`MAA.cc:108-114`). Cache/memory send helpers schedule on a clock edge
(`src/mem/MAA/Port.cc:729-756`), while current instruction dispatch helpers use
raw `curTick()+latency` (`MAA.cc:1299-1331`); the new bridge must use clock edges
and `Cycles`, not copy the latter ambiguity.

**FACT.** Runtime `driveCompute()` is synchronous begin+execute
(`LogicalSPDCacheRuntime.hh:456-527`), and Datapath executes all 4096 FP64
elements in one host loop (`LogicalSPDCacheDatapath.hh:83-109`). Calling it from
admission or a zero-delay loop would publish unmodeled work.

### Event set and transition budget

**DECISION.** Add these MAA-owned EventFunctions. Every callback handles a
fixed hardware-width amount of work and returns; no callback loops until
Runtime becomes idle.

| Event | Work per invocation | Next scheduling cause |
|---|---|---|
| `logicalTranslateEvent` | Start/commit at most one 4-KiB MMU translation for one pending registration/admission. | MMU `finish` schedules next clock edge; after page 31 it schedules admission/registration dispatch. |
| `logicalDriveEvent` | Round-robin one Runtime; start at most one state transition and issue at most `logical_line_issue_width` packets, bounded by configured private read ports, Transport credits, and RequestPort availability. | Admission, action completion, a freed credit, retry wake, or next modeled issue cycle. |
| `logicalResponseEvent` | Authenticate and pass at most `logical_response_width` returned Packets to Runtime; enqueue fill tickets; delete consumed Packets/states. | CacheSidePort response arrival; reschedule next cycle if fixed queue remains nonempty. |
| `logicalCopyEvent` | Reserve private-slot write bandwidth and commit at most `logical_slot_write_ports` 64-byte delivery tickets whose ready tick has arrived. | Fill response or next private write-port availability. |
| `logicalComputeDoneEvent[maa_id]` | Call `executeCompute()` exactly once for the correlation previously accepted by `beginCompute`. | `logicalDriveEvent` schedules it after modeled page compute latency. |
| `logicalCompletionEvent[maa_id]` | Verify exact waiter/operation ID and four acknowledged pages; call `retireCompletedOperation`; schedule the held CPU Packet response. | Exact final writeback response makes `operationComplete()` true. |

Page advance is not a host-side counter. The exact final line ACK lets Runtime
authenticate the PageAction (`LogicalSPDCacheRuntime.hh:871-886`); Slice marks
the page published, increments page, and queues the next source page, or marks
the operation Complete (`LogicalSPDCacheSlice.hh:680-699`). The response/copy
event then schedules `logicalDriveEvent` at the next clock edge.

Backpressure wake is equally explicit: a downstream `recvReqRetry`, a response
that frees combined port capacity, or a delivery that frees a Transport credit
schedules the drive event. `NoCreditAvailable`, local port capacity, and
`RetryRequired` do not poll at zero time.

### Modeled bandwidth and latency

**DECISION.** The Runtime slots are private storage, so the bridge owns private
read/write busy-time arrays rather than calling public SPD data methods. Reuse
the MAA parameters `spd_read_latency`, `spd_write_latency`,
`num_spd_read_ports_per_maa`, `num_spd_write_ports_per_maa`,
`ALU_lane_latency`, and `num_ALU_lanes` (`src/mem/MAA/MAA.py:120-130`), but
maintain separate logical-slot port occupancy. This does not allocate a second
payload and does not steal an architectural SPD lane port.

- Each writeback `prepare` consumes one private 64-byte read-port opportunity;
  Packet issue cannot occur before that opportunity's ready tick. Runtime's
  early C++ snapshot is invisible and immutable until the modeled tick.
- Each fill `commitDelivery` consumes one private 64-byte write-port
  opportunity. The credit remains owned during the delay.
- After `beginCompute`, reserve page traffic and schedule completion after
  `Tread + Talu + Twrite`, where
  `Tread = ceil(512/read_ports) + read_latency - 1`,
  `Talu = ceil(4096/ALU_lanes) * ALU_lane_latency`, and
  `Twrite = ceil(512/write_ports) + write_latency - 1` cycles. This conservative
  non-overlapped v1 formula must be a named stat/config contract.
- `logical_line_issue_width <= read_ports`, response width and copy width are
  explicit parameters with positive finite defaults. No value zero means
  “unlimited.”

The full-page transform still executes once in C++ at `logicalComputeDoneEvent`,
but its visible state transition occurs only after the reserved modeled
latency. Timing comparison is forbidden until counters prove these reservations
and issue/copy widths were honored.

## 5. SPD mapping and honest storage accounting

### Source facts

**FACT.** Runtime's accepted contract says it owns exactly two private 32-KiB
payload slots (`LogicalSPDCacheRuntime.hh:17-22,726-763,918-927`).

**FACT.** The current architectural SPD independently appends four hidden
32-bit lanes per MAA — two FP64 slots — totaling 64 KiB/MAA
(`src/mem/MAA/LogicalSPDHiddenPayload.hh:15-38,64-105`). SPD includes those
lanes in `allocated_tile_count` and allocates/initializes them
(`src/mem/MAA/SPD.hh:31-55`; `src/mem/MAA/SPD.cc:260-330`). Public SPD access
rejects IDs outside `visible_tile_count` (`SPD.hh:57-99`). Accounting tests
expect one 65,536-byte private payload per MAA
(`experiments/tests/test_spd_hardware_accounting.py:17-42,46-62`).

### Decision

**DECISION.** The first bridge keeps Runtime's two typed `array<double,4096>`
slots and removes the appended hidden-lane allocation from `SPD`. It does not
map Runtime storage onto reserved architectural lane tiles.

Concretely, the storage-isolation patch removes
`LogicalSPDHiddenPayload.hh` from `SPD.hh`, restores
`allocated_tile_count == visible_tile_count`, and allocates only visible bytes
in `SPD.cc`. The layout helper may remain as an accounting-only header if tests
still need constants, but SPD and MAA must have no runtime accessor to hidden
lanes. Runtime instantiation in the same commit supplies the one real 64-KiB
private payload per `maa_id`.

The honest payload equation is therefore:

```
visible architectural SPD bytes
+ num_maas * LogicalSPDCacheRuntime::PrivatePayloadBytes (65,536)
```

The Runtime's 66,785-byte semantic lower bound includes that 65,536-byte term;
only 1,249 bytes are its packed control-state remainder. The bridge page maps,
Packets, sender states, event state, timing arrays, and stats are additional and
must be reported separately. They are not “free metadata.”

**REJECT.** Keeping both current hidden lanes and Runtime arrays, or counting
one while allocating both, fails storage review. Mapping Runtime onto SPD lanes
would require external PageSpan injection and removal/rebinding of Runtime's
owned arrays. That changes the accepted ownership contract and is a Runtime
redesign, not a bridge optimization; defer it until after the vertical slice.

## 6. Drain, checkpoint, reset, and panic

### Source facts and hard gap

**FACT.** Runtime drain requires Transport drained, Slice drained, no page or
compute correlation, and no abort request (`LogicalSPDCacheRuntime.hh:588-620,
662-671`). Transport drain includes no copy, action, FIFO, pending record,
record, or credit owner (`LogicalSPDCacheTransport.cc:971-985`). Slice drain
requires no active/refill/memory action, no miss/lease, and every slot Empty or
Clean (`LogicalSPDCacheSlice.hh:953-978`).

**FACT.** Neither `MAA.hh` nor any MAA source overrides `drain`, `drainResume`,
`serialize`, or `unserialize`. `SimObject::drain` therefore returns Drained
unconditionally, and base serialization is empty
(`src/sim/sim_object.hh:282-286,315-316`); `ClockedObject` serializes only its
own power state (`src/sim/clocked_object.hh:242-243`,
`src/sim/clocked_object.cc:59-66`). A live logical bridge cannot be merged while
that remains true.

### Drain protocol and exact quiescence predicate

**DECISION.** `MAA::drain()` closes logical/native admission, calls
`requestDrain()` on every Runtime, and returns `DrainState::Draining` until all
normal active work completes. Normal checkpoint drain does not abort an
operation. Existing events, MMU callbacks, RequestPort retries, responses,
copies, compute, and final completion continue to run. Completion calls
`signalDrainDone()` only when this conjunction is true for every context:

1. `Runtime::drained()` and not poisoned/sealed;
2. Transport action Free, FIFO empty, pending NoRecord, all eight records Free,
   all four credits free, copy inactive;
3. Slice has no active operation/refill/accepted PageAction/miss/lease, and both
   slots are Empty or Clean;
4. no Runtime page/compute correlation and no abort requested;
5. no provisional source/destination translation, `RequestPtr`, MMU callback,
   or staged page map;
6. no bridge-owned gem5 Packet or fixed sender state, no RequestPort logical
   retry owner, logical outstanding count zero, response/delivery queue empty;
7. no source-registration or high-level completion waiter;
8. no logical EventFunction scheduled and no private port/compute busy tick
   later than `curTick()`;
9. every CPU response queue containing a logical completion is empty; and
10. native MAA is independently quiescent: all functional units/IF entries and
    instruction/register/ready waiters are empty, native outstanding/deferred
    maps and per-unit packet sets/counters are empty, all native port blocks are
    clear, transparent controller inactive, and native issue/send/dispatch
    events unscheduled.

Item 10 requires explicit const `empty()/quiescent()` queries; do not infer it
from `allFuncUnitsIdle()`, which checks unit state but not every map/waiter/event
(`MAA.cc:463-484`; hidden native ownership is at `MAA.hh:485-519,756-839`).

`drainResume()` first verifies the same predicate, calls each Runtime's
`resumeAfterDrain`, reopens admissions, and schedules any already received
non-logical dispatch work. A status other than Accepted is an internal panic.

### Abort and dirty flush

**DECISION.** Explicit cancellation (not normal checkpoint drain) calls
`Runtime::abort(Caller)` and continues the same event engine. Transport cancels
queued/pending/delivery records but retains in-flight ownership until responses
return (`LogicalSPDCacheTransport.cc:926-969`). If a writeback action was
accepted, Runtime cancels it back to Dirty and immediately establishes the
mandatory abort-flush writeback; abort does not complete until its exact ACK
(`LogicalSPDCacheRuntime.hh:530-585,889-915`; Controller cancellation preserves
dirty obligation at `LogicalSPDCacheController.hh:643-674`). The high-level
waiter receives no success response until the defined cancel/fatal policy is
implemented. Teardown, reset, and checkpoint wait for abort completion and the
full quiescence predicate.

### Checkpoint contract

**DECISION.** Add a versioned `LogicalSPDCacheRuntime::QuiescentImage` plus
`exportQuiescent`/`importQuiescent`. The image is an explicit field format, not
`memcpy(sizeof(Runtime))` and not the packed ledger. Export is legal only after
the full bridge predicate above. Import validates the entire image into a
temporary value before mutating a fresh Runtime and rebinds all internal span
pointers from selectors.

Serialize:

- format version, geometry, `maaID`, initialized/draining state;
- Slice descriptor roles/handles/backing spans/generations, backing-ready and
  writeback-ACK masks, producer and operation high-water IDs;
- Controller descriptor generations/readiness, Empty/Clean slot phase/page
  identity/transaction metadata, inactive lease serial high-water values, and
  `lastMemorySerial`;
- both complete 32-KiB typed payload arrays (even if a slot is Empty, for a
  fixed deterministic image);
- Transport next action/incarnation IDs, exhaustion flags, remaining budgets,
  and all eight retained record epochs; assert free records, empty/zero
  credits/line buffers/action bitmaps rather than serializing live packets;
- bridge committed BackingMaps: virtual span, 32 physical page bases,
  context/address-space identity, registered region ID, map generation;
- private timing configuration and high-water stats needed for deterministic
  continuation. Slice counters are simulation instrumentation explicitly
  excluded from the semantic lower bound (`Runtime.hh:37-43,133-134`); either
  serialize them in the stats section or reset them by one documented global
  stats policy, never treat them as operational Runtime bytes.

Forbid at checkpoint and fatal if observed: active/dirty/reserved/filling/
writeback slot; operation/refill/action/correlation/abort; poisoned or sealed
Runtime; any translation, Packet, sender state, retry, response credit,
delivery ticket, waiter, queued completion response, or scheduled logical
event; non-quiescent native MAA. Do not serialize raw pointers, Packet,
RequestPtr, Event objects, sender-state stacks, CopyHook contexts, cache/xbar
coherence state, or a live MMU callback.

This permits checkpoint after source registration or after an operation: clean
resident pages, descriptors, payload, page maps, and monotonic ID state are
preserved. Active-work checkpointing is explicitly forbidden. Without the new
Runtime import API, the only safe policy is to fatal on any initialized logical
descriptor; an audit snapshot alone is not a restoration interface.

### Reset, teardown, and ProductionStop

`MAA::logicalReset(maa_id)` is legal only at full context quiescence. Call
Runtime `reset()` first; it cleans descriptors and zeros both payloads
(`LogicalSPDCacheRuntime.hh:605-620`; Slice cleanup at
`LogicalSPDCacheSlice.hh:1013-1030`). Then clear bridge page maps, waiters,
timing high-water state, and event generations. Preserve Runtime's monotonic
generation/serial/incarnation budgets as its reset methods do; reset must not
create an identity replay.

Guarded teardown is: close admission, drain, empty response queues, clear
bridge maps, call Runtime `teardown()`, require `sealed()` and
`destructionSafe()`, then destroy contexts. Runtime teardown cleans descriptors
and seals Slice/Transport (`LogicalSPDCacheRuntime.hh:622-641`).

After every Runtime call, one bridge helper classifies status:

- `ProductionStop`, `Poisoned`, failed exact completion, CopyHook violation, or
  impossible adapter state -> `panic` immediately with Runtime/bridge audit
  snapshot;
- bad configuration or guest ABI/translation fault -> `fatal` before Runtime
  mutation;
- Busy/NotReady/NoCredit/Retry/Draining -> modeled wait only where explicitly
  allowed;
- Exhausted -> fatal, never wrap or reconstruct IDs.

Poison is persistent and is never reset, serialized as a recoverable state, or
converted to a guest error. This is the gem5 realization of the Runtime header's
explicit ProductionStop requirement (`LogicalSPDCacheRuntime.hh:20-25,
765-807`).

## 7. Minimal patch sequence and rollback boundaries

Each future commit is independently reviewable. No native transport refactor is
part of this sequence.

### Commit 1 — quiescent Runtime image, host-only

Paths:

- `src/mem/MAA/LogicalSPDCacheRuntime.hh`
- `src/mem/MAA/LogicalSPDCacheSlice.hh`
- `src/mem/MAA/LogicalSPDCacheController.hh`
- `src/mem/MAA/LogicalSPDCacheTransport.hh/.cc`
- `tests/maa/logical_spd_cache_vertical_slice_test.cc`
- `tests/maa/logical_spd_cache_transport_test.cc`

Add atomic versioned quiescent export/import and corruption/round-trip tests.
Rollback removes only checkpoint API; standalone execution is unchanged. Run
the existing controller/transport/vertical host gates plus the new round-trip
test. Reject any import that can restore a live pointer or active record.

### Commit 2 — single storage authority and bridge skeleton

Paths:

- `src/mem/MAA/LogicalSPDCacheGem5Bridge.hh/.cc` (new)
- `src/mem/MAA/MAA.hh/.cc`, `src/mem/MAA/MAA.py`
- `src/mem/MAA/SPD.hh/.cc`
- `src/mem/MAA/LogicalSPDHiddenPayload.hh`
- `src/mem/MAA/SConscript`
- `experiments/analysis/spd_hardware_accounting.py`
- `experiments/tests/test_spd_hardware_accounting.py`
- `tests/maa/logical_spd_hidden_payload_test.cc`

Instantiate one Runtime/context per `maa_id`, remove SPD hidden allocation, add
private timing state and accounting assertions. No admission and no Packet path
yet; logical ABI remains fail-closed. Rollback restores old hidden allocation
and removes the unconnected context as one unit. Host accounting must prove one
and only one 64-KiB payload/MAA.

### Commit 3 — bounded source MMIO and pretranslation

Paths:

- `include/gem5/maa_logical_spd_cache_abi.hh`
- `benchmarks/API/MAA_gem5.hpp`
- `configs/common/MAAConfig.py`
- `src/mem/MAA/IF.hh/.cc`
- `src/mem/MAA/CpuSidePort.cc`
- `src/mem/MAA/MAA.hh/.cc`
- `src/mem/MAA/LogicalSPDCacheGem5Bridge.hh/.cc`
- `tests/maa/logical_spd_cache_abi_test.cc`
- `tests/maa/logical_spd_cache_bridge_host_test.cc` (new fake MMU/port seam)
- `experiments/tests/test_logical_spd_cache_abi_contract.py`

Add the two-entry source range, FP64/MUL typed helpers, static validators,
32-page pretranslation, map alias checks, RF-span dependency query, and held
MMIO response. Keep operation admission fail-closed at the final handoff.
Rollback removes the new range/helper without altering the accepted word0-3
format. Host tests cover immediate/delayed/faulting translation and prove no
Runtime mutation before all pages validate.

### Commit 4 — isolated RequestPort adapter

Paths:

- `src/mem/MAA/LogicalSPDCacheGem5Bridge.hh/.cc`
- `src/mem/MAA/MAA.hh/.cc`
- `src/mem/MAA/CacheSidePort.cc`
- `src/mem/MAA/SConscript`
- `tests/maa/logical_spd_cache_bridge_host_test.cc`
- `experiments/tests/test_logical_spd_cache_bridge_contract.py` (new)

Add fixed sender states, packet conversion, actual/virtual address
authentication, separate retry owner/outstanding count, response/delivery
queues, and bounded CopyHook. Still do not enable final instruction admission.
Rollback is limited to the dedicated sender-state branch; native send/response
logic must be textually and behaviorally unchanged. Fake-port tests cover false
send/retry, replaced response Packet, reordered responses, both legal read
responses, write-data lifetime, duplicate/stale/wrong-port responses, four
credits, and zero deltas in native maps/counters/tables.

### Commit 5 — one FP64 multiply event-driven vertical slice

Paths:

- `src/mem/MAA/LogicalSPDCacheGem5Bridge.hh/.cc`
- `src/mem/MAA/MAA.hh/.cc`, `src/mem/MAA/MAA.py`
- `src/mem/MAA/CpuSidePort.cc`
- `benchmarks/API/MAA_gem5.hpp`
- `tests/maa/logical_spd_cache_bridge_host_test.cc`
- `benchmarks/` one exact-output logical FP64 smoke source

Replace only the fail-closed stub with bridge dispatch, capture scalar, add the
six EventFunctions/timing reservations, hold final word-three Packet, and
complete only after fourth page publication. Rollback reinstates the single
fail-closed branch; source registration may remain inert. Host event tests use
a fake event clock and prove no zero-time page/operation completion.

### Commit 6 — MAA drain/checkpoint/reset/teardown

Paths:

- `src/mem/MAA/LogicalSPDCacheGem5Bridge.hh/.cc`
- `src/mem/MAA/MAA.hh/.cc`
- `src/mem/MAA/IF.hh/.cc`
- `src/mem/MAA/CacheSidePort.cc`, `CpuSidePort.cc`, `MemSidePort.cc`
- affected native unit headers for const `quiescent()` only
- `tests/maa/logical_spd_cache_bridge_host_test.cc`

Implement the exact predicate, Runtime image serialization, page-map state,
poison mapping, response-queue checks, and native quiescence audit. Do not add
native live-state serialization: checkpoint remains quiescent-only. Rollback
must disable logical admission too; a live bridge without correct drain is not
an acceptable intermediate product. Host tests cover drain during translation,
retry, delivery, compute, final response, explicit dirty abort-flush, reset,
teardown, snapshot corruption, and restore.

### Commit 7 — mechanism stats and staged validation assets

Paths:

- `src/mem/MAA/LogicalSPDCacheGem5Bridge.hh/.cc`
- `src/mem/MAA/MAA.hh/.cc`
- the exact-output smoke and matrix runner under `benchmarks/` and
  `experiments/scripts/`
- analysis parser/tests under `experiments/analysis/` and
  `experiments/tests/`

Add only namespaced logical counters and matrix tooling. No timing conclusion
is allowed in this commit. The user-approved gem5 smoke is a later execution
gate, not part of implementation review.

## 8. Validation matrix

No validation described here was run while writing this report.

### Gate A — host bridge tests first

Run the accepted ABI/controller/transport/vertical tests and the new bridge
host test without building gem5. The bridge seam supplies fake MMU,
RequestPort, clock/event, and CPU completion queue objects. Required cases:

- complete guarded 16K FP64 multiply with exact byte oracle and canaries;
- 32-page source/destination translation, delayed callbacks, physical aliases,
  wrong context, page fault, overflow, and 128-KiB alignment;
- false send/repeated retry, four-credit pressure, eight-record/FIFO bounds,
  response reordering, response Packet replacement, wrong callback port,
  duplicate/stale identity, and read-invalidate response;
- delayed CopyHook, false/throw/reentrant/duplicate hook, exact copy-before-ACK;
- compute event cannot execute before its modeled ready tick;
- final CPU response cannot precede final destination WriteResp;
- drain at every adapter state, dirty abort-flush, reset/teardown, and
  quiescent-image round trip/corruption;
- snapshots of native maps/deferred queues, Row/Offset/Request tables, native
  per-unit counters, and native aggregate port counters remain unchanged by
  every logical Packet.

### Gate B — one compile/object gate

After every host gate passes, use one SCons invocation that compiles only the
affected X86 objects, for example:

```
scons -j<N> \
  build/X86/mem/MAA/LogicalSPDCacheGem5Bridge.o \
  build/X86/mem/MAA/LogicalSPDCacheTransport.o \
  build/X86/mem/MAA/MAA.o \
  build/X86/mem/MAA/CpuSidePort.o \
  build/X86/mem/MAA/CacheSidePort.o \
  build/X86/mem/MAA/SPD.o \
  build/X86/mem/MAA/IF.o
```

No simulator binary build or run is part of this gate. Warnings, missing source
registration in SConscript, or a dependency that requires native transport
changes fail the gate.

### Gate C — one explicit user-approved gem5 smoke

Only after separate user approval, build the required gem5 target and run one
exact-output smoke: aligned 16,384-double source/destination with guard pages or
canaries, finite/edge FP64 patterns, scalar multiply, one source registration,
one logical admission, and wait on the final instruction write response.
Compare every destination bit and both canary regions against the host oracle.
The smoke is a correctness/mechanism gate, not a performance result.

Before considering timing, require these exact counters for one operation:

| Counter/invariant | Required value |
|---|---:|
| source registrations / admissions / high-level completions | `1 / 1 / 1` |
| page fills / computes started / computes completed / writebacks / pages completed | `4 / 4 / 4 / 4 / 4` |
| refill completions / rejects / aborts / poisons | `0 / 0 / 0 / 0` |
| logical read requests / read responses / CopyHook commits | `2048 / 2048 / 2048` |
| logical write requests / WriteResp | `2048 / 2048` |
| source + destination page translations | `32 + 32`, no fault/alias |
| maximum Transport credits / records / FIFO | `<=4 / <=8 / <=8` |
| live Packets, sender states, retry owners, tickets, waiters at end | all `0` |
| page order | exactly `0,1,2,3`; no page advances before exact final action ACK |
| completion ordering | final CPU response tick strictly after/equal scheduled edge following final WriteResp authentication |
| native logical-packet deltas | outstanding/deferred map inserts `0`; Request/Row/Offset claims `0`; native indirect/stream/port response counter increments `0` |

Every refused downstream send must have exactly one later retry grant before
the next attempt; accepted+returned Packet counts must balance. The adapter
must separately report actual paddr/virtual identity validation and private
read/write/ALU busy cycles.

### Gate D — matched four-way mechanism/timing matrix

After the smoke passes, run the same binary/data/scalar/operation and matched
CPU/cache/memory/channel/clock configuration for:

1. `native16`: one native 16K FP64 source load, scalar multiply, and destination
   store using 16K physical capacity;
2. `logical16-on-4K`: the logical Runtime with four private 4K pages and 4K
   visible physical-tile capacity;
3. `native4Kx4`: four ordered native 4K FP64 load/multiply/store phases on the
   same page partition;
4. a host/reference run used only for exact output hashes, not timing.

Use identical 128-KiB aligned backing arrays, initial cache state, scalar bits,
memory mapping, and output/canary oracle. Report two timing intervals rather
than mixing scopes:

- execution/data-motion: first modeled source-line request through final
  destination response;
- end-to-end: source registration/load setup through completion-bearing MMIO
  response, with translation/setup cycles reported separately.

All four outputs and all mechanism invariants must pass before comparing
cycles, bandwidth, speedup, or energy proxies. A run with a wrong line count,
native-map contamination, unmatched memory traffic, incomplete output, retry
leak, or different measurement boundary is invalid rather than “slow.”

## 9. Blockers, reject criteria, and unresolved questions

### Source-contract blockers that must be resolved before enabling admission

1. **No source registration path.** The accepted instruction carries only
   destination backing; add the bounded two-entry MMIO range.
2. **ABI/Runtime mismatch.** Generic ABI allows 6 datatypes/16 operations and
   natural alignment; Runtime allows FP64 six operations and mandates a
   128-KiB-aligned 128-KiB span. V1 must reject everything except FP64 MUL and
   enforce the stronger alignment before Runtime mutation.
3. **Virtual/physical address split.** Guest pointers and registered regions are
   virtual, while cache-side Packets route by paddr. Runtime authenticates one
   64-bit address. The adapter therefore needs prevalidated page maps and must
   authenticate both vaddr and paddr; passing a raw guest pointer as paddr is
   incorrect.
4. **Fixed port geometry.** Transport is compile-time four ports / 64-byte line
   and hashes `(address >> 6) & 3`
   (`LogicalSPDCacheTransport.hh:17-29`; implementation at
   `LogicalSPDCacheTransport.cc:165-169`). V1 must fatal unless four coherent
   cache ports, 64-byte lines, `m_tx_offset==6`, and paddr callback mapping all
   agree.
5. **Double private storage.** Runtime arrays plus current SPD hidden lanes
   allocate twice the claimed payload. Remove one authority before Runtime is
   live.
6. **No MAA drain/serialization.** Inherited no-op drain/serialize makes live
   checkpoint and safe destructor impossible. Commit 6 is an admission gate.
7. **No Runtime import/export.** Audit snapshots are insufficient to restore
   generations, serials, payload, and budgets. Add quiescent image API or fatal
   every checkpoint with initialized descriptors.
8. **Synchronous datapath.** `executeCompute` changes 4096 values immediately.
   It must be separated from `beginCompute` by a modeled EventFunction delay.
9. **No logical IF dependency semantics.** Inserting the accepted opcode into
   current IF routes it to native ALU and physical hazards. Direct bridge
   admission plus a read-only RF-span dependency check is required.
10. **No repeated descriptor lifecycle.** After completion,
    `retireCompletedOperation` clears only the active operation
    (`LogicalSPDCacheSlice.hh:903-919`); source and destination descriptors stay
    allocated. Runtime exposes descriptor cleanup only through reset/teardown,
    not a reusable publish/promote/rebind command. V1 therefore guarantees one
    source-to-destination operation per Runtime reset. A multi-operation chain
    or destination-as-next-source is a Runtime API redesign.
11. **Finite identities.** Action IDs, incarnations, record epochs, descriptor
    generations, operation IDs, producer transactions, and memory serials are
    finite and intentionally do not wrap (`LogicalSPDCacheTransport.cc:268-295,
    319-337`; `LogicalSPDCacheSlice.hh:435-456,504-513`). Exhaustion is fatal,
    not a reset opportunity.
12. **Asynchronous fault channel.** Once a completion-bearing MMIO write is
    accepted there is no defined architectural way to inject a later page
    fault into that store. V1 pretranslates all pages before Runtime mutation
    and fatals a translation fault; general demand paging requires a new ABI.

### Hidden state outside the 66,785-byte lower bound

The lower bound does not include, and implementation/accounting must separately
name:

- Slice's 12 saturating 64-bit counters (96 bytes) — expressly excluded;
- two 32-entry physical page maps plus virtual span, context/ASID, region,
  validity, and generation metadata;
- gem5 `Request`/`Packet` objects and their dynamic read buffers;
- eight fixed sender states per Runtime, response and delivery queue entries,
  translation slot, registration waiter, high-level waiter, retry-owner state,
  logical outstanding counts, and CPU response queue occupancy;
- EventFunction scheduling bits/ready ticks/generations and private read/write/
  ALU busy-time arrays;
- requestor ID, PC/CID capture, configuration, logical stats, tracing, and
  checkpoint version/section metadata;
- external TLB, page-table, cache, xbar, coherence, and memory-controller state;
- ordinary C++ padding/alignment and pointer representation (the ledger uses
  selectors, not host virtual addresses).

**REJECT.** Any area claim that calls these fields part of 66,785 bytes, or
uses the old 66,181-byte Python model as the accepted Runtime total, is false.

### Conditions requiring redesign rather than an adapter

- arbitrary core/port count or non-64-byte cache lines;
- Runtime payload stored in architectural SPD lanes;
- non-128-KiB-aligned backing, mixed types, comparisons, vector-vector/RMW, or
  more than the six accepted FP64 operations;
- concurrent operations, descriptor eviction/reuse, or chained logical
  destinations without reset;
- page remapping, demand faults, process migration, or full-system operation
  without a pin/unpin and fault-completion ABI;
- checkpoint with live Packet/MMU/Event/dirty state;
- routing logical traffic through native coalescing/deferred maps, native
  Row/Offset/Request tables, native retirement caches, or native aggregate
  response counters;
- treating a CopyHook as an unbounded software callback or publishing a page
  outside Runtime's precommit authority.

### Unresolved beyond the first slice

**QUESTION.** Should a later ABI add explicit logical status/error reads instead
of using a completion-bearing final instruction store and fatal-on-fault?

**QUESTION.** What architectural page-pinning/ASID contract is required for
full-system mode? V1's staged page map is appropriate only while mappings are
stable for the operation lifetime.

**QUESTION.** After correctness, should private slot bandwidth share physical
SPD ports or remain an independent cache bank? V1 chooses independent, named
ports because storage is private; changing that contention model requires a
separate timing study, not an invisible constant change.

**QUESTION.** What Runtime lifecycle should support repeated operations:
explicit descriptor release, destination promotion, or more descriptors? The
adapter must not invent one by reset because reset discards all cached logical
state.

## Implementation-ready acceptance checklist

The bridge is ready for the first user-approved gem5 smoke only when all are
true:

- one Runtime and one 64-KiB private payload per `maa_id`, no SPD duplicate;
- exact accepted word0-3 ABI plus bounded source MMIO, FP64 MUL only;
- all virtual/physical pages and scalar dependencies validated before Runtime
  mutation;
- dedicated cache-side sender state branches before native code on issue,
  retry, and response;
- logical counters prove zero native map/table/counter ownership;
- EventFunctions model translation, line issue/response/copy, compute, page
  advance, retry wake, and completion with no zero-time completion loop;
- full quiescence drain, versioned quiescent checkpoint round trip, reset, abort
  dirty flush, ProductionStop panic, and guarded teardown pass host tests;
- exact mechanism counters pass before any timing result is reported; and
- gem5 build/run remains a separate, explicit user-approved action.
