# Direct-retirement historical reuse audit — 2026-08-10

## Result

The shortest legal generic path is to **factor and reuse** three current mechanisms: the virtual producer's write-ACK page closure, `LogicalSPDCacheTransport`'s four exact 64-byte response credits, and the acknowledged virtual-retirement `WriteReq` path. Add a line-buffer ALU handshake and retire through the existing MAA/IF completion path only after every destination write response.

Do not revive XRAGE's zero-payload opcode as the generic consumer. Commit `e6325d40` is a strict 4K `A[B[i]] * scalar -> C[i]` terminal chain with XRAGE-specific index, scalar, alias, completion-token, and storage contracts. Its ALU ownership/timing pattern is useful; its opcode, `XRAGEZeroPayloadContract`, 4K limits, private batch storage, and IF/API encoding are not a 16K generic consumer implementation.

This is a source audit at current `15b3506a`. No live source was changed. The finite scheduler checkpoint `b95c42d9a66e` is a requirements cross-check, not live gem5 evidence.

## Commit disposition

| Commit | Current relationship | Disposition |
|---|---|---|
| `531fbf34` (`LogicalStreamResponse`) | Not an ancestor; its files are absent. Subsequent commits repaired response ownership/accounting before the current transport replaced it. | Do not resurrect. Its exact tag/issue/ACK ledger is conceptually useful, but current `LogicalSPDCacheTransport` is the maintained authority. |
| `4bf5ef53` (live logical SPD slice) | Ancestor, followed by `6ebd25d4`, `66ca767c`, `35dfb514`, `382c022e`, `a5ff6016`, and `c6beb88f` fixes. | Reuse only the **current descendants** of bridge, transport, retry, port-provenance, and response-authentication hooks. |
| `e6325d40` (XRAGE zero payload) | Not an ancestor; `XRAGEZeroPayload.hh`, opcode 18, and direct-transform code are absent at HEAD. | Reuse the ALU handshake/timing idea selectively. Reject the XRAGE opcode and storage contract as the generic path. |
| `b59f85e2` (issue-ready forwarding) | Not an ancestor. | Reject. It publishes producer data before the backing ACK and copies it through a private scheduled-forward queue. |

## Exact reusable live hooks

### 1. Producer visibility from real backing ACKs

Reuse the current `IndirectAccessUnit` closure:

- `trackVirtualIteration()` counts logical and expected output words.
- `trackVirtualRetirementWrite()` binds an issued write key to exact pages/words.
- `retirementWriteComplete()` is called by `MAA::recvTimingResp()` only for a real `MemCmd::WriteResp`.
- `completeVirtualRetirementWrite()` increments completed page words.
- `markVirtualPageReadyIfComplete()` calls `MAA::setVirtualPageReady()` only when scanned, issued, and completed counts all close exactly.
- `boundedRetirementComplete()` prevents the producer instruction from reaching `Status::Response` while a combiner word or acknowledged retirement write remains live.

This is the correct visibility boundary. Registration (`resetVirtualPageReady()`), write issue, cache request acceptance, and `my_fill_finished` are not visibility.

Required adaptation: the current page token exposes generation/readiness but not an exact per-page backing transaction identity. The generic bridge must latch a unique producer transaction for each page and accept the notification only from the matching final `WriteResp`; a boolean page-ready callback alone is insufficient authentication.

### 2. Four finite cache-line buffers and credits

Reuse/factor the current `LogicalSPDCacheTransport`:

- Constants: `ResponseCredits == 4`, `LineBytes == 64`, four `lineBuffers`, and four `creditOwners`.
- `prepare()` allocates exactly one free credit, constructs an incarnated request/route token, and snapshots a write payload when applicable.
- `sendPrepared()`, `recvReqRetry()`, and `resumeLocalCapacity()` preserve refused-request ownership.
- `lookupToken()`, `wireExact()`, `precommitReceive()`, and `receiveAuthorized()` authenticate request identity, route token, port, address, command, and size.
- `commitDeliveryAuthorized()` and `ackReleaseAndRefill()` provide one-time delivery/ACK bookkeeping; `releaseRecord()` is the sole credit release.
- `assertInvariants()` and `creditsInUse()` provide the finite-state audit boundary.

The live adapter hooks in `MAA` are also reusable: `makeLogicalSPDPacket()`, `recvLogicalSPDTimingResp()`, `serviceLogicalSPD()`, `sendPacketCache()`, `LogicalSPDCacheLiveAdapterState`, and `LogicalSPDCachePortProvenance::responseMatches()`.

Required adaptation: current fill delivery immediately copies the line into a 2K/4K private page slot, then releases the credit. A direct consumer must instead retain that same credit and 64-byte buffer across read response, ALU ownership, destination request acceptance, and exact destination `WriteResp`. Factor the record/credit machinery; do not instantiate a second transport or copy into a new queue.

### 3. Line-fed scalar ALU timing

The reusable historical pattern is the `e6325d40` implementation of:

- `ALUUnit::canStartDirectTransform()` and `MAA::claimALUForDirectTransform()` for exclusive ALU ownership;
- `ALUUnit::startDirectTransform()` for `ceil(words / lanes) * ALU_lane_latency` timing and scalar snapshotting;
- `ALUUnit::executeInstruction()` for completion notification;
- `directTransformReady()`, `directTransformData()`, and `consumeDirectTransformWord()` for backpressured result handoff; and
- `IndirectAccessUnit::drainVirtualResponses()` for width/bank/backpressure accounting before combiner insertion.

Reuse the ownership and charged-latency semantics, not the storage. The XRAGE version creates a lane-entry payload array and batch copies detached from the four line credits. The generic implementation must transform the owned transport line buffer in place (or through an explicitly charged existing ALU register slice), keep one ALU token, use the instruction's real datatype/operation, and preserve the captured scalar across the whole operation.

Current `LogicalSPDCacheRuntime::driveCompute()` is **not** reusable as timing evidence: `executeCompute()` invokes `LogicalSPDCacheDatapath::transform()` synchronously over the private page payload without charging `ALUUnit` latency or contention.

### 4. Aligned full-line destination writes with real completion

Reuse/factor the current acknowledged retirement path:

- `IndirectAccessUnit::drainVirtualCombiner()` recognizes a complete aligned line.
- `validateRetirementWriteRange()` enforces registered destination coverage.
- `createRetirementWrite()` creates a 64-byte `MemCmd::WriteReq`, records exact-address ownership and page metadata, and calls `MAA::sendPacket(... force_cache=true, force_retirement_cache=true)`.
- `MAA::sendPacket()` and `my_outstanding_pkt_map` serialize all same-address traffic behind a retirement owner.
- `MAA::sendOutstandingCachePacket()` sends retirement traffic through `sendPacketRetirementCache()` and retains response-requiring ownership.
- `MAA::recvTimingResp()` accepts the real `WriteResp`, removes the outstanding owner, releases deferred traffic, and calls `retirementWriteComplete()`.

For the generic consumer, factor these hooks so the transport buffer—not the virtual producer combiner—owns the 64-byte write until its exact response. A buffer must not be freed on write issue or cache acceptance.

The retirement caches are explicitly configured coherent children of `tol3bus`; they are not hidden state. They are nevertheless extra configured cache capacity (default 1 KiB per core), so an iso-area claim must debit them or use the ordinary coherent cache-side path. A separate unaccounted destination cache is forbidden.

### 5. Fallback partial-line RMW

Reuse the current `StreamAccessUnit` semantics for ineligible lines:

- `createReadPacket()` uses `MemCmd::ReadExReq` for stores.
- `recvData()` copies the fetched line, replaces only selected words from the SPD source, and emits `MemCmd::WritebackDirty`.
- `RequestTable` preserves exact destination-line/word membership.

This is the required fallback for unaligned endpoints, predicates/holes, short spans, or any line not overwritten byte-for-byte. It is not reusable as strict write-completion closure: `WritebackDirty` has no response and `writePacketSent(..., true)` counts transport acceptance as completion. A fused architectural operation that promises real completion must either upgrade the fallback terminal write to a response-bearing coherent request or delay retirement behind an equivalent downstream completion authority.

### 6. Architectural retirement

Reuse the current terminal sequence:

- `IndirectAccessUnit::boundedRetirementComplete()` and the `Status::Response` invariants close source responses, combiners, outstanding writes, page accounting, and exact-once range accounting.
- `MAA::finishInstructionCompute()` publishes destination readiness and releases the owning functional unit.
- `IF::finishInstructionCompute()` invalidates the IF entry and wakes dependent source/condition operands.
- `IF::completion_only_tiles` prevents a virtual completion token from being consumed as SPD payload.

The generic consumer must reach this sequence only after every line is ACKed (full-line direct path) or equivalently completed by the fallback. Early producer-page visibility may enable consumer issue, but it must never retire the consumer or publish its destination.

`LogicalSPDCacheGem5Bridge::operationComplete()`, `completeOperation()`, and `acknowledgeCallback()` are reusable lifecycle patterns, but the current operation completion is coupled to the private-page runtime and synchronous datapath. It cannot be used unchanged as proof that the generic ALU and destination writes completed.

## XRAGE-specific fusion versus the generic consumer

`e6325d40` assumes one strict opcode: 4,096 logical entries, streamed 32-bit B indices, FP64 A and C, scalar multiply, no predicate, one index partition, non-overlapping registered regions, a completion-only tile, at most 64 ALU lanes, and the exact capacities checked by `XRAGEZeroPayloadContract::validate()`. Its `INDIR_LD_VIRTUAL_INDEX_SCALAR` decode, `src4RegID` encoding, IF range hazards, and `isZeroPayloadXRAGE()` checks belong to XRAGE only.

The generic path starts after an accepted 16K reorder producer has placed values in coherent backing. It does not consume B, does not reproduce gather ordering, and does not erase architectural producer backing. It applies an independently specified line-local operation to acknowledged backing lines and writes a distinct destination. Eligibility is therefore based on line locality, complete overwrite, registered non-aliasing spans, datatype/operation support, and exception/replay policy—not XRAGE's index or 4K opcode contract.

## Semantic hazards and rejected reuse

- **Reject `b59f85e2`:** `scheduleRetirementForward()` copies bytes from the producer's outstanding write packet before its ACK; `serviceRetirementForwards()` injects them directly into `StreamAccessUnit::recvData()`. This creates producer visibility outside the coherent backing response and adds a private forwarding queue. Exact-address serialization does not turn pre-ACK data into acknowledged architectural state.
- **No hidden logical page payload:** `LogicalSPDCacheRuntime` owns `2 * 2048 * 8 = 32,768` private payload bytes per MAA, and `driveCompute()` operates on them. The direct path may reuse its four line buffers only by factoring them out; it may not silently instantiate the 32 KiB runtime payload in addition to the hybrid's existing SPD/backing state.
- **No duplicate line payload:** do not add XRAGE lane batches, a second response array, a scheduled-forward payload, or a write-copy queue beside the four credits. Any extra register/SRAM capacity must be explicit and debited.
- **Preserve coherence:** producer reads begin only after matching page write responses; destination writes use coherent ports; same-address requests remain serialized; partial updates acquire exclusive line state; request and response ports must match; backing and destination ranges must be registered and non-overlapping.
- **Preserve stale-event rejection:** generation, producer transaction, action, line, buffer/credit, request incarnation, route token, address, size, command, and callback port must all match before state changes.
- **Preserve hazards:** a completion token is control, not data. IF and invalidator range tracking must cover producer backing reads and destination writes, including RAW/WAR/WAW conflicts with non-fused MAA operations and CPUs.
- **Preserve faults/retry/squash:** immediate translation assumptions in the logical live slice are not a generic exception model. Failed translation, request refusal, abort, and stale response must not lose a credit or publish completion.

## Minimum live source closure

The smallest honest core change is the following existing-file set; fewer files either duplicates machinery or leaves a semantic boundary unwired:

1. `src/mem/MAA/IndirectAccess.hh` and `.cc`: export authenticated per-page producer ACK transactions and keep current producer closure unchanged.
2. `src/mem/MAA/LogicalSPDCacheTransport.hh` and `.cc`: factor the four credits/line buffers into a line action whose credit survives read, ALU, and destination ACK.
3. `src/mem/MAA/ALU.hh` and `.cc`: add the generic line-buffer handshake while charging existing lane latency/ownership.
4. `src/mem/MAA/MAA.hh` and `.cc`: own the fused execution, route exact requests/responses/retries, select direct versus fallback, and call architectural completion only after closure.
5. `src/mem/MAA/StreamAccess.hh` and `.cc`: expose the partial-line RMW fallback and give its terminal store a real completion authority for the fused operation.
6. `src/mem/MAA/IF.hh` and `.cc`, `src/mem/MAA/CpuSidePort.cc`, and `src/mem/MAA/Invalidator.cc`: carry producer generation/transaction and destination geometry, enforce memory/register/token hazards, and retire/wake dependencies exactly once.

`CacheSidePort.cc`, `MAA.py`, `MAAConfig.py`, and `Options.py` need no semantic change if the implementation reuses the existing four cache ports, retirement ports, ALU geometry, and fixed four credits. Any new capacity or selectable behavior makes the corresponding configuration/accounting changes mandatory. A reachable software ABI additionally requires the existing API header/benchmark descriptor path plus focused unit and live tests; those are validation surface, not substitutes for the core closure above.

## Handoff gate

A live candidate is not promotable until matched evidence proves exact output, four-credit high water, no hidden payload, per-page producer ACK provenance, read/ALU/write issue-completion closure, real full-line write responses, exercised partial-line fallback, stale/retry tests, coherent CPU observation, and architectural retirement after the final ACK. The optimistic timing bound in `b95c42d9a66e` does not satisfy this gate.
