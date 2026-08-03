# Logical SPD-cache to gem5/DX100 bridge design

Date: 2026-08-03

Accepted standalone baseline: `46f36d632b8af9b20e365962656d089feaec9262`
(`mem: finalize standalone logical SPD cache contract`)

Reviewed report commit: `66564b83500b6d28df134136cc9ec37ca271cc5d`

Independent verdict:
`/data1/nier/worktrees/codex-coordination/sessions/logical-spd-cache-gem5-bridge-design-review-20260803-054226-6424724c/session.json`

Scope: repaired implementation design only. No production source was changed,
built, or run while repairing this report.

## Executive decision

**DECISION — repaired design candidate, not evidence.** Proceed only with the
bounded bridge series below. The smallest honest first slice is **X86 syscall
emulation (SE) only**. The DX100 launch surface uses
`configs/deprecated/example/se.py`, which constructs `Root(full_system=False)`
at `configs/deprecated/example/se.py:309-315`; representative DX100 runners
select that file at `benchmarks/gapbs/run_gapbs_tile_smoke.sh:13` and
`benchmarks/UME/run_ume_tile_smoke.sh:11`. V1 rejects full-system (FS) mode
before logical registration. It does not give MAA a private timing MMU and does
not claim a stable virtual-to-physical mapping.

The accepted standalone Runtime remains the sole logical Slice/Controller/
Transport/Datapath authority. One Runtime exists per `maa_id`; its two typed
32-KiB arrays are the only logical payload. The bridge adds bounded adapter
state, a shared native/logical cache-port arbiter, SE address-space
authentication, finite MMIO/CPU-response ownership, modeled private-slot
timing, and lifecycle guards. None of that adapter state is included in the
accepted 66,785-byte Runtime semantic lower bound.

**DECISION — eight review closures.**

| Review issue | Repaired v1 closure |
|---|---|
| 1. Translation/MMU lifecycle | SE-only; resolve the command owner through `Request::contextId()` and `System::threads`, use that `ThreadContext`'s `Process::pTable`, retranslate and authenticate every data Packet, and treat any changed lookup/owner/region as stale. FS is rejected. |
| 2. Retry/fairness | One per-cache-port `DownstreamState` owns a refused request or granted retry for exactly one native or logical Packet. Local capacity is a separate condition. Class and `maa_id` round-robin cursors advance only on accepted sends. |
| 3. Checkpoints | `MAA::serialize`/`unserialize` chain `ClockedObject`; v1 accepts only construction-pristine MAA/logical state, matching the checkpoint-before-`alloc_MAA()` workflow. Every non-pristine checkpoint fails closed; no Runtime image is claimed to restore native MAA/SPD/RF state. |
| 4. Lifecycle ordering | SConscript/build closure, single storage ownership, drain, pristine-checkpoint guard, teardown, ProductionStop mapping, and lifecycle host tests land while the guest logical path remains fail-closed. Admission is enabled only in the penultimate implementation commit. |
| 5. MMIO/range/RF hazards | One command slot and one held completion-bearing operation per `maa_id`; one registration waiter per `maa_id`, never concurrent with its operation waiter; command-range IDs and guest region IDs are kept distinct; CPU request-retry/response ownership is finite; owner tuple and the complete two-word RF span are checked before scalar capture. |
| 6. Timing/hidden state | Every adapter pool, waiter, event field, retry slot, timing port, and stat is bounded in a separate bridge-state ledger. The synchronous host datapath executes only at the modeled compute-completion event after explicit private read, ALU, and private write reservations. |
| 7. Payload transition | Hidden SPD allocation removal and Runtime instantiation are one atomic commit. All allocator, object, source-contract, accounting, optimized, and sanitizer gates change in that same commit. No intermediate tree has zero or two logical payloads. |
| 8. Performance matrix | Only a 4Kx4 native/logical pair with equal SRAM, ports, credits, traffic, setup boundary, final durability, software work, hashes, and oracle is timing-eligible. `native16` remains a capacity-unmatched diagnostic. Mechanism counters precede timing, and the first gem5 run still requires explicit user approval. |

**REJECT.** Do not begin implementation if any closure above is weakened into
an assumption, an unbounded host container, or a comparison with a different
completion boundary. Nothing in this report is live correctness, performance,
energy, or area evidence.

## Evidence convention and accepted baseline

- **FACT** records source at baseline `46f36d6` with a `file:line` anchor.
- **DECISION** is a mandatory v1 implementation choice.
- **QUESTION** is deliberately unresolved beyond v1.
- **REJECT** is a fail-closed review or runtime condition.

**FACT.** Runtime owns Slice, Transport, Datapath, and exactly two private
32-KiB payload slots (`src/mem/MAA/LogicalSPDCacheRuntime.hh:17-25,281-321,
918-927`). Slice geometry is two descriptors, four pages, two slots, 4096 FP64
elements/page, 32 KiB/page, and 128 KiB/backing
(`src/mem/MAA/LogicalSPDCacheSlice.hh:24-33,90-167`). Transport is fixed at four
ports, eight records/FIFO entries, four response credits, and 64-byte lines
(`src/mem/MAA/LogicalSPDCacheTransport.hh:17-29,145-180,369-385,464-489`).

**FACT.** The packed Runtime ledger is a semantic lower bound, not
`sizeof(Runtime)`, a checkpoint format, or an area result. It encodes host
pointers as selectors and excludes instrumentation
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:34-43`). Its accepted total is 534,275
bits / 66,785 bytes, including exactly 65,536 payload bytes
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:239-255`).

**DECISION.** The first slice remains deliberately narrow: FP64 scalar multiply
only (`ALU_SCALAR`, datatype 5, operation 2), logical source 0, destination 1,
four ordered 4K-element pages, and one operation per Runtime reset. General
types, comparisons, repeated descriptor reuse, chaining, demand paging, and FS
are outside v1.

## 1. Ownership and the legal SE address contract

### 1.1 Source facts

**FACT.** `MAA` is one `ClockedObject` with a configurable `num_maas`, one
shared SPD/RF/IF, and arrays of native functional units
(`src/mem/MAA/MAA.hh:54-55,271-285,420-455`; construction at
`src/mem/MAA/MAA.cc:52-114,149-183`). It has a configured `BaseMMU *mmu`, but
that is not ownership of each process address space (`src/mem/MAA/MAA.hh:451-
455`; `src/mem/MAA/MAA.py:157-163`).

**FACT.** In SE, the authoritative mapping belongs to `Process::pTable`
(`src/sim/process.hh:169-184`). `ThreadContext` exposes `contextId()`,
`getMMUPtr()`, `getSystemPtr()`, and `getProcessPtr()`
(`src/cpu/thread_context.hh:121-149`). `System::threads[ContextID]` resolves the
current context and exposes a bounds-checking `size()`
(`src/sim/system.hh:180-189,286`). `EmulationPageTable` exposes
`lookup`, `pageSize`, `translate(vaddr,paddr)`, and `translate(RequestPtr)`
(`src/mem/page_table.hh:53-115,137-189`); the Request form sets paddr or returns
a page-table fault (`src/mem/page_table.cc:132-168`). Mapping flags explicitly
identify uncacheable and read-only pages (`src/mem/page_table.hh:90-101`).

**FACT.** `Request` can carry simultaneous vaddr/paddr, requestor ID, context
ID, PC, task ID, and signed region ID (`src/mem/request.hh:372-451,486-491,
533-588,795-918`). `Packet(RequestPtr, MemCmd)` snapshots paddr and region from
the Request (`src/mem/packet.hh:865-904`). Cache region accounting requires
Packet and Request region IDs to match and be in `[0, MAX_CMD_REGIONS)`
(`src/mem/cache/base.hh:1068-1077`; `MAX_CMD_REGIONS == 32` at
`src/mem/packet.hh:67-75`).

### 1.2 Sole owners

**DECISION.** Add `LogicalSPDCacheGem5Bridge` as one MAA-owned object. It owns a
fixed vector of `num_maas` contexts. Each context owns exactly one Runtime, one
SE owner tuple, two non-authoritative page fingerprints, fixed adapter pools,
one MMIO slot, and timing/event state. Runtime remains sole descriptor, slot,
lease, page-action, payload, and completion authority. The bridge exposes only
read-only audit snapshots; Runtime already forbids direct Slice publication
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:17-25,676-724,810-868`).

The owner tuple captured from the first valid registration command is:

```
{ ContextID cid, ThreadContext *tc, Process *process,
  EmulationPageTable *pTable, pTable->pid(), RequestorID requestor,
  cpuId, maaId, taskId, commandPC, addrRegionGeneration }
```

The pointer fields are simulator identity only; they are never serialized or
counted as hardware bits.

**DECISION.** Owner authentication is exact and repeatable:

1. The incoming command Request must have a valid context ID and PC. Require
   `cid < system->threads.size()`, then resolve the live
   `tc = system->threads[request.contextId()]`; require `tc` equal the captured
   pointer, `tc->contextId()` equal, `tc->getProcessPtr()` non-null, and
   `tc->status()` neither Halting nor Halted. Re-fetch `Process *` and `pTable`
   through that live `tc` on every later authentication; never dereference a
   captured process/page-table pointer first.
2. Require `request.requestorId() == tc->getCpuPtr()->dataRequestorId()`;
   `BaseCPU` exposes its CPU ID and unique data requestor ID at
   `src/cpu/base.hh:180-189`. Set `cpuId = tc->getCpuPtr()->cpuId()` and
   `maaId = cpuId % num_maas`. The command's explicit `maa_id` aperture must
   equal this value. Arrival order in `my_RID_to_core_id` is not authority; its
   current first-seen mapping is at `src/mem/MAA/CpuSidePort.cc:193-215`.
3. Bind one owner tuple per `maa_id` until `logicalReset`. Later registration,
   instruction-word, and completion Packets for that `maa_id` must match cid,
   tc, process, pTable/pid, requestor, cpuId, and taskId. Another context—even
   one sharing the Process page table—gets a guest-fatal rejection before
   Runtime mutation.

Every incoming registration or instruction MMIO Packet is authenticated before
ownership transfer: non-null Request; expected WriteReq/size; present vaddr and
paddr with no overflow; `Packet::getAddr() == Request::getPaddr()`; exact
configured aperture and offset; Packet/Request region equality and the helper's
expected MMIO region/generation; and the owner requestor, context, task, and PC.
The CPU timing path owns MMIO translation; the bridge neither reconstructs nor
changes its paddr. Before returning an immediate or final held response, the
CPU-response arbiter repeats those fields, exact RequestPtr/Packet identity,
live owner, original CPU response port, and response command. Data Packets use
the separate fresh SE page-table contract in section 1.3.

**REJECT.** Requestor arrival order, `core_id` port index alone, a global MAA
MMU pointer, or a raw guest pointer is not an address-space identity.

### 1.3 SE translation, faults, staleness, and lifetime

**DECISION.** V1 requires `!FullSystem`, `pTable != nullptr`, and
`pTable->pageSize() == 4096` during bridge initialization. FS admission is
guest-fatal before any descriptor exists. The bridge never calls
`BaseMMU::translateTiming` for logical traffic and never advertises a TLB,
walker, pin, ASID, or invalidation contract it does not implement.

Registration and admission perform an early-fault/alias fingerprint pass over
the 32 page starts in each 128-KiB span. A fixed event validates at most one PTE
per invocation and charges at least one configured bridge lookup cycle. It uses
the owning `pTable->lookup`/`translate(RequestPtr)`, rejects a missing PTE,
rejects a read-only destination, carries `Uncacheable` into Request flags, and
records `{vpage, ppage, PTE flags}`. Source and destination physical pages must
be internally unique and mutually disjoint. The pass is provisional until
`registerSource`/`admit` accepts.

**DECISION.** A committed fingerprint is a detector, not a stable translation
cache. Immediately before **every** 64-byte data send, the bridge:

1. re-authenticates the complete owner tuple and registered-region generation;
2. requires the line's vaddr and size to lie in exactly the captured registered
   region and descriptor span;
3. constructs `Request(vaddr, 64, flags, requestor, commandPC, cid)`, copies the
   captured `taskId`, and calls `setRegion(regionID)`;
4. calls the same owning `Process::pTable->translate(RequestPtr)` and applies
   the current PTE cacheability/read-only checks; and
5. requires the translated page and offset to equal the committed page
   fingerprint before creating the Packet.

The per-line revalidation has an explicit positive `se_revalidate_latency` and
one fixed revalidation slot per Runtime; it is not reported as MMU/TLB timing.
The matched baseline pays the same bridge lookup schedule.

**DECISION.** Before a response mutates Runtime, authenticate all of:

- logical sender magic, `maa_id`, adapter slot, adapter epoch, action/record
  identity, callback port, and expected response command;
- Packet address/size/region and Request vaddr/paddr/size/region;
- Request requestor ID, context ID, PC, task ID, and exact RequestPtr captured
  by the fixed sender state; and
- a fresh owner/PTE lookup whose paddr still equals the per-Packet captured
  paddr and committed page fingerprint.

This check occurs even if the cache returns a replacement Packet. Only after it
passes does the adapter construct Runtime's virtual-address `ReturnedHandle`.
Runtime then performs its own immutable precommit correlation
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:401-453`).

**DECISION.** A fingerprint becomes stale on the first observed PTE/paddr/flag
change, registered-region generation change, context replacement, process or
pTable/pid change, owner status Halting/Halted, or owner-field mismatch. No
later Packet may be issued and no response may commit. Because the accepted
MMIO ABI has no asynchronous fault return, a pre-admission fault is guest-fatal
without Runtime mutation; a post-admission fault/stale map is fatal with an
audit snapshot and terminates the simulation. It is never repaired by silently
using the old paddr or by rebuilding Runtime identity.

**REJECT.** There is no v1 pin/unpin API, page-table mutation callback, TLB
shootdown listener, migration support, or FS fault completion. Consequently v1
does not promise mapping stability across an operation; it promises fresh
per-Packet SE ownership and mapping authentication and fail-closed detection.

**QUESTION.** A later FS design must identify the IOMMU/MMU owner, translation
requestor, fault delivery ABI, ASID/PCID, page pin lifetime, TLB invalidation and
remap callbacks, process migration behavior, and timing walker resources before
FS can be admitted.

## 2. Cache RequestPort adapter and retry arbitration

### 2.1 Source facts and packet conversion

**FACT.** The current cache response path is native-only: it calls
`MAA::recvTimingResp`, decrements the native aggregate, and deletes the Packet
(`src/mem/MAA/CacheSidePort.cc:30-40`). Native response handling requires paddr
in `my_outstanding_pkt_map` and routes only native indirect/stream ownership
(`src/mem/MAA/Port.cc:698-728`). Native issue similarly mutates the native
outstanding/deferred maps (`src/mem/MAA/Port.cc:48-79`; storage at
`src/mem/MAA/MAA.hh:763-825`).

**FACT.** A false `sendTimingReq` obligates the RequestPort to wait for
`recvReqRetry` before reissuing (`src/mem/port.hh:234-247`). Current
`CacheSidePort::sendPacket` conflates local outstanding capacity and a
downstream false into `BlockReason`, and `recvReqRetry` blindly clears
`CACHE_FAILED` (`src/mem/MAA/CacheSidePort.cc:84-113,123-139`). That state is
insufficient for two traffic classes.

**FACT.** Current Transport `prepare` consumes IDs/credit, pops FIFO, snapshots
write data, and changes a record to `PendingSend` before the downstream result
is known (`src/mem/MAA/LogicalSPDCacheTransport.cc:395-491`). A false send then
changes it to `WaitRetry` (`src/mem/MAA/LogicalSPDCacheTransport.cc:494-520`). That API does
not satisfy the stricter bridge rule that downstream refusal must not mutate
logical state.

**DECISION.** Extend Runtime/Transport with a fixed, two-phase adapter seam:

- `previewLine()` is non-mutating and returns one by-value candidate tied to a
  free record/credit/ID budget and a read-only 64-byte write snapshot. It does
  not advance IDs, FIFO, issued bits, action state, Slice, or payload.
- `commitAcceptedLine(preview)` is called in the same event callback only after
  `sendTimingReq` returns true. It atomically consumes the previewed record,
  credit, IDs, snapshot, and action correlation. A mismatch is ProductionStop.
- A false send leaves Runtime/Transport byte-for-byte unchanged. The bridge
  retains the exact provisional Packet/Request/sender slot until its retry.

There is one provisional slot per Runtime, drawn from the same eight adapter
Packet/sender slots; a preview is unavailable when all eight are committed.
Host tests snapshot every Runtime audit field before a false send and require
no delta. Timing responses must not be delivered inside the `sendTimingReq`
call; a fake peer that attempts re-entry is rejected before this seam can be
used.

For an accepted candidate, convert ReadReq to `Packet(req, MemCmd::ReadReq)`
with a fixed 64-byte adapter response buffer; convert WriteReq to
`MemCmd::WriteReq` with a non-owning pointer to the fixed preview snapshot. Use
WriteReq, not WritebackDirty, because Runtime requires an authenticated
WriteResp. Push one fixed `LogicalPacketSenderState` containing:

```
magic, maaID, adapterSlot, adapterEpoch, transportRecord, recordEpoch,
actionID, requestIncarnation, callbackPort, virtualLine, physicalLine,
RequestPtr identity, regionID, ownerEpoch, mapFingerprintGeneration
```

There are exactly eight sender, Packet, Request, and fixed 64-byte adapter data
slots per Runtime. A data slot is a read-response buffer or an unaccepted
write-preview snapshot, never both; after acceptance the write snapshot moves
into the already-accounted Transport record. No adapter Packet uses
`MAA::sendPacket`, native Request/Row/Offset tables, native maps, or native
aggregate response counters.

### 2.2 One exact per-port state machine

**DECISION.** Replace cache-side `BlockReason` as protocol authority with one
state per physical cache RequestPort:

```
DownstreamState = Open
                | WaitNative(nativePacketIdentity)
                | GrantNative(nativePacketIdentity)
                | WaitLogical(maaID, adapterSlot, adapterEpoch)
                | GrantLogical(maaID, adapterSlot, adapterEpoch)
```

It contains exactly one discriminated owner; it cannot encode two waiters.
`acceptedOutstanding < maxOutstandingCacheSidePackets` is a separate derived
local-capacity predicate. Per-port state also contains one class round-robin bit
and one `nextLogicalMaa` cursor.

| Event/state | Required transition and ownership |
|---|---|
| Native or logical ready, `Open`, capacity available | Present one candidate selected by class RR; within logical, scan at most `num_maas` from `nextLogicalMaa`. No other caller may invoke `sendTimingReq` on that port. |
| Local capacity full | Do not call downstream, do not create a retry owner, and do not mutate the logical preview. Leave the native candidate in its existing native owner or the logical work unpreviewed; response progress schedules arbitration. |
| Native downstream false | `Open -> WaitNative(exact Packet)`. The port takes Packet custody; the FU retains correlation but no send right and no accepted-outstanding entry exists. All sends on the port stop. |
| Logical downstream false | `Open -> WaitLogical(exact provisional slot)`. Runtime/Transport remains unchanged; bridge retains the exact Packet, Request, sender state, and preview. |
| `recvReqRetry` in `WaitNative` | `WaitNative -> GrantNative`; schedule the port arbiter. Only that exact Packet may retry. |
| `recvReqRetry` in `WaitLogical` | `WaitLogical -> GrantLogical`; schedule the port arbiter. Do not call Runtime `recvReqRetry`, because Runtime was never mutated by refusal. |
| Retry attempt false | `Grant* -> Wait*` with the same owner/identity. |
| Retry attempt true | Atomically commit the corresponding native sent bit or logical preview, increment the combined accepted-outstanding count, clear to `Open`, advance RR cursors, and schedule another arbitration edge. |
| Fresh attempt true | Same commit/count/cursor transition as an accepted retry. |
| Response | Authenticate class first, decrement the combined count once and its class count once, enqueue to the bounded class response owner, and schedule both response progress and port arbitration. It never manufactures `recvReqRetry`. |
| Spurious/duplicate `recvReqRetry` | Internal panic; state must be `WaitNative` or `WaitLogical`. |

Native issue must expose the same present/accepted boundary: the existing FU
issue owner may retain correlation, but the port's `WaitNative`/`GrantNative`
state is the sole retry entitlement, and no accepted-outstanding map entry,
counter, or sent bit changes until `sendTimingReq` returns true. A false native
send transfers Packet custody to that one port state; the FU correlation has
no independent send right. The bridge adds no native FIFO. Native outstanding
and deferred owners remain capped by their configured limits; limit exhaustion
is local capacity, not downstream retry.

The combined outstanding bound is the existing configured port maximum. The
port itself holds at most one refused Packet. Logical response queues hold at
most eight Packet-slot indices per Runtime; delivery queues hold at most four
tickets per Runtime, matching Transport credits. No new unbounded queue, map,
list, or allocation is permitted in the bridge.

**DECISION.** Fairness is deterministic. If both classes remain eligible, the
class RR bit alternates after each accepted fresh send. Logical grants rotate
`nextLogicalMaa` after each accepted logical send. A granted retry preempts fresh
work until that exact Packet is accepted, because gem5 issued the retry for the
refused owner. Response and local-capacity wakeups cannot change retry owner.
Under recurring downstream acceptance and response progress, an eligible class
receives a grant within two accepted fresh sends and an eligible logical
context within `2 * num_maas` accepted fresh sends. No bounded latency is
claimed while downstream never retries or accepted requests never respond.

**REJECT.** Any direct native caller bypassing the arbiter, two fields that can
simultaneously await one `recvReqRetry`, RR movement on refusal/stall, a retry
callback delivered to a different Packet, polling at zero time, logical
Runtime/Transport mutation on false send, or a ninth response/Packet/sender
slot fails review.

### 2.3 Response and delivery ordering

**DECISION.** `CacheSidePort::recvTimingResp` examines sender state before every
native map/counter access. Logical response authentication uses both fresh
SE/paddr checks from section 1 and Runtime wire identity. ReadResp and
ReadRespWithInvalidate are distinct legal inputs; WriteResp is exact. A read
response remains in its fixed Packet slot until Runtime copies 64 bytes into a
Transport credit and returns a delivery ticket. A write snapshot remains owned
until WriteResp. The copy event then consumes one fixed ticket and one private
write-port reservation before `commitDelivery`.

CopyHook remains a bounded internal permit, not guest code: one fixed
`{maaID,ticket,eventGeneration,armed}` value, one invocation, no allocation,
scheduling, Packet access, Runtime call, or exception. Transport performs the
actual 64-byte copy (`src/mem/MAA/LogicalSPDCacheTransport.cc:643-702,757-805`).

## 3. ABI, MMIO, range IDs, command ownership, and RF hazards

### 3.1 Range and ABI facts

**FACT.** The accepted logical word image uses the high bytes of word zero for
logical IDs, requires logical source 2 to be `0xff`, preserves the legacy
all-zero form, uses opcode 8, requires word two `NoAddress`, and carries
destination backing in word three
(`include/gem5/maa_logical_spd_cache_abi.hh:19-29,124-174`;
`src/mem/MAA/CpuSidePort.cc:311-424`). The guest helper emits those four words
(`benchmarks/API/MAA_gem5.hpp:237-277`). Current decode fails closed before
controller mutation (`src/mem/MAA/CpuSidePort.cc:389-424`).

**FACT.** `AddressRangeType` currently has seven MMIO range types, IDs 0-6,
with `MAX=7` (`src/mem/MAA/IF.hh:263-289`); its name table contains seven names
plus `MAX` (`src/mem/MAA/IF.cc:713-739`). `MAAConfig` appends the ranges in that
same order (`configs/common/MAAConfig.py:217-252`), and all non-SPD ranges are
owned by CPU response port 0 (`src/mem/MAA/MAA.cc:200-241`).

**FACT.** Those MMIO range-type IDs are not guest memory-region IDs. The guest
helper registers MMIO apertures as regions 0-6 and starts user regions at 7
(`benchmarks/API/MAA_gem5.hpp:84-100`). MAA stores up to 32 guest regions in
`addrRegions` (`src/mem/MAA/MAA.cc:105,287-325`). Conflating the eighth MMIO
type with the `MAX_CMD_REGIONS` limit is incorrect.

### 3.2 Exact v1 command surface and finite Packet owners

**DECISION.** Append one eighth MMIO type:

```
LOGICAL_SOURCE_RANGE = 7
MAX = 8
```

and expand `address_range_names` to nine entries including `MAX`. Its byte size
is `num_maas * LogicalDescriptorCount * sizeof(uint64_t)`, ordered by
`offset = (maa_id * 2 + logical_id) * 8`. Append it after
`VIRTUAL_PAGE_READY_RANGE` in `MAAConfig.py` and the guest allocator. The helper
then registers MMIO region ID 7 and begins user backing regions at ID 8. V1
requires all source/destination backing region IDs in `[8, 32)` and rejects any
configuration whose ordered range vector has a different size/type/name.
Exactly 24 guest backing-region IDs remain. Because v1 reserves two distinct
live backing regions per `maa_id`, initialization requires
`8 + 2 * num_maas <= MAX_CMD_REGIONS` (therefore `num_maas <= 12`) and admission
requires those two IDs to remain uniquely owned until reset. Shared or reused
IDs do not evade this bound.

Add typed helpers with explicit ownership:

```
maa_register_logical_source_fp64(maa_id, logical_id, const double *base)
maa_mul_scalar_logical_fp64(maa_id, src_logical, dst_logical,
                            double *destination, int scalar_reg)
```

The first performs one 8-byte registration WriteReq. The second preserves the
accepted word0-3 image and restricts it to source 0, destination 1, FP64 MUL,
and one 128-KiB-aligned source/destination span.

**DECISION.** Each `maa_id` has exactly one fixed `LogicalMmioSlot`:

```
Free -> RegisterValidate -> RegisterResponse -> Free
Free -> AssembleWords0To3 -> AdmitValidate -> Active
     -> CompletionReady -> CompletionResponse -> Free
```

It holds at most one incoming Packet and one command assembly. Therefore a
registration waiter and operation waiter for the same `maa_id` never coexist.
Exactly one completion-bearing logical operation can exist per `maa_id`; a
second word zero, registration, or doorbell while the slot is not Free returns
false/retry before consuming the Packet. The held word-three Packet is not
responded until the fourth destination page's final WriteResp is authenticated
and Runtime retires the operation.

The three earlier instruction-word responses and the registration/final
response use a new bounded MAA CPU-response arbiter. Logical contribution is
hard-limited to `4 * num_maas` entries (at most three assembly acknowledgements
plus one registration-or-completion response per `maa_id`); native contribution
is hard-limited by `max_outstanding_cpu_side_packets`. It owns one downstream
response-retry token and alternates native/logical ready classes. Logical
Packets never rely on the current nominally infinite `QueuedResponsePort`
contract (`src/mem/qport.hh:52-88`) or its 16,384-entry sanity panic
(`src/mem/packet_queue.cc:97-113`). Every transfer is explicit:

- incoming command Packet: CPU requestor -> one `LogicalMmioSlot`;
- completed command Packet: slot -> bounded CPU-response arbiter;
- accepted response: arbiter -> CPU; and
- refused response: exact arbiter entry retains ownership until
  `recvRespRetry`.

The inbound request side extends each physical `CpuSidePort`'s existing tile
retry fields (`src/mem/MAA/MAA.hh:61-140`;
`src/mem/MAA/CpuSidePort.cc:654-726`) into one discriminated token:

```
CpuReqRetry = Open
            | WaitNativeTile(tileID)
            | WaitLogical(maaID, mmioSlotEpoch)
            | GrantOutstanding
```

A busy logical slot returns false without taking the Packet and changes only
`Open -> WaitLogical`; the CPU/xbar retains Packet ownership. Slot release
changes that exact state to `GrantOutstanding` and calls `sendRetryReq()` once.
The next `recvTimingReq` consumes the generic gem5 grant before independently
authenticating whatever Request the source retries; if it is still blocked it
installs exactly one new wait reason. Native tile wakeup uses the same token.
No CpuSidePort can record native and logical retry reasons simultaneously,
issue two grants, or poll capacity. A retry while not `GrantOutstanding`, a
second refusal while not Open, or teardown with a live token is an internal
panic.

`MAA::drain` waits for all logical MMIO slots, the bounded response arbiter, and
its retry owner, plus every CPU request-retry token, to empty. Queue overflow,
two completion waiters for one `maa_id`, an unowned Packet, or response to a
different CPU/requestor/context is an internal panic.

### 3.3 Validation and scalar producer closure

**DECISION.** Before Runtime mutation, validate in this order:

1. exact unmasked 8-byte WriteReq and range offset; owner tuple/`maa_id` from
   section 1; initialized four-port/64-byte geometry; no lifecycle closure;
2. exact logical IDs, FP64 MUL shape, absent physical/conditional operands,
   `NoAddress` word two, 128-KiB alignment/no overflow, one complete registered
   region per span, and no virtual overlap;
3. all 32 source/destination PTE fingerprints and physical non-aliasing;
4. the complete two-word FP64 scalar dependency and value capture; and
5. one atomic `registerSource` or `admit`, followed by fingerprint commit.

**FACT.** Current RF writes are first held in `my_registers`/`my_register_pkts`
and dispatch later (`src/mem/MAA/CpuSidePort.cc:143-180`;
`src/mem/MAA/MAA.cc:946-978`). Current `IF::canPushRegister` checks only one
register word (`src/mem/MAA/IF.cc:451-467`). Valid IF entries remain present
while issued (`src/mem/MAA/IF.cc:644-712`) and functional units read/write RF
directly (`src/mem/MAA/ALU.cc:707-776,851-861`). Checking only current RF
contents or one IF word cannot prove a two-word FP64 value ready. The
transparent controller also exposes an existing register-span ownership query
(`src/mem/MAA/MAA.cc:641-647`).

**DECISION.** Add read-only `IF::usesRegisterSpan(maa_id, first, words)` and
`MAA::registerSpanQuiescent(owner, first, words)`. The latter requires:

- `words == 2` and the full span in RF bounds;
- no valid IF entry for that `maa_id` names either word as source or
  destination, including active Service entries;
- no pending `my_registers` write from any owner overlaps either word;
- `transparentControllerUsesRegister(maa_id, first, 2)` is false;
- no logical MMIO assembly has already captured the span; and
- the command owner tuple matches the RF-producing requestor/context policy.

Only then read the 64 bits once and place `scalarBits` by value in Runtime
Admission (`src/mem/MAA/LogicalSPDCacheSlice.hh:112-120,522-529`). Later RF
writes cannot change the operation. Busy returns bounded NotReady and leaves
Runtime unchanged; no polling occurs at zero time.

**REJECT.** If a complete producer query cannot cover pending RF writes plus all
valid/active IF entries, remove the RF operand from v1 and place scalar bits in
a newly reviewed command field. Reading RF speculatively is not an option.

## 4. Scheduling, modeled timing, and the bridge-state ledger

### 4.1 Events and finite work

**FACT.** Runtime compute is synchronous: `beginCompute` and `executeCompute`
are separate calls, but `executeCompute` performs all 4096 FP64 operations in a
host loop (`src/mem/MAA/LogicalSPDCacheRuntime.hh:456-527`;
`src/mem/MAA/LogicalSPDCacheDatapath.hh:83-109`). MAA already owns
`EventFunctionWrapper`s and clock-edge scheduling helpers
(`src/mem/MAA/MAA.hh:485-519,834-835`; `src/mem/MAA/Port.cc:729-756`).

**DECISION.** Add these bounded event roles; each invocation performs at most
the stated work and returns:

| Event role | Maximum work per invocation |
|---|---|
| `logicalValidateEvent` | One 4-KiB PTE fingerprint or one 64-byte line revalidation. |
| `logicalPortArbiterEvent[4]` | One native or logical downstream send attempt for that port. |
| `logicalDriveEvent` | Select one Runtime RR, make at most one Runtime state transition, and expose at most one preview. |
| `logicalResponseEvent` | Authenticate/receive at most `logical_response_width` Packets. |
| `logicalCopyEvent` | Commit at most `logical_slot_write_ports` ready delivery tickets. |
| `logicalComputeDoneEvent[maa_id]` | Call `executeCompute` exactly once for the saved compute correlation. |
| `logicalCompletionEvent[maa_id]` | Authenticate four published pages, retire once, and enqueue the held word-three response. |
| `logicalCpuResponseEvent` | Make one bounded native/logical CPU response attempt. |

Every event has one scheduled bit, ready tick, and monotonically increasing
generation field. Stale generation is a no-op only when cancellation explicitly
invalidated it; duplicate live generation is a panic. No callback loops until
idle and no zero-delay polling is allowed.

### 4.2 Private slot timing and the synchronous commit point

**DECISION.** Runtime arrays are private banks with explicitly separate timing
ports. Reuse the numeric parameters `spd_read_latency`, `spd_write_latency`,
`num_spd_read_ports_per_maa`, `num_spd_write_ports_per_maa`,
`ALU_lane_latency`, and `num_ALU_lanes` as initial values
(`src/mem/MAA/MAA.py:120-130`), but store independent logical busy-until arrays
and count their reservations. This does not contend with architectural SPD in
v1; the matched native arm receives the same number/latency of ports.

- Fill delivery reserves one private 64-byte write transfer; its Transport
  credit remains owned until the reservation's ready event commits the copy.
- Writeback preview cannot expose or snapshot a line until one private 64-byte
  read transfer is ready. The snapshot remains immutable through WriteResp.
- `beginCompute` reserves, without overlap, 512 source-line reads, 4096 FP64
  lane operations, and 512 destination-line writes. With `R` read ports, `W`
  write ports, `L` ALU lanes, positive latencies `lr`, `lw`, and `la`, the
  conservative v1 ready cycle is:

```
Tread  = ceil(512 / R) + lr - 1
Talu   = ceil(4096 / L) * la
Twrite = ceil(512 / W) + lw - 1
Tdone  = Tread + Talu + Twrite
```

Only `logicalComputeDoneEvent` at or after `Tdone` calls `executeCompute` and
therefore commits destination payload. The early host loop never executes at
admission, `beginCompute`, or a zero-time drive callback. V1 fixes logical line
issue and CPU-response width to one, requires
`1 <= logical_response_width <= 8` and
`1 <= logical_slot_write_ports <= 4`, and rejects zero as “unlimited.”

### 4.3 Separate bridge-state ledger

**DECISION.** The following is the v1 cardinality ledger. It is separate from
the accepted 66,785-byte Runtime lower bound.

| Adapter state | Exact maximum |
|---|---:|
| Runtime/context/payload owner | `1 / maa_id`; payload remains inside Runtime |
| SE owner tuple | `1 / maa_id` |
| Registered span records | `2 * {vbase, length, regionID, regionGeneration} / maa_id` |
| PTE fingerprints | `2 spans * 32 entries / maa_id` |
| PTE/revalidation in-progress slots | `1 / maa_id` |
| Logical MMIO slots and held incoming Packets | `1 / maa_id` |
| Completion-bearing active operations | `1 / maa_id` |
| Adapter sender states | `8 / maa_id` |
| Adapter Packet slots | `8 / maa_id` |
| Adapter RequestPtr slots | `8 / maa_id` |
| Fixed response/write-preview data buffers | `8 * 64 bytes / maa_id`, tagged union |
| Logical response queue entries | `8 / maa_id` |
| Logical delivery ticket entries | `4 / maa_id` |
| Copy permits | `4 / maa_id`, at most one armed per active copy event |
| Cache-port downstream retry owners | `1 / physical cache port`, four total |
| Cache-port RR state | one class bit and one `maa_id` cursor per port |
| CPU request-retry state | one discriminated native/logical token per physical `CpuSidePort` |
| CPU-response logical entries | `4 * num_maas` total |
| CPU-response retry owner/RR | one owner and one class bit total on command port 0 |
| Event generation/scheduled/ready triples | validate, drive, response, copy, compute, completion per `maa_id`; one CPU-response triple; four port-arbiter triples |
| Private read busy ticks | `num_spd_read_ports_per_maa / maa_id` |
| Private write busy ticks | `num_spd_write_ports_per_maa / maa_id` |
| ALU completion reservation | `1 / maa_id` |
| Waiters | included in the one MMIO slot: registration or operation, never both |
| MAA lifecycle/checkpoint flags | one each: `admissionClosed`, `draining`, `teardown`, `checkpointDirtyV1` |

Runtime's own eight Transport records, FIFO, four credits/line buffers, Slice,
Controller, and two payload slots remain in its accepted semantic ledger and
are not counted twice here. Cache/TLB/page-table/xbar/coherence/memory state and
C++ allocator/pointer/padding costs are external inventories, not hidden bridge
bits.

**DECISION.** Add one `statistics::Scalar` per Runtime for each exact name:

```
registrationAttempts, registrationAccepted, registrationRejected,
admissionAttempts, admissionAccepted, admissionRejected,
sePreflightLookups, seLineRevalidations, staleMapRejects, ownerRejects,
logicalReadReqAccepted, logicalReadResponses,
logicalWriteReqAccepted, logicalWriteResponses,
downstreamRefusals, downstreamRetryGrants, localCapacityStalls,
rrLogicalGrants, pageFills, computesStarted, computesCompleted,
pageWritebacks, pagesCompleted, highLevelCompletions,
privateReadReservations, privateReadBusyCycles,
privateWriteReservations, privateWriteBusyCycles,
aluReservations, aluBusyCycles, aborts, poisons,
packetSlotsHighWater, responseQueueHighWater, deliveryQueueHighWater
```

Add per physical cache port `rrNativeGrants`, `rrLogicalGrants`,
`nativeDownstreamRefusals`, `logicalDownstreamRefusals`, `retryGrants`, and
`acceptedOutstandingHighWater`; add command-port
`logicalCpuResponseQueueHighWater` and `logicalCpuResponseRetries`. Also retain
zero-delta audit counters for native map inserts, native deferred inserts,
Request/Row/Offset claims, and native logical-response counter increments.
Counters are instrumentation excluded from 66,785 bytes.

**REJECT.** No area, SRAM-overhead, or total-hardware-byte claim is allowed
until these production fields exist and a source-checked post-implementation
ledger accounts their declared widths. Host `sizeof`, STL capacity, and the
66,785-byte Runtime lower bound cannot substitute for that ledger.

## 5. Single payload authority and atomic transition

### 5.1 Source facts and storage table

**FACT.** Runtime owns exactly two private 32-KiB arrays
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:17-22,726-763,918-927`). Current SPD
independently appends four hidden 32-bit lanes per MAA, another 64 KiB/MAA
(`src/mem/MAA/LogicalSPDHiddenPayload.hh:15-38,64-105`), and allocates status,
payload, element, waiter, and port state for the enlarged count
(`src/mem/MAA/SPD.hh:31-55`; `src/mem/MAA/SPD.cc:260-338`). Existing accounting
expects 65,536 private bytes/MAA
(`experiments/tests/test_spd_hardware_accounting.py:17-42,46-62`).

**DECISION.** V1 keeps Runtime arrays and removes SPD hidden allocation in the
same commit that instantiates Runtime. `allocated_tile_count` becomes
`visible_tile_count`; SPD allocates and initializes only visible payload/state;
MAA/SPD expose no hidden-lane accessor. `LogicalSPDHiddenPayload.hh` may be
replaced by an accounting-only constant header, but cannot allocate or map.

| Storage class | V1 accounting |
|---|---:|
| Visible architectural SPD payload | `visible_tile_count * physical_tile_elements * 4` bytes |
| Runtime private logical payload | `num_maas * 65,536` bytes |
| Runtime packed semantic control lower bound | `num_maas * 1,249` bytes |
| Bridge adapter state | separate cardinality ledger in section 4; bytes/area intentionally TBD |
| External cache/MMU/page-table/xbar/memory state | outside MAA/bridge area |

Thus Runtime's total remains 66,785 semantic bytes/MAA, of which 65,536 are
payload. The storage equation is:

```
visible architectural SPD bytes
+ num_maas * LogicalSPDCacheRuntime::PrivatePayloadBytes
+ separately implemented-and-audited bridge state
```

### 5.2 Atomic test and allocator transition

**DECISION.** The storage-authority commit changes all of these together:

- allocation/ownership: `src/mem/MAA/SPD.hh`, `src/mem/MAA/SPD.cc`,
  `src/mem/MAA/LogicalSPDHiddenPayload.hh`, `src/mem/MAA/MAA.hh`,
  `src/mem/MAA/MAA.cc`, new bridge files, and `src/mem/MAA/SConscript`;
- object/geometry gate: `tests/maa/logical_spd_hidden_payload_test.cc` becomes a
  Runtime-owner/no-hidden-tail test and
  `tests/maa/logical_spd_cache_vertical_slice_test.cc` asserts two Runtime slots;
- source contract: `experiments/tests/test_logical_spd_hidden_payload_contract.py`
  rejects hidden allocation/accessors and requires one Runtime/`maa_id`;
- accounting: `experiments/analysis/spd_hardware_accounting.py` source-checks
  visible SPD plus Runtime payload, and
  `experiments/tests/test_spd_hardware_accounting.py` keeps the exact one-payload
  total; and
- gates: `experiments/scripts/run_logical_spd_hidden_payload_unit.sh` runs the
  updated optimized, ASan/UBSan, source-contract, vertical-slice, and accounting
  tests before the commit is accepted.

No later Runtime-attachment commit exists. Rollback restores hidden allocation
and removes Runtime ownership as one unit.

**REJECT.** Any intermediate commit that removes the hidden tail without
Runtime payload, instantiates Runtime while retaining the tail, changes other
visible SPD/virtual capacities, leaves old allocator tests passing for the
wrong reason, or reports one payload while allocating zero/two fails the series.

## 6. Drain, checkpoint, reset, teardown, and panic policy

### 6.1 Source gap and full drain predicate

**FACT.** MAA currently overrides none of `drain`, `drainResume`, `serialize`,
or `unserialize`. `SimObject::drain` therefore returns Drained and base
serialization is empty (`src/sim/sim_object.hh:282-286,315-316`).
`ClockedObject` serializes only power state
(`src/sim/clocked_object.cc:58-67`; declarations at
`src/sim/clocked_object.hh:230-245`).

**FACT.** Runtime drain requires Transport drained, Slice drained, no page or
compute correlation, and no abort request
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:588-620,662-671`). Transport drain
requires no copy/action/FIFO/pending/record/credit owner
(`src/mem/MAA/LogicalSPDCacheTransport.cc:971-985`). Slice drain requires no
active/refill/action/miss/lease and slots only Empty or Clean
(`src/mem/MAA/LogicalSPDCacheSlice.hh:953-978`).

**DECISION.** Lifecycle support lands before registration. `MAA::drain` closes
native and logical admission, requests Runtime drain, and returns Draining until
all existing work completes normally. It does not abort for checkpoint. The
predicate is the conjunction of:

1. every Runtime drained, unpoisoned, and unsealed; Transport action/FIFO/
   pending/records/credits/copy empty; Slice no operation/action/miss/lease and
   slots Empty or Clean; no page/compute correlation or abort;
2. no PTE validation/revalidation, provisional fingerprint, adapter Packet,
   Request, sender state, response, delivery ticket, CopyPermit, retry owner,
   MMIO waiter/assembly, or CPU response/retry owner;
3. all logical/native response queues empty; all logical events unscheduled;
   no private port/ALU busy tick after `curTick()`;
4. native IF/FU/register/instruction/ready/transparent-controller ownership
   empty; native outstanding/deferred maps, per-unit sets/counters, Request/Row/
   Offset tables, port blocks, and issue/send/dispatch events empty; and
5. no teardown/reset in progress.

Explicit const `quiescent()` queries prove each term. `allFuncUnitsIdle()` alone
is insufficient because it does not cover maps/waiters/events
(`src/mem/MAA/MAA.cc:463-484`; native ownership at
`src/mem/MAA/MAA.hh:485-519,756-839`). `drainResume` verifies the same predicate,
calls Runtime `resumeAfterDrain`, and reopens admission.

MAA owns the four lifecycle flags inventoried in section 4.3. In particular,
`checkpointDirtyV1` is initialized false, set **before** the first
simulator-visible guest mutation of regions, RF, SPD, IF, native units, virtual
metadata, logical registration/admission, or bridge state, and never cleared by
drain, abort, reset, or teardown. Every such mutation entry point must pass one
central `markMaaCheckpointDirty()` seam. This irreversible latch prevents a
reset or coincidental return to zero-valued fields from masquerading as a
construction-pristine checkpoint.

### 6.2 Honest v1 checkpoint policy

**DECISION.** V1 supports only a construction-pristine MAA checkpoint. This
matches the existing DX100 pattern in which the guest checkpoints before
`alloc_MAA()`, region registration, RF writes, or MAA commands; for example,
`benchmarks/API/test_virtual_index_gather.cpp:58-72` calls `m5_checkpoint` at
line 60 and `alloc_MAA` at line 62.

The current `alloc_MAA()` computes guest pointers and then invokes
`clear_mem_region()`, so its region mutation crosses the enforceable boundary
(`benchmarks/API/MAA_gem5.hpp:102-129`). Pure guest pointer arithmetic is not
observable by the SimObject, but the current workflow's checkpoint-before-
`alloc_MAA()` placement precedes its first simulator-visible MAA mutation. The
report does not claim to detect an otherwise inert host instruction.

`MAA::serialize(CheckpointOut &cp) const` must first require full drain and
`maaCheckpointPristine()`, then call `ClockedObject::serialize(cp)` and emit only
a versioned `maaPristineV1=true` marker plus immutable geometry/config hashes.
`MAA::unserialize(CheckpointIn &cp)` first reads and requires the v1 marker/
config match and a pristine newly constructed MAA/bridge; only then does it call
`ClockedObject::unserialize(cp)`. It imports no native, bridge, or logical
operation state. Base chaining is not optional.

Pristine means all of:

- global `checkpointDirtyV1 == false` and every other MAA lifecycle flag at its
  permitted drained-checkpoint value;
- Runtime never initialized/registered/admitted; both payload arrays remain
  construction-zero; all descriptor/generation/action/record/budget state at
  construction values;
- no bridge owner, fingerprints, generation, stat-dependent operational state,
  MMIO slot, Packet, event, timing reservation, or retry state;
- `addrRegions` all `{0,0}` and `maxRegionID == -1`;
- RF bytes at construction zero and no register waiter;
- SPD payload/status/dirty/ready/size/element/waiter/busy-tick state exactly at
  construction values; and
- IF, every native FU/table/map/queue/event/counter that affects behavior,
  virtual-page/transparent-controller state, and all ports at construction
  values.

**DECISION.** The following persistent native state is expressly **not**
serialized in v1 and is the reason non-pristine checkpoints fail:

| Outside v1 restore scope | Source anchor |
|---|---|
| SPD payload, status, dirty, ready, size, element-finished, waiter vectors, read/write busy ticks | `src/mem/MAA/SPD.hh:31-46`; allocation at `src/mem/MAA/SPD.cc:308-338` |
| RF byte array | `src/mem/MAA/SPD.hh:153-182` |
| Guest registered regions and `maxRegionID` | `src/mem/MAA/MAA.hh:442-446`; mutation at `src/mem/MAA/MAA.cc:287-325` |
| IF entries/valid bits/completion-only flags and live native instructions | `src/mem/MAA/IF.hh:235-261`; `src/mem/MAA/IF.cc:335-450` |
| Pending instruction/register/ready Packets and transparent state | `src/mem/MAA/MAA.hh:485-511` |
| Native outstanding/deferred maps, per-unit packet sets/counters, send events, and port blocks | `src/mem/MAA/MAA.hh:756-839` |
| Native FU internal progress, Request/Row/Offset tables, invalidator and virtual metadata | `src/mem/MAA/MAA.hh:278-285,393-440` |

A logical-only `QuiescentImage` is therefore not part of v1. It could restore
Runtime while leaving native MAA/SPD/RF/regions wrong. Any serialize attempt
after the first simulator-visible MAA initialization, `m5_add_mem_region`, or
RF/SPD/IF/native/logical mutation, even if drained or reset, is fatal with the
first failed pristine term. Any non-pristine v1 marker or attempt to import
logical descriptors/payload is fatal before state mutation.

**REJECT.** “Quiescent” is not synonymous with “checkpointable.” No report or
test may claim post-registration/post-operation restore until every persistent
native and logical field has a complete versioned serializer and independent
restore oracle.

### 6.3 Reset, abort, teardown, and ProductionStop

**DECISION.** Explicit cancel—not checkpoint drain—uses Runtime abort and its
mandatory dirty flush. Transport retains in-flight ownership until responses;
accepted writeback cancellation returns to Dirty and establishes abort-flush
until exact ACK (`src/mem/MAA/LogicalSPDCacheRuntime.hh:530-585,889-915`;
`src/mem/MAA/LogicalSPDCacheController.hh:643-715`).

`logicalReset(maa_id)` is legal only at full context quiescence. It calls
Runtime reset first, then clears owner/fingerprints/MMIO/timing/event state.
Guarded teardown closes admission, drains, empties CPU responses, clears
adapter identity, calls Runtime `teardown`, requires `sealed()` and
`destructionSafe()`, then destroys contexts
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:605-641`).

Every Runtime return is classified before guest enable:

- ProductionStop, Poisoned, failed exact completion, stale internal identity,
  CopyHook violation, or impossible adapter state -> `panic` with audits;
- invalid guest ABI/owner/preflight mapping before Runtime mutation -> `fatal`;
- post-admission stale mapping/fault -> fatal simulation stop, never continue;
- Busy/NotReady/NoCredit/local capacity -> bounded event wait only;
- downstream false -> exact port retry owner only; and
- Exhausted -> fatal, never wrap an identity.

Poison is persistent; reset/checkpoint cannot turn it into a recoverable guest
error (`src/mem/MAA/LogicalSPDCacheRuntime.hh:20-25,765-807`).

## 7. Lifecycle-first patch sequence and rollback boundaries

**DECISION.** Every implementation commit must pass its named host/source gates,
compile the affected X86 objects, and remain guest fail-closed. No commit may
depend on a later commit for ownership, teardown, or accounting correctness.

### Commit 1 — build closure and atomic payload authority, admission closed

Paths:

- `src/mem/MAA/SConscript`
- new `src/mem/MAA/LogicalSPDCacheGem5Bridge.hh`
- new `src/mem/MAA/LogicalSPDCacheGem5Bridge.cc`
- `src/mem/MAA/LogicalSPDCacheTransport.cc`
- `src/mem/MAA/MAA.hh`, `src/mem/MAA/MAA.cc`
- all storage/test/accounting paths named in section 5.2

Add Transport and bridge sources to SConscript, construct one inert Runtime per
`maa_id`, and atomically move payload authority from SPD hidden lanes to
Runtime. No MMIO registration, Packet path, or admission exists. Run the exact
payload/accounting/object gates from section 5.2 and compile all affected
objects. Rollback restores hidden allocation and removes Runtime/bridge as one
unit.

### Commit 2 — ownership, lifecycle, pristine checkpoint guard, panic mapping

Paths:

- bridge files; `src/mem/MAA/MAA.hh`, `src/mem/MAA/MAA.cc`
- `src/mem/MAA/IF.hh`, `src/mem/MAA/IF.cc`
- cache/CPU/memory port sources and native FU headers for const queries
- new `tests/maa/logical_spd_cache_bridge_lifecycle_test.cc`

Add owner containers, guarded constructor/destructor, drain/resume, exact
quiescence/pristine queries, ClockedObject checkpoint chaining, fail-closed
non-pristine policy and irreversible `checkpointDirtyV1` seam, reset/abort/
teardown, bounded CPU-response skeleton, and ProductionStop/Poisoned panic
conversion. Host tests cover construction,
partial-failure cleanup, every drain term, pristine round trip, rejection after
one mutation in every persistent class, reset, abort flush, and teardown. Guest
logical decode remains at the accepted panic.

### Commit 3 — SE ownership, range, registration, and RF queries, admission closed

Paths:

- `include/gem5/maa_logical_spd_cache_abi.hh`
- `benchmarks/API/MAA_gem5.hpp`
- `configs/common/MAAConfig.py`
- `src/mem/MAA/IF.hh`, `src/mem/MAA/IF.cc`
- `src/mem/MAA/CpuSidePort.cc`, `src/mem/MAA/MAA.hh`,
  `src/mem/MAA/MAA.cc`, bridge files
- `tests/maa/logical_spd_cache_abi_test.cc`
- new `tests/maa/logical_spd_cache_bridge_host_test.cc`
- `experiments/tests/test_logical_spd_cache_abi_contract.py`

Add the ordered eighth MMIO range, explicit `maa_id` helpers, fixed MMIO slots,
owner-tuple validation, one-PTE-per-event SE fingerprint/revalidation seam, and
complete RF-span queries. Permit source registration only after lifecycle is
present; keep operation admission fail-closed. Tests cover range/name/vector
order, guest region IDs 8-31, wrong owner/context/requestor/process, immediate
and changed PTEs, flags/faults/alias, lifetime, all RF producers, finite Packet
ownership, and no Runtime mutation on validation failure.

### Commit 4 — shared cache-port and CPU-response arbiters, admission closed

Paths:

- bridge files; `src/mem/MAA/MAA.hh`, `src/mem/MAA/MAA.cc`
- `src/mem/MAA/CacheSidePort.cc`, `src/mem/MAA/CpuSidePort.cc`
- Runtime/Transport files for `previewLine`/`commitAcceptedLine`
- bridge host test and new
  `experiments/tests/test_logical_spd_cache_bridge_contract.py`

Implement the exact state machines, fixed Packet/Request/sender/response/
delivery pools, two-phase no-mutation refusal seam, response authentication,
and CopyPermit. The logical operation path is still closed. Tests cover native
and logical false sends, repeated/spurious retries, local capacity, class and
`maa_id` fairness, response progress/reordering/replacement, wrong owner/paddr/
region/port, all fixed bounds, discriminated CPU request and response retry,
and zero native-contamination deltas.

### Commit 5 — timing ports, events, ledger fields, and mechanism stats, admission closed

Paths:

- bridge files; `src/mem/MAA/MAA.py`, `src/mem/MAA/MAA.hh`,
  `src/mem/MAA/MAA.cc`
- host test
- `experiments/analysis/spd_hardware_accounting.py`
- `experiments/tests/test_spd_hardware_accounting.py`

Add every section-4 field and counter, positive configuration checks, private
read/write/ALU reservations, and event-generation tests. The source-checked
ledger reports production field inventory but makes no area claim. Host tests
prove `executeCompute` cannot run or change destination payload before its
modeled event and every queue/high-water counter obeys its bound.

### Commit 6 — enable exactly one FP64 multiply

Paths:

- bridge files; `src/mem/MAA/CpuSidePort.cc`, `src/mem/MAA/MAA.hh`,
  `src/mem/MAA/MAA.cc`
- `benchmarks/API/MAA_gem5.hpp`
- bridge lifecycle/host tests
- one exact-output smoke source under `benchmarks/`

Only now replace the accepted fail-closed operation panic. Admission captures
the scalar after all validation, holds exactly one word-three Packet per
`maa_id`, drives four ordered pages, and completes only after the final
destination WriteResp and CPU-response acceptance. Rollback restores one panic
without removing safe inert registration/lifecycle.

### Commit 7 — matched validation assets, no timing claim

Paths:

- bridge/MAA stats only if a missing mechanism counter is found
- exact-output runner under `experiments/scripts/`
- parser/oracle tests under `experiments/analysis/` and `experiments/tests/`

Add hashes, manifests, common configuration, matched-arm enforcement, counter
parser, and reject diagnostics. No gem5 execution or performance conclusion is
part of this commit.

**REJECT.** Commits 1-5 must compile with guest operation admission closed.
Commit 6 is forbidden unless every lifecycle, storage, retry, translation,
timing, and finite-ownership host gate already exists and passes.

## 8. Validation and the matched performance matrix

No validation described here was run while repairing this report.

### Gate A — host/source gates

**DECISION.** Before any gem5 binary build, run the accepted ABI/controller/
transport/vertical tests plus the new bridge storage/lifecycle/host gates. The
fake seams cover:

- owner/context/process/page-table lifetime and every Request metadata field;
- 32-page preflight plus every-line revalidation, PTE flags, fault, remap,
  region generation, offset, alias, and FS rejection;
- one MMIO slot/`maa_id`, range IDs/names/order, user regions 8-31, RF pending
  and IF/FU hazards, CPU request-token and response bounds/retry;
- native/logical local capacity, false send, exact/repeated/spurious retry,
  class/context fairness, response progress/reorder/replacement, and all pool
  limits;
- read/write data lifetime, CopyPermit misuse, exact Runtime precommit, and
  zero native map/table/counter contamination;
- private read/write/ALU reservations and no early host datapath commit; and
- drain at every adapter state, pristine-only checkpoint accept/reject,
  ClockedObject chain, abort dirty flush, reset, panic mapping, and teardown.

### Gate B — per-commit object compile

**DECISION.** After each future commit's host gates, compile only its affected
X86 objects. Missing SConscript sources, warnings, link dependency drift, or a
commit that requires a later lifecycle file rejects that commit. This repair
session does not perform those builds.

### Gate C — first gem5 correctness/mechanism smoke

**DECISION.** The first gem5 build and run requires a new, explicit user
approval after implementation review. Restore a construction-pristine
checkpoint taken before `alloc_MAA`, then run one aligned 16,384-double FP64
multiply with guarded source/destination, exact scalar bits, one registration,
one admission, and the final held response. Compare every destination bit and
both guard regions with the host oracle.

Before timing is inspected, require:

| Counter/invariant | Exact result |
|---|---:|
| registrations / admissions / high-level completions | `1 / 1 / 1` |
| page fills / computes started / computes completed / writebacks / pages completed | `4 / 4 / 4 / 4 / 4` |
| logical read requests/responses | `2048 / 2048` |
| logical write requests/responses | `2048 / 2048` |
| preflight lookups | `64` total source+destination |
| line revalidations | one before each of 4096 requests plus one for each response, as configured and reported separately |
| faults / stale maps / owner rejects / aborts / poisons | all `0` |
| max Runtime credits / records / FIFO | `<=4 / <=8 / <=8` |
| adapter Packet/sender/response/delivery high-water | `<=8 / <=8 / <=8 / <=4` |
| cache-port retry ownership | every false has exactly one grant before the exact retry; end state Open |
| live adapter/MMIO/CPU-response/event/timing owners at end | all `0` |
| page order | `0,1,2,3`; no page advance before exact final action ACK |
| completion | final CPU response no earlier than the edge after final destination WriteResp authentication |
| native contamination | all audited native map/deferred/table/response deltas `0` |

The response-time lookup count is deliberately separate from request-time
lookup count; a matrix manifest must freeze the selected policy rather than
silently omit it.

### Gate D — resource- and protocol-matched timing matrix

**DECISION.** Only these timing arms are admissible:

1. `logical4kx4-matched`: the v1 Runtime, two private 32-KiB slots, four ordered
   pages, and the bridge path.
2. `native4kx4-matched`: four ordered native 4K FP64 load/multiply/store phases,
   using exactly two 32-KiB FP64 staging tiles (64 KiB total), throttled to the
   same eight request records, four response credits, four coherent cache
   ports, issue/response widths, and SPD read/write/ALU port counts/latencies.
   A native durability shim holds its final command response until the same
   2048 destination WriteResp acknowledgements are authenticated.
3. `host-oracle`: exact output/canary hash only; never timed against gem5.

`native16` may be recorded as a capacity-unmatched diagnostic, but is not in a
speedup denominator: a 16K FP64 source plus destination has four times the
staging payload and may fuse/end at a different point.

**DECISION.** The two timing arms use the same workload source and binary hash,
simulator commit/binary hash, input bytes/hash, 128-KiB source/destination
addresses, scalar bits, FP64 multiply, four page boundaries/order, output and
canary oracle, CPU model/count, clocks, cache hierarchy/state, memory model,
channel mapping, requestor/context/region metadata, construction-pristine
checkpoint hash, restore tick, stats reset point, and warm/cold policy.

Both arms execute exactly 2048 source-line reads, 4096 FP64 multiplies, and 2048
destination-line writes. Both end only after the same final destination array
is globally durable at the modeled WriteResp boundary and the
completion-bearing MMIO response is accepted. Cross-page fusion, prefetch,
coalescing, extra overlap, different final tile readiness, or private-only
completion is disabled unless implemented identically in both arms.

Report the same three intervals for both:

- `setup`: first completion-bearing command word through all owner/region/PTE/
  RF validation, with lookup counts/cycles;
- `data_motion_compute`: first accepted source request through final destination
  WriteResp authentication; and
- `end_to_end`: first source registration request through accepted final MMIO
  response.

Actual setup/translation/revalidation work is included in end-to-end and
reported separately with identical boundaries; no arm may receive unreported
pretranslation. Mechanism/oracle gates must pass before cycles, bandwidth,
speedup, or energy proxies are parsed.

**REJECT.** Reject an arm for unequal payload, ports/credits/widths, software
work, scalar operation, fusion, destination/durability boundary, setup boundary,
translation policy, cache/checkpoint state, hashes, line counts, output, retry
balance, or native contamination. Do not relabel a mismatched run “slow.”

## 9. Consolidated reject criteria and remaining questions

### Admission and implementation rejects

**REJECT.** Stop before guest admission if any is true:

1. mode is FS, the SE owner cannot be resolved exactly, pTable page size is not
   4 KiB, or any Packet metadata field cannot be authenticated;
2. a mapping is assumed stable, a committed fingerprint is used without fresh
   per-Packet translation, or remap/fault can continue silently;
3. a cache port can have two retry owners, local capacity creates a fake retry,
   RR can starve one class/context, or refusal mutates Runtime/Transport;
4. any Packet/Request/sender/response/delivery/MMIO/CPU-request-retry/
   CPU-response/event/timing owner exceeds the section-4 bound or uses an
   unbounded container;
5. Runtime arrays and SPD hidden lanes both exist, neither exists, or any
   allocator/accounting/object gate describes a different tree;
6. MAA lifecycle/checkpoint/panic/teardown tests are absent when registration or
   admission becomes reachable;
7. `serialize` omits ClockedObject chaining, accepts non-pristine MAA state, or
   claims logical-only restoration covers native SPD/RF/IF/regions/FUs;
8. range types/names/vector/helper order disagree, `8 + 2 * num_maas > 32`, a
   source/destination region is outside `[8,32)` or reused while live, or
   requestor/context maps to a different `maa_id`;
9. scalar capture ignores either RF word, pending register writes, or a valid/
   active IF/FU/transparent-controller producer;
10. synchronous Datapath mutation occurs before all private read/ALU/write
    reservations reach the compute-completion event;
11. ProductionStop/Poisoned can return to guest-visible success, or a finite ID
    wraps; or
12. mechanism counters or exact output are missing before timing, or the matrix
    differs in resources/protocol/boundaries/hashes.

### Beyond-v1 questions

**QUESTION.** What pin/invalidation/fault-completion/IOMMU contract would make
FS legal?

**QUESTION.** Should a later ABI return explicit logical status/error instead
of fatal-on-post-admission SE mapping failure?

**QUESTION.** Should private Runtime slots eventually share architectural SPD
ports/banks? V1 models separate named ports and matches the native timing arm;
changing contention requires a new study.

**QUESTION.** What complete serializer covers native SPD, RF, IF, FUs, tables,
regions, virtual metadata, bridge, and Runtime for non-pristine checkpoints?

**QUESTION.** What descriptor release/promotion lifecycle permits repeated
operations and source reuse? Until that exists, the v1 matrix measures one
operation's mechanism/overhead and cannot claim multi-operation cache benefit.

## Implementation-ready acceptance checklist

The candidate is ready for the first separately approved gem5 smoke only when
all boxes are true:

- [ ] SE-only gate is explicit; FS fails before registration.
- [ ] Every command and data Packet authenticates vaddr, fresh paddr, region,
  requestor, context, process/pTable identity, owner generation, and `maa_id`.
- [ ] A fingerprint is never called stable; all stale/fault/lifetime transitions
  fail closed exactly as section 1 specifies.
- [ ] Each cache RequestPort implements the one-owner state machine, exact retry,
  local-capacity separation, native/logical and logical-context RR fairness.
- [ ] A downstream false leaves all Runtime/Transport audit fields unchanged.
- [ ] All logical queues/pools/events/waiters and CPU responses obey section-4
  cardinalities; each CPU request port has one discriminated retry token; all
  drain to zero.
- [ ] Exactly one completion-bearing operation and one MMIO slot exist per
  `maa_id`; ranges/names/vector order, `num_maas <= 12`, and unique live user
  region IDs are source-tested.
- [ ] Both RF words, pending writes, and every valid/active IF/FU/transparent-
  controller producer are clear before scalar capture.
- [ ] Runtime is the sole 64-KiB payload owner per `maa_id`; every named hidden-
  payload/accounting/object/sanitizer test changed atomically.
- [ ] Lifecycle, guarded teardown, ProductionStop panic, and construction-
  pristine checkpoint accept/reject tests land before guest admission.
- [ ] `MAA::serialize`/`unserialize` chain ClockedObject and every non-pristine
  MAA/logical checkpoint fails closed through the irreversible v1 dirty latch.
- [ ] The host datapath commits only at the modeled event after explicit private
  read, ALU, and private write reservations.
- [ ] The separate bridge-state production ledger exists; no area claim derives
  from 66,785 bytes.
- [ ] Exact mechanism/output gates pass before timing; only the matched 4Kx4
  matrix is timing-eligible.
- [ ] Simulator/workload/config/checkpoint/input hashes and final durability
  boundary are identical across timing arms.
- [ ] The first gem5 build/run has fresh, explicit user approval.

**DECISION — final status.** This document is a repaired bridge design
candidate. It authorizes neither implementation nor a gem5 run and supplies no
live correctness/performance evidence.
