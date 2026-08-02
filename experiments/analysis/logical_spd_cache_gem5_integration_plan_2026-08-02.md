# Logical SPD cache: precise gem5 integration plan

Date: 2026-08-02
Audited baseline: `dbc7f79ff7e55f4c685a0675ed294eb6334be0d9`
Scope: a plan only; this document does not modify simulator or benchmark source.

## 1. Required first slice

Implement exactly two logical descriptors and exactly two resident physical slots per MAA. Each logical descriptor represents one 16K-element tile split into four 4K-element pages. A normal pagewise `ALU_SCALAR` instruction names logical source and destination descriptors; it does not use the special transparent opcode and does not expose physical slot IDs to software.

The two slots are controller-private SPD storage. A source page is filled into a slot on a miss, a destination page is reserved in the other slot, and the existing ALU executes one ordinary physical page micro-op. The destination becomes dirty at ALU completion. A slot and its descriptor may not be reused until a response-bearing `WriteReq` receives its matching `WriteResp`.

This first slice deliberately permits only one logical ALU consumer at a time per MAA. It accepts scalar operations already implemented by the ALU when the operation writes every destination element. A conditional/masked logical operation is rejected in this slice because destination allocation is full-overwrite and does not first fetch old destination data. Two-source logical ALU and reduction are reserved encodings, not partially implemented features.

No performance, timing, or area conclusion is part of this plan.

## 2. Source audit: what exists at the baseline

### Instruction file and MMIO ABI

- `src/mem/MAA/IF.hh` contains the actual MAA `gem5::Instruction` and `IF` classes. There is no separate MAA architectural ISA instruction file under `src/arch`.
- `src/mem/MAA/CpuSidePort.cc::recvTimingReq` assembles an instruction from MMIO writes. Word 0 carries physical destinations/sources, opcode, datatype, and optype. Word 1 carries the remaining source tiles, registers, and condition register; word 2 is the base address; words 3 and 4 are used by virtual/fused forms.
- `benchmarks/API/MAA_gem5.hpp` is the software-side encoding. `maa_alu_scalar` emits ordinary opcode 8. `maa_virtual_tile_alu_scalar_store` emits special opcode 16 and currently exposes a completion token, a physical input tile, an output tile, and page registers.
- `src/mem/MAA/IF.cc::pushInstruction`, `getReady`, issue, and finish logic express hazards in physical tile IDs/address ranges. The current special path adds a completion-only physical tile token rather than a logical identity.

### SPD and execution units

- `src/mem/MAA/SPD.hh` and `SPD.cc` allocate `num_tiles * physical_tile_elements * 4` bytes. A 32-bit tile consumes one lane tile and a 64-bit tile consumes two consecutive lane tiles. Existing payload access, element readiness, size, ready-credit, status, and port arbitration are usable for controller-private slots.
- `SPD::setVirtualSize` changes metadata only; it does not make a physical payload tile hold 16K elements. `SPD::tiles_dirty` is CPU/SPD coherence state and is not a logical-cache dirty bit.
- `src/mem/MAA/ALU.cc::executeInstruction` reads and writes physical SPD tile IDs. Its scalar operation and datatype implementation can be used unchanged once the controller emits a physical 4K-page micro-op.
- `src/mem/MAA/StreamAccess.cc` performs line iteration and store read/modify/write. Its normal store creates a response-less `WritebackDirty`; `writePacketSent` counts an accepted send as completion. That path is not true write completion. There is one `StreamAccessUnit` per MAA, so logical fills and writebacks must share and serialize through that unit; the design does not assume two simultaneous stream actions.

### Current transparent controller

- `src/mem/MAA/TransparentSPDController.hh` is a header-only, single-descriptor, single-mapping controller. Its fixed chain is fill, compute, store for each of four pages. It supports one native micro-op in flight and carries a generation but no unique transaction ID.
- `src/mem/MAA/MAA.hh` owns one global controller and physical-tile-indexed arrays for virtual page readiness, generation, backing address, word size, and consumed generation.
- `src/mem/MAA/MAA.cc::submitTransparentDescriptor` admits only opcode 16 and currently requires FLOAT64 multiply, 4096 physical elements, 16384 logical elements, and software-supplied physical/token/output IDs.
- `MAA::dispatchTransparentMicroOp` emits native stream load, ALU, and stream store operations. `MAA::finishInstructionCompute` advances the controller when each native unit finishes. A final normal stream store therefore retires after its write packets are accepted, not after memory returns write responses.
- `MAA::dispatchInstruction` acknowledges the special descriptor to the CPU when the descriptor is accepted, not when the destination is durably complete.

### Producer, ports, and response path

- `src/mem/MAA/IndirectAccess.cc::createRetirementWrite` already creates response-bearing `WriteReq` packets for indirect virtual production, forces them through a cache retirement port, and counts completion in `retirementWriteComplete` on `WriteResp`.
- `IndirectAccess::markVirtualPageReadyIfComplete` publishes a page only after its scan is complete and all expected retirement writes have been issued and acknowledged. This completion rule is reusable.
- The producer currently identifies its destination through `my_dst_tile` and calls `MAA::setVirtualPageReady(tile, page)`. Neither callback carries an explicit logical descriptor generation nor a unique producer transaction ID.
- `src/mem/MAA/Port.cc` holds `OutstandingPacket`/deferred state keyed by address. Indirect response-bearing retirement writes remain outstanding and are routed from `MAA::recvTimingResp` to `IndirectAccessUnit::retirementWriteComplete`. Stream writes assert that no response is needed and complete on send.
- `src/mem/MAA/CacheSidePort.cc` and `MemSidePort.cc` are thin response wrappers around the shared MAA receive path and need no new policy.
- `src/mem/MAA/MAA.py` declares the SPD sizing and ports. `configs/common/MAAConfig.py` builds the virtual-ready MMIO range and connects `retirement_sides` to cache CPU sides.

### Existing models and tests

- `tests/maa/transparent_spd_controller_test.cc` and `experiments/tests/test_transparent_spd_controller_contract.py` cover the current fixed transparent chain.
- `experiments/analysis/spd_cache_state_model.py` models two logical descriptors and finite generation-tagged cache transitions, but only one physical slot. It is a useful oracle, not an integration implementation.
- Coordination checkpoint `456fa5d19f5c8bd0e29a47a7171dd8ee0ad9eae1` reports a hardened logical-controller prototype. It is not present in this audited baseline. It may seed patch 1 only after its interfaces and invariants are compared against this plan; it must not be silently treated as integrated code.

## 3. Architectural identity and storage

### Logical descriptors

Create `src/mem/MAA/LogicalSPDCacheController.hh` and replace the transparent controller with one `LogicalSPDCacheController` per MAA. The first slice uses compile-time constants:

```text
LogicalDescriptorCount = 2
LogicalElements         = 16384
PageElements            = 4096
PagesPerDescriptor      = 4
PhysicalSlotCount       = 2
MissQueueDepth          = 4
InvalidTransactionID    = 0
```

Each descriptor contains:

```text
state: Free | Producing | Live | Retiring
generation: uint64_t, nonzero
producerKind: None | Indirect | LogicalALU
producerTransactionID: uint64_t, nonzero while producing
backingBase, elementCount, dataType, wordSize
pageReady[4]
consumerRefs, pendingWaiters, producerActive
```

`Producing` covers both an indirect source being published pagewise and the destination of the active logical ALU; `producerKind` distinguishes their callbacks. The last valid indirect page publication or last acknowledged logical-ALU destination page transitions the descriptor to `Live`. A requested reuse first enters `Retiring`, invalidates clean resident tags, and drains dirty tags through acknowledged writeback.

Allocation increments the generation; zero and wraparound are fatal. A descriptor can return to `Free` only when it has no producer, consumer reference, waiter, queued miss, resident slot, pin, fill, or writeback. A new generation is never visible through an old waiter.

### Physical slots

Each slot contains:

```text
state: Empty | FillQueued | FillInFlight | Clean | Dirty |
       WritebackQueued | WritebackInFlight
tag: {logicalID, page, generation}
pinCount
activeTransactionID
internalLaneBase
```

Use hidden SPD lane tiles rather than architectural application tile IDs. Reserve two 32-bit lanes per slot so the same slot can hold one 64-bit page. Extend the SPD allocation by `num_maas * PhysicalSlotCount * 2` lane tiles and map:

```text
internalLaneBase(maa, slot) = architecturalLaneCount + maa * 4 + slot * 2
```

CPU-visible SPD ranges and all external physical-ID validation remain bounded by `architecturalLaneCount`. Only controller-generated instructions may name the hidden lane range. A slot uses its base lane for 32-bit data and both lanes for 64-bit data. Logical cache state, pins, tags, and dirty state live in the controller; they do not reuse `SPD::tiles_dirty`.

### Transactions

Every asynchronous producer instruction, fill, ALU page operation, and page writeback receives a monotonically increasing nonzero `uint64_t transactionID` from the owning per-MAA controller. A producer's page notifications share its unique producer-instruction ID and are further identified by their page; each fill, ALU page, and writeback operation has its own ID. IDs are not recycled; exhaustion is fatal.

All asynchronous requests and callbacks carry:

```text
{maaID, transactionID, action, logicalID, page, generation, slot}
```

The callback must match the slot/descriptor's current state, action, transaction ID, and complete page tag before it mutates state. Late, duplicate, wrong-kind, wrong-generation, wrong-page, or wrong-slot responses increment a stale-response statistic and are discarded. A callback that matches the active transaction but contradicts its expected state fails closed because it indicates internal corruption.

## 4. Software-visible instruction contract

Keep `ALU_SCALAR = 8`. Leave opcode 16 numerically reserved and reject it with a migration diagnostic; do not renumber existing opcodes.

Use the currently unused high bytes of MMIO instruction word 0:

```text
bits 63:56  logicalSrc1ID
bits 55:48  logicalSrc2ID (reserved in this slice)
bits 47:40  logicalDst1ID
value 0xff  no logical operand
bits 39:0   existing encoding, unchanged
```

An ordinary physical instruction sets all three logical bytes to `0xff`. A first-slice logical `ALU_SCALAR` sets logical source and destination, encodes its physical source/destination bytes as the existing `0xff` sentinel (decoded to `-1`), has no condition register, and uses word 3 for the logical destination backing address. `CpuSidePort::recvTimingReq` waits for word 3 before dispatching this form. It rejects mixed logical/architectural operands, logical source 2, invalid descriptor IDs, aliasing source and destination descriptors, conditional execution, and unsupported operand shape before changing controller state.

Add explicit fields to `Instruction`:

```text
src1LogicalID, src2LogicalID, dst1LogicalID
src1LogicalGeneration, dst1LogicalGeneration
controllerAction, controllerTransactionID
controllerSrcSlot, controllerDstSlot, controllerPage
```

Only controller-generated native micro-ops populate generations, slots, action, and transaction ID. They still use ordinary native STREAM/ALU opcodes and physical hidden-slot IDs.

In `benchmarks/API/MAA_gem5.hpp`:

- add `maa_alu_scalar_logical<T>(srcLogical, dstLogical, destinationBacking, scalarReg, op)` that emits opcode 8;
- change the virtual indirect producer helper to name a logical destination rather than a physical completion token;
- add `maa_wait_logical_page(logicalID, page)` and `maa_wait_logical_tile(logicalID)` using the existing virtual-ready MMIO shell;
- remove the special helper from tests and mark it unavailable rather than preserving two ownership models.

The ready range becomes descriptor-indexed: `LogicalDescriptorCount * MaxVirtualPages * 2` bytes per MAA, in both `MAAConfig.py` and the API. A wait captures the current nonzero generation and increments `pendingWaiters`; it wakes only for that generation, then releases its reference. A descriptor with an outstanding waiter cannot be reused.

## 5. Producer and consumer flows

### Indirect producer

At virtual indirect dispatch, call:

```text
beginProduce(maaID, logicalID, backingBase, elementCount, dataType)
    -> {generation, producerTransactionID}
```

Store both values on the `Instruction` and `IndirectAccessUnit`. Remove the physical completion-token use, `setVirtualSize` call on that token, `completion_only_tiles`, and the physical-tile-indexed virtual-generation arrays.

Keep the producer's current response-bearing retirement `WriteReq` machinery and completed-word/page accounting. Replace the publication callback with:

```text
setLogicalPageReady(maaID, logicalID, generation,
                    producerTransactionID, page)
```

The controller accepts it only for the active `Producing` descriptor and matching producer transaction. The last valid page transitions the descriptor to `Live` and clears `producerActive`. The data becomes fillable from coherent backing only after the existing matching retirement write responses have completed.

### Logical ALU consumer

Admission captures source and destination descriptor generations and scalar-register values. It holds one descriptor reference on each until all four pages complete. Destination descriptor allocation supplies its backing address and word size; compare operations allocate 4-byte destination elements, while the other accepted scalar operations use their normal output size.

For each lowest-numbered unprocessed source-ready page:

1. Lookup `{srcLogical, page, srcGeneration}`. On a miss, enqueue a deduplicated read miss. A fill uses a clean empty/victim slot and a unique fill transaction.
2. Reserve `{dstLogical, page, dstGeneration}` as a full-overwrite destination. It requires an empty/clean victim but performs no fill.
3. Do not hold one page while waiting for the other. Once both are available, acquire the two distinct pins atomically in ascending slot number.
4. Emit one ordinary physical `ALU_SCALAR` micro-op using the source and destination hidden lane bases, the captured scalar value, and a unique ALU transaction.
5. On its exactly matching completion, mark the destination `Dirty`, mark the source used, and release both pins in descending slot number.
6. Queue the destination writeback. A matching `WriteResp` marks the destination page ready and permits the slot to become clean/empty. Only then may a logical waiter observe completion or the slot/tag be reused.

The logical ALU instruction completes only when all four destination writebacks have received matching responses. This response-acknowledged completion, not descriptor admission or packet acceptance, is the architectural completion boundary.

### Miss, eviction, and finite backpressure

The miss FIFO has four entries and deduplicates the complete generation-tagged page key. Admission stalls cleanly when it is full. A clean unpinned slot may be retagged. A dirty unpinned victim first transitions through `WritebackQueued` and `WritebackInFlight`; its tag and generation remain unchanged until the write response. A filling, pinned, or writeback-owned slot is never a victim.

If neither slot can satisfy a page pair, the consumer releases any tentative reservation and waits. It never pins a source while waiting for a destination. Controller work is retried by normal unit-completion, response, and retry events; no unbounded request structure is added.

## 6. True write completion path

Normal application stream stores retain their current response-less `WritebackDirty` behavior. Only a controller-managed logical page writeback takes the new path:

1. `StreamAccessUnit` creates `MemCmd::WriteReq`, not `WritebackDirty`, and attaches a `LogicalSPDTransactionState : Packet::SenderState` containing the controller transaction tuple and line address. Preserve any predecessor sender state and restore it when the MAA state is removed.
2. `MAA::sendPacket` marks it response-bearing, forced-cache, and virtual-retirement, then selects the existing retirement-side cache path.
3. `Port.cc` keeps it in the outstanding/deferred tables after a successful send, exactly as it already does for indirect retirement writes. Address serialization remains FIFO for the exact line address.
4. `MAA::recvTimingResp` cross-checks the response sender state against the outstanding entry, then routes its `WriteResp` to `StreamAccessUnit::writeResponseReceived(address, transactionID, pageTag)`.
5. The stream unit allocates a bounded per-page line-state vector of exactly `PageElements * wordSize / blockSize` entries at transaction start. It counts each matching line response once, classifies a repeated line response as stale, and reports page writeback complete only after every issued line is acknowledged and no packet for the transaction remains outstanding.
6. `MAA::finishInstructionCompute` invokes the logical controller with the complete action/tag/transaction tuple. A writeback action cannot reach this callback through `writePacketSent`.

Extend `OutstandingPacket` and deferred packet metadata with the transaction tuple; an address alone is not identity. Keep address maps for ordering, but validate both the packet sender state and outstanding metadata at response routing. Read/fill response callbacks receive and validate the same tuple. The logical response path must hand an otherwise-unmatched tagged response to stale-response classification rather than letting a missing address-map entry mutate a current transaction.

## 7. Ownership and deadlock order

Ownership is single and explicit:

- software owns logical IDs but never hidden physical lane IDs;
- each MAA controller owns its two descriptors, two slots, transaction allocator, and miss FIFO;
- the indirect unit owns a producer transaction until its last acknowledged page publication;
- the active logical consumer owns generation-tagged descriptor references and captured scalar inputs;
- the SPD owns bytes/readiness/port credits, not logical tags or dirty lifecycle;
- the single stream unit for an MAA owns at most one fill or writeback micro-op and its issued line packets until their matching responses; the controller owns slot reuse policy.

The deadlock order is:

```text
matching responses and stale-response disposal
    -> queued dirty writebacks that can free a slot
    -> queued fills
    -> atomic two-slot pin and ALU issue
    -> new logical consumer admission
```

Within a pair, pins are acquired in ascending slot ID and released in descending slot ID. No pin is retained across a wait for another slot, queue entry, register, IF entry, or port credit. Descriptor references do not block writeback progress. Exact-address deferred packets preserve FIFO order, and response handling never waits for a new request resource. With finite producers and eventual accepted requests/responses, a queued writeback can drain and release a slot; the controller does not create a source/destination reservation cycle.

## 8. Exact code map

| File / class / function | Required change |
|---|---|
| `src/mem/MAA/LogicalSPDCacheController.hh` (new) | Implement the exact descriptor, slot, miss FIFO, generation/transaction, pin, fill, dirty, writeback, wait, and stale-response state above. |
| `src/mem/MAA/TransparentSPDController.hh` | Delete after callers/tests migrate; do not retain a second scheduler. |
| `src/mem/MAA/IF.hh`, `Instruction` | Add logical operands, captured generations, controller action/transaction/page/slot fields. Keep native opcode numbers. |
| `src/mem/MAA/IF.cc`, `IF::pushInstruction` and ready/finish helpers | Remove completion-token special cases. Preserve physical hazards for generated micro-ops; route logical admission through the per-MAA controller. |
| `src/mem/MAA/CpuSidePort.cc::recvTimingReq` | Decode word-0 logical bytes, collect word 3 for logical ALU, validate the first-slice contract, and make waits generation-aware. |
| `src/mem/MAA/SPD.hh`, `SPD.cc` | Distinguish architectural lane count from allocated lane count; allocate/map four hidden lanes per MAA; reject hidden IDs from CPU-visible access. Reuse existing physical storage mechanics. |
| `src/mem/MAA/MAA.hh` | Own one controller per MAA; replace physical-tile virtual arrays; add logical producer/wait callbacks and transaction-bearing send/response APIs. |
| `src/mem/MAA/MAA.cc::dispatchInstruction` | Admit ordinary logical `ALU_SCALAR`; reject legacy opcode 16; bind the selected per-MAA controller. |
| `src/mem/MAA/MAA.cc::submitTransparentDescriptor` | Remove after logical admission is live. |
| `src/mem/MAA/MAA.cc::dispatchTransparentMicroOp` / `tryIssueTransparentMicroOp` | Replace with action-driven logical fill/ALU/writeback scheduling using hidden slot IDs and complete transaction tags. |
| `src/mem/MAA/MAA.cc::finishInstructionCompute` | Pass the exact transaction tuple; distinguish ALU dirty/release from acknowledged writeback completion. |
| `src/mem/MAA/MAA.cc::resetVirtualPageReady` / `setVirtualPageReady` | Replace with descriptor allocation and generation/producer-transaction-aware page publication. |
| `src/mem/MAA/IndirectAccess.hh`, `IndirectAccess.cc` | Store logical ID, generation, and producer transaction; keep response-bearing retirement writes and page accounting; publish the explicit tuple. |
| `src/mem/MAA/StreamAccess.hh`, `StreamAccess.cc` | Add controller fill/writeback transaction metadata and matching response counters; emit `WriteReq` for logical writeback; add `writeResponseReceived`; preserve normal store semantics. |
| `src/mem/MAA/Port.cc`, `OutstandingPacket` / deferred packet handling | Carry full transaction metadata; retain logical stream writes until `WriteResp`; route responses by action and unit; validate identity in addition to address. |
| `src/mem/MAA/CacheSidePort.cc`, `MemSidePort.cc` | Reuse the shared response wrappers; only signature plumbing should be necessary. |
| `src/mem/MAA/ALU.cc::executeInstruction` | No semantic change; execute the controller-generated physical page instruction. |
| `src/mem/MAA/MAA.py` | Keep the feature fixed-size in this slice; document allocated hidden lanes and existing retirement-port requirement rather than adding tunable capacities. |
| `configs/common/MAAConfig.py` | Size logical-ready MMIO by two descriptors and retain retirement-side cache connections. |
| `benchmarks/API/MAA_gem5.hpp` | Encode logical operands on ordinary ALU, logical producer destination, and generation-safe logical waits; retire the special helper. |
| `src/mem/MAA/SConscript` | Register `MAALogicalSPDTrace` if debug flags are declared here on this branch. |
| `tests/maa/*`, `experiments/tests/*` | Add controller, ABI, source-contract, and trace-contract tests described below; remove transparent-only expectations. |

## 9. Reuse versus required replacement

### Reuse unchanged or with metadata plumbing

- SPD payload access, size/readiness tracking, physical status, ready credits, and port arbitration.
- ALU scalar arithmetic, datatype handling, and physical page execution.
- Stream line traversal, load mechanics, and store read/modify/write data construction.
- Indirect producer `WriteReq`, retirement-port routing, per-line acknowledgement, and publish-only-after-complete accounting.
- Cache/memory response wrapper classes and exact-address deferred ordering.
- IF physical hazards for controller-generated native micro-ops.
- The virtual-ready MMIO shell, with descriptor/generation semantics replacing physical-token semantics.

### Must change

- The single fixed transparent controller and special opcode submission model.
- Software-visible physical token/input/output/page-register ownership.
- Physical-tile-indexed virtual readiness and generation arrays.
- The absence of unique transaction IDs on asynchronous requests and callbacks.
- SPD sizing/validation needed to create controller-private hidden lanes.
- Stream controller writeback completion on packet acceptance.
- Port response retention/routing for controller-managed stream `WriteReq`.
- Producer publication without explicit logical generation/transaction identity.
- Tests that equate the fixed fill/compute/store chain with the final design.

## 10. Statistics and traces

Add per-MAA counters:

```text
descriptorAllocations, descriptorReuseStalls
logicalConsumersSubmitted, logicalConsumersCompleted
pageHits, pageMisses, missQueueFullStalls
fillsIssued, fillsCompleted
pinsAcquired, pinsReleased
pagesMarkedDirty
writebacksIssued, writeResponsesCompleted
waitDeferrals, waitWakeups
staleGenerationCallbacks, staleTransactionCallbacks
wrongKindCallbacks
```

Add miss-FIFO occupancy and slot-state occupancy distributions/high-water marks. Assert at teardown that every acquired pin was released and that completed consumers have equal writebacks issued and acknowledged for their four pages.

Add a dedicated `MAALogicalSPDTrace` debug stream. Each state-changing record has a stable event name and fields:

```text
event, maa, core, transactionID, action, logicalID, page,
generation, slot, address, oldState, newState
```

Required events are `allocate`, `producer_page_ready`, `miss`, `fill_issue`, `fill_complete`, `pin`, `alu_issue`, `dirty`, `release`, `writeback_issue`, `writeback_ack`, `wait_wake`, `descriptor_reuse`, and `stale_response`. Extend instruction printing to show logical IDs, generations, action, transaction, page, and slots.

## 11. Review-sized patch sequence

1. **Controller core and exhaustive host tests.** Add the standalone two-descriptor/two-slot controller, fixed queues, state invariants, generations, unique transaction IDs, and transition tests. Do not connect it to MAA yet. The external `456fa5d…` prototype may be rebased here only after a line-by-line contract check.
2. **Instruction/API ABI.** Add logical fields and word-0 decoding, descriptor-indexed waits, API helpers that emit ordinary opcode 8, and validation. Keep the legacy implementation present but reject opcode 16 so there is one software contract.
3. **Response-bearing stream transaction path.** Plumb transaction metadata through `Instruction`, StreamAccess, outstanding/deferred packets, and shared response routing. Add logical-only `WriteReq`/`WriteResp` accounting while leaving normal stream stores unchanged. Unit-test stale/duplicate/reordered responses before using it for slot reuse.
4. **Producer conversion.** Bind virtual indirect production to `{logicalID, generation, producerTransactionID}`, reuse its retirement acknowledgements, and remove physical completion-token readiness arrays and calls.
5. **Hidden slots and scheduler integration.** Extend SPD hidden storage, instantiate one controller per MAA, generate native fills/ordinary physical ALUs/writebacks, enforce slot ownership, and remove the transparent scheduler/class and its single-chain tests.
6. **Observability and focused end-to-end gate.** Add statistics/traces, the two-logical-tile microbenchmark/reference, adversarial backpressure tests, source-contract checks, and documentation cleanup.

Each patch must compile and pass its host/unit gate before the next patch. No patch combines ABI migration with port-completion semantics, and removal of the old controller occurs only when producer and consumer callers have migrated.

## 12. Three-point validation matrix

| Gate | Focus | Required pass conditions |
|---|---|---|
| 1. Host controller and contract gate | No simulator run: C++ transition replay plus Python ABI/source checks | Exhaustively cover two descriptors/two slots, FIFO bounds, deduplication, atomic pins, dirty eviction, generation rollover guard, and late/duplicate/reordered/wrong-kind transaction responses. Prove by assertions that a dirty tag and descriptor cannot be reused before matching write acknowledgement. Verify opcode 8/high-byte encoding and removal of completion-token dependencies. |
| 2. Functional simulator gate | Focused microbenchmark with two interleaved indirect producers and ordinary logical scalar ALU consumers | Check 32-bit, 64-bit, and comparison destination sizing; out-of-order producer page readiness; a chained consumer; descriptor waits; guard regions; and exact CPU-reference bytes. Trace must show four matching destination `writeback_ack` events before consumer completion. |
| 3. Adversarial reuse/backpressure gate | Delayed/reordered write responses, IF/stream/retirement-port retry, full miss FIFO, and immediate logical-ID reuse attempts | No hang or unbounded queue; queue high-water never exceeds four; no pin leak; old-generation responses are counted and ignored; same-page fill is excluded while dirty writeback owns the tag; neither physical slot nor logical descriptor changes generation before the final matching response. |

The current plan-only change runs gate 1's document/source consistency checks only. Simulator gates 2 and 3 belong to the future implementation and must not be reported as completed by this plan.

## 13. Definition of implementation completion

The implementation is complete only when software can produce two logical descriptors, issue an ordinary opcode-8 pagewise consumer naming logical source and destination, and observe destination completion after four acknowledged page writebacks; when all controller queues and states are finite; when generation plus unique transaction identity protects every asynchronous transition; and when the three validation gates pass. Packet acceptance, IF retirement, or a response-less writeback is not sufficient evidence of logical destination completion or safe reuse.
