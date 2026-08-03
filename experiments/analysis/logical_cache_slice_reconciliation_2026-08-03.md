# Two-slot logical SPD-cache slice reconciliation

Date: 2026-08-03

Clean implementation base: `2354407a50ba4aa2fd127ee15493bf141b289dc2`

Clean production ancestor: `9fcb18c4cabb782975c68b6a8f484364f8987637`

Provisional slice examined: `02b6e8002c201c46876d1efb1c4e53cb866b0d2e`

Rejected response ancestors: `531fbf3`, `42e38a9`, `90822c0`, `0c3710a`

## Verdict

**READY TO IMPLEMENT AFTER TRANSPORT GO.** The useful control and datapath
semantics in `02b6e80` can be re-expressed on `2354407`, but none of the five
commits on the rejected branch is a cherry-pick candidate. The clean base
already contains the accepted logical opcode ABI, the two-descriptor/four-page/
two-slot controller, and the private two-slot FP64 allocation. The provisional
slice adds a valuable four-page full-overwrite schedule, captured-scalar FP64
datapath, and exact host oracle. Its fill/writeback implementation, however,
depends on the rejected `LogicalStreamResponse.hh` plus native
`StreamAccess.*`/`Port.cc` ownership.

`2354407` is only an architecture contract and Python safety model. Its own
status says that production work requires a fresh review
([design lines 9-21](logical_cache_dedicated_transport_design_2026-08-02.md#L9))
and its executable model explicitly excludes compute semantics
([model lines 699-708](logical_cache_dedicated_transport_model.py#L699),
[980-996](logical_cache_dedicated_transport_model.py#L980)). Therefore this
verdict authorizes planning only. Implementation starts only after an explicit
independent **GO dedicated transport** verdict. It makes no correctness,
performance, cache-latency, or coherence claim.

## Source-grounded baseline and lineage

The rejected branch is linear:

```text
9fcb18c
  -> 531fbf3 -> 42e38a9 -> 90822c0 -> 0c3710a -> 02b6e80

9fcb18c -> ...analysis-only commits... -> 2354407
```

The merge base of `2354407` and `02b6e80` is `9fcb18c`. The only changes from
`9fcb18c` to `2354407` are the dedicated-transport design, Python model, and
model tests. Thus the accepted production ABI/controller/private-slot source on
`2354407` is still the clean `9fcb18c` source; no rejected response source is
present.

The accepted substrate is:

- `LogicalSPDCacheController<>` defaults to two descriptors, four pages, and
  two slots, uses fixed arrays and nonzero generations
  ([controller lines 12-42](../../src/mem/MAA/LogicalSPDCacheController.hh#L12),
  [45-68](../../src/mem/MAA/LogicalSPDCacheController.hh#L45)).
  `reserveFullOverwrite` atomically obtains a resident source, a distinct
  destination slot, two managed leases, and compute/writeback serials
  ([384-440](../../src/mem/MAA/LogicalSPDCacheController.hh#L384)). Exact
  fill and writeback completions require the slot, page generation, and serial
  ([615-654](../../src/mem/MAA/LogicalSPDCacheController.hh#L615)).
- `LogicalSPDHiddenPayloadLayout` fixes two logical slots per MAA, two FP32
  lanes per FP64 slot, and 4,096 elements per lane
  ([layout lines 15-38](../../src/mem/MAA/LogicalSPDHiddenPayload.hh#L15)).
  Hidden IDs are derived after the visible range
  ([79-105](../../src/mem/MAA/LogicalSPDHiddenPayload.hh#L79)); `SPD` keeps
  the mapping private while public `check_tile_id` rejects every hidden ID
  ([SPD.hh lines 49-68](../../src/mem/MAA/SPD.hh#L49)). The constructor
  appends and initializes the hidden storage
  ([SPD.cc lines 279-330](../../src/mem/MAA/SPD.cc#L279)).
- `LogicalSPDCacheABI` fixes two descriptors, opcode 8, 16K logical elements,
  and a disjoint `src2=0xff` high-byte discriminator
  ([ABI lines 19-29](../../include/gem5/maa_logical_spd_cache_abi.hh#L19),
  [142-174](../../include/gem5/maa_logical_spd_cache_abi.hh#L142)). Shape and
  complete destination-span validation are shared with the guest API
  ([176-253](../../include/gem5/maa_logical_spd_cache_abi.hh#L176)). The CPU
  decoder validates and then deliberately panics before admission
  ([CpuSidePort.cc lines 365-424](../../src/mem/MAA/CpuSidePort.cc#L365)).
- The guest helper writes the accepted four-word logical opcode and currently
  promises that simulator admission is disabled
  ([MAA_gem5.hpp lines 237-277](../../benchmarks/API/MAA_gem5.hpp#L237)).
- Native traffic is address-owned: `MAA::sendPacket` dereferences `pkt->req`,
  enters `my_outstanding_pkt_map`/`my_deferred_pkt_map`, coalesces, and mutates
  unit counters ([Port.cc lines 29-77](../../src/mem/MAA/Port.cc#L29),
  [79-170](../../src/mem/MAA/Port.cc#L79)). Native response ownership is also
  address keyed and fans out to `IndirectAccessUnit` or `StreamAccessUnit`
  ([Port.cc lines 698-728](../../src/mem/MAA/Port.cc#L698)). The existing
  `CacheSidePort` calls that callback, changes its shared credit, and deletes
  the packet ([CacheSidePort.cc lines 30-40](../../src/mem/MAA/CacheSidePort.cc#L30)).
  Dedicated logical traffic must enter none of these functions or containers.

## File-by-file disposition of `02b6e80`

This table includes all 19 paths changed by the commit, including its analysis
and gate files so that no part of the commit is silently inherited. Line
references of the form `02b6e80:path:Lx-Ly` refer to that commit's tree.

| `02b6e80` path | Disposition | Symbols and dependency-grounded action |
| --- | --- | --- |
| `experiments/analysis/logical_spd_cache_vertical_slice_design_2026-08-02.md` | **DROP** | Do not replay lines 9-81, which report provisional implementation evidence built on the rejected response branch. Retain the earlier accepted design already on `2354407`; this reconciliation supersedes the provisional status text. |
| `experiments/scripts/run_logical_spd_cache_vertical_slice_unit.sh` | **KEEP AS-IS** | Lines 1-22 are a suitable optimized plus ASan/UBSan host runner and source-contract invocation. It names only the vertical host test. Additional transport/bridge gates need separate runners rather than widening this one. |
| `experiments/tests/test_logical_spd_cache_abi_contract.py` | **DROP** | The diff at lines 106-136 removes the assertion that `CpuSidePort.cc` fails closed. Keep the accepted test until stage 4 has a reviewed source-registration, waiter, drain, and dedicated-transport admission edge. Then replace it in the final test commit; do not replay this deletion. |
| `experiments/tests/test_logical_spd_cache_vertical_slice_contract.py` | **PORT WITH CHANGES** | Keep fixed geometry/admission/scalar/final-ACK assertions (`02b6e80:...:L22-L55,L82-L121`). Replace lines 13-20 and 57-80, which require `StreamAccess.cc`, `Port.cc`, `LogicalStreamResponse`, and `micro.logicalResponseManaged`, with deny-list assertions for native files and positive assertions for `LogicalSPDCacheTransport` and `LogicalCacheSidePort`. |
| `src/mem/MAA/ALU.cc` | **PORT WITH CHANGES** | Preserve FP64 captured-scalar semantics and ADD/SUB/MUL/DIV/MIN/MAX from `executeLogicalInstruction` (`02b6e80:...:L903-L1009`). First move the pure element transform to a standalone datapath. Later add a narrowly tagged ALU bridge. Do not key live ownership on `Instruction::logicalResponseManaged`, reuse `backingAddr` as scalar authority, or grant ALU direct general access to hidden lanes (`L53-L61,L88-L92,L903-L1022`). |
| `src/mem/MAA/ALU.hh` | **PORT WITH CHANGES** | The `executeLogicalInstruction` declaration at `02b6e80:...:L72` is useful only after the standalone datapath and an unforgeable MAA-owned action are defined. Rename/retype it to consume that exact action instead of the rejected instruction marker. |
| `src/mem/MAA/CpuSidePort.cc` | **PORT WITH CHANGES** | The only change deletes the accepted panic at `02b6e80^:...:L420-L424`. Remove that panic only in stage 4 and replace it with an atomic enqueue/admission result. Until then keep the clean fail-closed decoder. Validation must still precede mutation and dispatch. |
| `src/mem/MAA/IF.hh` | **PORT WITH CHANGES** | Keep logical descriptor/generation identity and add an explicit internal micro-op discriminator. The added `isLogicalControllerMicroOp` (`02b6e80:...:L210-L218`) depends on `logicalResponseManaged`, which came from rejected ancestor `531fbf3`, and overloads transparent-controller fields. It must not be copied verbatim. |
| `src/mem/MAA/LogicalSPDCacheController.hh` | **KEEP AS-IS** | The forward-declared test peer/friend (`02b6e80:...:L11-L12,L46-L47`) and pure `canAllocateMemorySerials` query (`L772-L777`) do not depend on ports, packets, payload, or rejected response state. Port these exact small additions manually, not by cherry-picking the commit. The query protects the slice's 12 controller serials; dedicated packet/action identities remain a separate reservation. |
| `src/mem/MAA/LogicalSPDCacheSlice.hh` | **PORT WITH CHANGES** | Keep geometry, descriptor roles, full-span/overlap/type/op admission, nonwrapping operation/producer IDs, four-page sequencing, exact compute reservation/completion, and drain/cleanup intent (`02b6e80:...:L16-L191,L203-L312,L465-L581,L645-L797`). Remove the include and member of `LogicalStreamResponse.hh` (`L10,L800`) and every `LogicalStreamTransactionTag`, ledger, issue-window, and response method (`L123-L140,L312-L464,L673-L763`). Replace that boundary with a transport-neutral `PageAction` and one exact completion/abort callback from the dedicated transport. |
| `src/mem/MAA/LogicalStreamResponse.hh` | **REPLACE BY DEDICATED TRANSPORT** | The added `issueDirectWriteLine` (`02b6e80:...:L528-L544`) modifies a file created by `531fbf3`. None of this header exists on `2354407`; its tag, wrapper, counter, ledger, and sender-state authority are rejected. `LogicalSPDCacheTransport.{hh,cc}` must instead own fixed records, FIFO, pending retry owner, credits, packet IDs, exact response validation, and abort-drain. |
| `src/mem/MAA/MAA.cc` | **PORT WITH CHANGES** | Keep the conceptual per-MAA slice initialization, source/admission checks, captured scalar, memory-before-compute schedule, exact compute completion check, and delayed high-level response (`02b6e80:...:L140-L146,L657-L779,L1044-L1167,L1249-L1262,L1372-L1461`). Replace `logicalStream*`, `StreamAccessUnit`, `LogicalStreamTransactionTag`, and `logicalResponseManaged` edges. Memory actions go only to the new transport; ALU completion returns an exact MAA-owned compute action. Add real drain/checkpoint/waiter lifecycle before enabling CPU admission. |
| `src/mem/MAA/MAA.hh` | **PORT WITH CHANGES** | Keep fixed per-MAA ownership and one high-level owner (`02b6e80:...:L451,L477-L488,L515-L541`). Replace response-tag APIs and `LogicalStreamTransactionTag` runtime fields with `LogicalSPDCacheTransport` and `LogicalSPDComputeAction`; own a vector of distinct `LogicalCacheSidePort` objects. Do not import any rejected response header. |
| `src/mem/MAA/Port.cc` | **DROP** | The post-retirement call to `logicalStreamResponseReceived` (`02b6e80:...:L1152-L1163`) is precisely an edge from the slice back into rejected shared ownership. `Port.cc` must remain byte-identical to `2354407`. |
| `src/mem/MAA/SPD.cc` | **PORT WITH CHANGES** | Keep the split visible/unchecked write-latency helper and hidden-slot validation/prepare/size behavior (`02b6e80:...:L54-L82,L269-L333`). Replace friends' tile-ID-based raw access with a constrained `{maa,slot,element}` private interface, prove alignment and bounds, and preserve visible checks and existing wakeups. Dedicated fill/writeback and the ALU bridge may use it; native stream code may not. |
| `src/mem/MAA/SPD.hh` | **PORT WITH CHANGES** | Keep private hidden accessors and preparation/latency declarations (`02b6e80:...:L53-L85`) but do not friend `StreamAccessUnit`. Avoid accepting a caller-supplied hidden tile ID and avoid `reinterpret_cast<T *>` as the storage contract until alignment/aliasing is proved. Public `getData`/`setData` and visible bounds stay unchanged. |
| `src/mem/MAA/StreamAccess.cc` | **REPLACE BY DEDICATED TRANSPORT** | `executeLogicalInstruction`, `createLogicalPacket`, `recvLogicalData`, and ledger callbacks (`02b6e80:...:L394-L530,L585-L608,L685-L706,L758-L824`) route logical packets through `MAA::sendPacket` and the rejected response ledger. Preserve no production code from these hunks. The standalone host datapath may reuse their line-copy idea; live packet movement belongs only to the new dedicated transport. |
| `src/mem/MAA/StreamAccess.hh` | **REPLACE BY DEDICATED TRANSPORT** | Drop the new slice include and logical packet/data methods (`02b6e80:...:L12,L174-L177`). `StreamAccess.*` remains byte-identical to `2354407`. |
| `tests/maa/logical_spd_cache_vertical_slice_test.cc` | **PORT WITH CHANGES** | Keep the guarded 16K source/destination oracle, two slot buffers, six scalar functions, four-page A/B schedule, distinct source/destination slots, delayed final write ACK, exact line totals, admission/exhaustion, and cleanup checks (`02b6e80:...:L59-L170,L220-L385,L388-L518`). Replace `LogicalStreamResponse` fault/counter calls and the test's manual line ledger with a dedicated transport plus mock peer. Do not access controller private state through production friends for ordinary assertions; add bounded exhaustion injection hooks explicitly. |

There is no safe `git cherry-pick 02b6e80`: its `LogicalSPDCacheSlice.hh`
includes the ancestor-created response header, its MAA/IF/ALU code consumes
ancestor-created response symbols, and its `Port.cc` hunk assumes the final
ancestor response implementation.

## Narrowest compilable host vertical slice

The first implementation proof must be a dependency-light C++ host test, not
a gem5 object build. Its compile closure is exactly:

```text
src/mem/MAA/LogicalSPDCacheController.hh        accepted + small serial query
src/mem/MAA/LogicalSPDCacheDatapath.hh          new pure FP64 page transform
src/mem/MAA/LogicalSPDCacheSlice.hh             new transport-neutral schedule
src/mem/MAA/LogicalSPDCacheTransport.hh/.cc     new finite dedicated state
tests/maa/logical_spd_cache_vertical_slice_test.cc
tests/maa/support/logical_spd_cache_mock_peer.hh
```

It must not include `MAA.hh`, `IF.hh`, `SPD.hh`, `StreamAccess.hh`,
`LogicalStreamResponse.hh`, `mem/packet.hh`, or any gem5 port header. The mock
peer owns four 64-byte response buffers and models `ReadReq -> ReadResp`,
`WriteReq -> WriteResp`, send refusal/retry, reorder, and exact pointer-token
return. It is not a coherence model.

The smallest positive trace uses one FP64 ADD scalar operation; separate table
tests retain all six provisional operations. The vertical trace is:

1. Allocate source descriptor 0 and destination descriptor 1 with nonzero,
   nonwrapping generations. Register a complete aligned 128-KiB coherent source
   span and publish exactly four source pages.
2. For each page 0 through 3, fill one of two private
   `std::array<double,4096>` slots using 512 exact 64-byte `ReadResp`s. Slots
   alternate A/B. A fill is clean only after response 512.
3. Atomically reserve a distinct destination slot, pin both exact leases, run
   `dst[i] = src[i] + capturedScalar` for all 4,096 elements, and transition
   only that destination to dirty. The source stays clean and distinct.
4. Issue 512 response-bearing direct `WriteReq`s from the dirty slot. Delay the
   final response and prove the destination page is not ready, the slot is not
   reusable, and high-level completion is false at ACK 511. The exact final
   `WriteResp` publishes the page and releases the slot.
5. After four pages, require exactly 2,048 fill responses and 2,048 write ACKs,
   all 16K destination FP64 bit patterns and both guard words exact, the source
   unchanged, both descriptors still generation-correct, and completion
   occurring once after the 4,096th response event.

The dependency gate is a generated compiler dependency list plus symbol scan:
the closure may name none of `MAA::sendPacket`, `MAA::recvTimingResp`,
`sendOutstandingCachePacket`, `my_outstanding_pkt_map`,
`my_deferred_pkt_map`, `my_num_outstanding_*`, `RowTable`, or `OffsetTable`.
This host test proves only the bounded controller/datapath/transport composition;
it does not prove gem5 packet ownership or coherent writes.

## Ordered, disjoint implementation commits

### 0. Mandatory precondition: transport review

No source commit starts until an independent reviewer accepts or repairs the
`2354407` contract. Required decisions are response `PacketPtr` preservation or
an immutable route token, unknown-response disposal, direct `WriteReq` coherent
semantics, private-slot access, reset/destructor order, fairness, and saturating
diagnostics ([design lines 354-374](logical_cache_dedicated_transport_design_2026-08-02.md#L354)).

Failure or deferral of any of the first five decisions means **STOP
RECONCILIATION**; it is not permission to use native `Port.cc`.

### 1. Transport-independent controller/datapath extraction

Expected files, and no others:

- `src/mem/MAA/LogicalSPDCacheController.hh`
- `src/mem/MAA/LogicalSPDCacheDatapath.hh` (new)
- `src/mem/MAA/LogicalSPDCacheSlice.hh` (new)

`LogicalSPDCacheSlice` owns descriptor roles, spans, operation/scalar capture,
four-page sequencing, compute reservations, and drain intent. It exposes a
plain `PageAction` and consumes a plain exact completion/abort record. It owns
no packet, sender state, retry state, response bitmap, or port. The datapath
accepts exact source/destination slot spans and a captured FP64 scalar; it has
no `MAA`, `SPD`, `Instruction`, register-file, or event dependency.

Acceptance gate: C++17 `-Wall -Wextra -Werror -pedantic` syntax/host compile;
existing controller tests unchanged; fixed-array/no-allocation scan; admission
atomicity and generation/serial exhaustion; no forbidden include or symbol.

### 2. Standalone dedicated transport C++ state machine and mock peer

Expected files, and no others:

- `src/mem/MAA/LogicalSPDCacheTransport.hh` (new)
- `src/mem/MAA/LogicalSPDCacheTransport.cc` (new)
- `tests/maa/support/logical_spd_cache_mock_peer.hh` (new, fixed-capacity
  send/retry/reply peer with opaque request/response handles; no gem5 type)

Implement the reviewed fixed point: one action, eight records/FIFO entries,
four response credits/buffers, one pending/retry owner, two 512-bit
issued/ACK sets, pre-reserved nonwrapping record/action/packet identities, exact
field validation, and abort-drain. A queued record has no packet object; one
refused send retains the identical peer handle. Never use `std::map`,
`unordered_map`, `vector`, `deque`, or history/tombstones as authority.

Acceptance gate: standalone compile and invariants after every transition;
exact 512 response completion; retry retains the same handle; reordered good,
duplicate/stale/foreign/corrupt responses; wrong size/command/address/port/key;
identity exhaustion before mutation; abort in every state; responder silence
keeps drain blocked; ASan/UBSan/LSan. The accepted Python model may be used as
a reviewed differential oracle, not as production evidence merely because its
current 20 tests pass.

### 3. Distinct gem5 `RequestPort` bridge

Expected files, and no others:

- `src/mem/MAA/LogicalCacheSidePort.hh` (new)
- `src/mem/MAA/LogicalCacheSidePort.cc` (new)
- `src/mem/MAA/SConscript`

`LogicalCacheSidePort final : public RequestPort` adapts only
`sendTimingReq`, `recvReqRetry`, and `recvTimingResp`. It constructs/destroys
the gem5 `Request`, `Packet`, optional non-authoritative sender state, and four
line buffers under the corresponding fixed transport record. It never derives
from or calls `MAA::CacheSidePort`, `MAACacheRequestPort`, `MAAReqPacketQueue`,
or any function in `Port.cc`.

Acceptance gate: object compile with warnings as errors; synthetic timing peer
tests for accepted/refused send, exact retry endpoint, response replacement,
foreign callback disposal, and all pointer/request/sender/data lifetimes. If a
legal cache response replaces `PacketPtr` and no reviewed immutable route token
exists, STOP. No MAA/config connection exists in this commit.

### 4. MAA/config/datapath wiring

Expected files, and no others:

- `src/mem/MAA/MAA.hh`, `src/mem/MAA/MAA.cc`, `src/mem/MAA/MAA.py`
- `src/mem/MAA/IF.hh`, `src/mem/MAA/IF.cc`
- `src/mem/MAA/SPD.hh`, `src/mem/MAA/SPD.cc`
- `src/mem/MAA/ALU.hh`, `src/mem/MAA/ALU.cc`
- `src/mem/MAA/CpuSidePort.cc`
- `include/gem5/maa_logical_spd_cache_abi.hh`
- `benchmarks/API/MAA_gem5.hpp`
- `configs/common/MAAConfig.py`

Add `VectorRequestPort logical_cache_sides`, construct one distinct logical
port per connected logical endpoint, export it under a new `getPort` name, and
connect it to the same normal cache-side fabric without priority bypass. Own
fixed per-MAA slice/transport/runtime/waiter state. Map `{maa,slot}` to the
accepted private payload through the constrained SPD interface. Drive the pure
datapath through an exact MAA-owned compute action. Preserve existing port
names/indices and all native unit scheduling.

This stage must also define, not assume, a software-visible coherent-source
registration and a bounded generation-tagged logical-ready waiter operation.
The source command validates the entire 128-KiB span, allocates a nonzero
producer transaction, and publishes four pages only for an already-materialized
coherent source. It is not evidence for an indirect producer. The consumer
opcode panic in `CpuSidePort.cc` remains until registration, waiters, drain,
and completion ownership all compile together.

Acceptance gate: X86 object-only compile, Python config compile, exact port
count/connectivity assertions, disabled and enabled-but-idle state audit,
visible hidden-ID rejection, scalar capture before register reuse, no early CPU
response, bounded waiter exhaustion, and real MAA drain/quiescent checkpoint
methods. `MAA` may not inherit the default always-drained/empty-serialization
behavior shown at [SimObject lines 282-316](../../src/sim/sim_object.hh#L282).

### 5. Tests and gates

Expected files, and no others:

- `tests/maa/logical_spd_cache_vertical_slice_test.cc` (new, ported)
- `tests/maa/logical_spd_cache_transport_test.cc` (new)
- `tests/maa/logical_cache_side_port_test.cc` (new)
- `experiments/tests/test_logical_spd_cache_vertical_slice_contract.py` (new)
- `experiments/tests/test_logical_spd_cache_dedicated_edges.py` (new)
- `experiments/tests/test_logical_spd_cache_abi_contract.py` (replace its
  fail-closed expectation only now)
- `experiments/scripts/run_logical_spd_cache_vertical_slice_unit.sh` (ported
  unchanged from `02b6e80`)
- `experiments/scripts/run_logical_spd_cache_transport_unit.sh` (new)

Acceptance gate: optimized and sanitizer host suites; exact four-page oracle;
all finite-capacity and fault cases; `git diff --check`; all accepted ABI,
controller, hidden-payload, transparent, physical opcode-8, opcode-16, stream,
and virtualization unit gates. Source contracts require zero diffs in native
response files and zero forbidden call edges. Only after an independent review
of these results may anyone request a gem5 link/run authorization.

## Rejected response lineage: prohibited imports and detection

Do not cherry-pick any of the following, wholly or partially:

- `531fbf3`: `LogicalStreamResponse.hh`, `logicalResponseManaged`,
  `LogicalSPDTransactionState`, logical fields in `OutstandingPacket`/
  `DeferredPacket`, logical coalescing/counter changes in `MAA::sendPacket`,
  `StreamAccessUnit` response ledger/callbacks, and its response tests.
- `42e38a9`: the follow-on native ownership classification, sender-state
  release, and shared response-retirement repairs. They repair the rejected
  architecture rather than isolate it.
- `90822c0`: `LogicalStreamCounterEvent`,
  `decideLogicalStreamCounterUpdate`, and native send/response accounting
  changes. In the final rejected tree, reads relinquish the stream count only
  at accepted send
  (`0c3710a:LogicalStreamResponse.hh:L261-L295`), while an exact response marked
  unsent takes a fatal route after a response-abort decision
  (`0c3710a:Port.cc:L1047-L1122`); that combination is not a dedicated owner.
- `0c3710a`: `TimingResponseDisposition`,
  `invokeTimingResponseWrapper`, bounded sender-state chain probing, and all
  changes to `CacheSidePort.cc`, `MemSidePort.cc`, `MAA.hh`, or the normal
  `MAA::recvTimingResp`. It still dereferences `pkt->req` before its exact
  pointer scan (`0c3710a:Port.cc:L902-L927`), accepts `WriteResp` without an
  exact 64-byte size check (`L1057-L1059`, `L1179-L1181`), calls native owners
  before erasing the normal map entry (`L1214-L1242`), and accepts arbitrary
  residual non-logical sender-state chains when the bounded scan merely
  completes (`L1195-L1202`).
- `02b6e80`: its `Port.cc` controller callback, all `LogicalStreamResponse`
  additions, and all `StreamAccess.*` logical packet/data paths.

Run these static gates on every implementation commit:

```bash
git diff --exit-code 2354407 -- \
  src/mem/MAA/Port.cc src/mem/MAA/CacheSidePort.cc \
  src/mem/MAA/MemSidePort.cc src/mem/MAA/StreamAccess.cc \
  src/mem/MAA/StreamAccess.hh src/mem/MAA/IndirectAccess.cc \
  src/mem/MAA/IndirectAccess.hh src/mem/MAA/Tables.cc src/mem/MAA/Tables.hh

rg -n 'MAA::sendPacket|MAA::recvTimingResp|sendPacketCache|\
sendPacketRetirementCache|sendOutstanding(Cache|Mem)Packet|\
my_(outstanding|deferred)_pkt_map|my_num_outstanding_|\
LogicalStreamResponse|logicalResponseManaged|RowTable|OffsetTable|\
retirementWriteComplete|StreamAccessUnit::recvData' \
  src/mem/MAA/LogicalSPDCache* src/mem/MAA/LogicalCacheSidePort*
```

The only allowed timing call is the new bridge's inherited
`RequestPort::sendTimingReq`; the only allowed response callback is
`LogicalCacheSidePort::recvTimingResp`. The second gate must therefore have no
matches other than its explicitly reviewed own method declaration.

Dynamic bridge tests additionally snapshot native port credits/statistics and
test-only fingerprints of the native maps/queues/counters before and after
logical traffic. Coverage must show zero entries into `MAA::sendPacket`,
`sendOutstandingCachePacket`, `sendOutstandingMemPacket`,
`MAA::recvTimingResp`, `IndirectAccessUnit::{recvData,retirementWriteComplete}`,
and Row/Offset claim/lookup/cleanup methods. Any native delta or covered call
edge is a STOP.

## Unresolved risks that must be closed

### ABI and admission

- The accepted public validator permits six data types and 16 scalar operation
  encodings ([ABI lines 176-221](../../include/gem5/maa_logical_spd_cache_abi.hh#L176)),
  while `02b6e80` implements only FP64 ADD through MAX. The reviewed stage-4
  contract must reject non-FP64 and operations 6-15 in the shared validator or
  define a broader implementation; a late dispatch panic is not an ABI.
- Preserve opcode 8 and the `src2=0xff` discriminator. Physical all-zero high
  bytes and every non-logical instruction must remain byte-for-byte compatible.
- `Instruction` currently stores logical generations as `uint64_t`
  ([IF.hh lines 171-175](../../src/mem/MAA/IF.hh#L171)), while the controller
  generation is `uint32_t`; conversion must be checked and canonical.
- Capture scalar bits and all destination-span metadata before the guest may
  reuse its register or mutate front-end state. Busy/backpressure returns must
  be atomic and must retain the one CPU completion packet exactly once.

### Drain and checkpoint

- Current `MAA` has no `drain`, `serialize`, or `unserialize` override, so it
  inherits an always-drained and empty-checkpoint default
  ([SimObject lines 282-316](../../src/sim/sim_object.hh#L282)). The
  provisional slice's `requestDrain()` is not gem5 drain integration.
- Drain blocks new source registration, consumer admission, and waiters; it
  cancels only unsent dedicated records and waits for every sent record. Dirty
  writeback abort returns the slot to dirty and blocks checkpoint.
- Checkpoint is quiescent-only: no packet, credit, retry owner, action, lease,
  waiter, dirty slot, or high-level completion packet may remain. Serialize
  descriptor generations/roles/backing/type/readiness and nonwrapping identity
  allocators; restore transport/slots/waiters empty. Never reset generations or
  record epochs.

### Waiters and source/producer registration

- Existing readiness waiters are parallel dynamic vectors of `PacketPtr` and
  integer ready IDs ([MAA.hh lines 490-493](../../src/mem/MAA/MAA.hh#L490),
  [CpuSidePort.cc lines 527-582](../../src/mem/MAA/CpuSidePort.cc#L527)). They
  are not generation-tagged logical waiters. Add a fixed eight-entry logical
  table keyed by `{maa,logical,generation,page}` with exact packet ownership,
  full/backpressure behavior, drain exclusion, and one wakeup.
- `02b6e80` exposes only `MAA::registerLogicalSPDSource`; there is no caller,
  MMIO encoding, producer retirement identity, or waiter API. A reviewed
  source-registration command must exist before the logical consumer panic is
  removed.
- The first slice may import only an already coherent, fully materialized
  128-KiB source span. An indirect/gather producer remains out of scope until
  it publishes each page with exact
  `{logical,generation,producerTransaction,page}` after its own acknowledged
  retirement. Source registration must never masquerade as that proof.

### Hidden-slot allocation and datapath

- Retain exactly two FP64 slots per MAA and the accepted appended allocation;
  do not reserve visible SPD tiles or expose hidden MMIO addresses.
- Prove `tiles_data` alignment/aliasing for FP64 or use byte-copy access. Check
  every access by `{maa,slot,element}` and calculate the physical offset
  internally. Do not accept arbitrary hidden tile IDs from `Instruction`.
- Keep source and destination slots distinct and pinned through compute. A
  dirty slot cannot be evicted, reused, reset, destroyed, or published before
  all 512 exact write ACKs.
- The dedicated transport's four line buffers are separate from the two 32-KiB
  payload slots; charge and bound both. Do not reuse SPD `tiles_dirty`, visible
  wait vectors, or stream request-table state as logical authority.

### Native regressions and bridge integration

- Adding a requestor/port changes xbar arbitration and routing-table pressure
  even when native source files are untouched. Prove disabled and
  enabled-but-idle configurations preserve native port counts, names, indices,
  callbacks, counters, and behavior. Logical traffic receives no priority
  bypass.
- Prove exact `ReadReq -> ReadResp` and 64-byte `WriteReq -> WriteResp`
  semantics through hit, miss, retry, and reordered response. The Python model
  cannot prove cache coherence, packet replacement, or unknown-packet disposal.
- Destructor order must seal only a drained transport. Never silently delete an
  in-flight packet. A nonreturning responder safely blocks drain rather than
  freeing an owner.
- A guest must not forge the internal compute marker. Keep high-level logical
  operands separate from MAA-owned physical hidden-slot actions, and match the
  complete operation/action/generation/page/slot/scalar identity on completion.

## Stage gates and pre-gem5 STOP condition

| Stage | Acceptance gate | Immediate STOP |
| --- | --- | --- |
| Transport review | Independent GO with pointer/token, unknown response, command/coherence, private access, and teardown questions resolved | Review rejects or leaves gates 1-5 unresolved |
| 1: controller/datapath | Standalone Werror compile; fixed storage; exact atomic admission/compute transitions; no forbidden dependencies | Dynamic owner state, partial mutation, generation/serial reuse, or native include/symbol |
| 2: dedicated transport | Optimized + sanitizers; same-handle retry; exact 512 ACK set; all corruption/abort/drain/exhaustion cases | Lost/duplicated owner, wrap, bad response mutation, abandoned sent handle, or unbounded state |
| 3: bridge | Werror object compile; exact lifetime tests; response pointer or reviewed immutable token preserved | Fallback to address/RequestPtr/sender wrapper, unknown disposal ambiguity, or native callback |
| 4: wiring | Object-only compile; Python config compile; producer/waiter/drain/checkpoint contract; hidden access; no early CPU response | Consumer enabled without producer/waiter/drain, hidden visible, native file edit, or idle-state delta |
| 5: tests | All host/unit/source/native-disabled gates and independent code review pass | Any native call edge/delta, early completion/reuse, byte/guard mismatch, sanitizer issue, or live checkpoint state |

**STOP before any gem5 link or run** unless all five implementation-stage gates
pass, the implementation diff contains none of the prohibited response source,
and a fresh reviewer explicitly authorizes the bridge integration step. At that
checkpoint, the remaining first gem5 test is itself a pointer/coherence/
callback-routing experiment; failure stops integration and may not be repaired
by routing logical packets through native maps. Timed or performance evidence
is a later, separately authorized activity.

## Changed-file surface and conflict hotspots

The exact staged list above has an expected unique surface of **30 files**:
21 production/config/API/build files and nine test/gate/support files. Six
production files are new and logically scoped (`LogicalSPDCacheDatapath.hh`,
`LogicalSPDCacheSlice.hh`, transport `.hh/.cc`, and bridge `.hh/.cc`); the
other 15 are shared edits or the build manifest. `SConscript` adds only the two
new `.cc` files. Splitting source registration or logical waiters into their
own source/test files would raise the estimate and requires a plan amendment,
not an opportunistic extra file.

Likely conflict hotspots, highest first:

1. `MAA.hh`/`MAA.cc`: constructor/destructor, `getPort`, scheduling,
   instruction ownership, CPU completion, drain, and serialization all meet.
2. `CpuSidePort.cc`, `IF.hh/.cc`, shared ABI, and `MAA_gem5.hpp`: accepted
   fail-closed opcode shape must become an atomic live contract without changing
   physical encodings.
3. `SPD.hh/.cc` and `ALU.hh/.cc`: private hidden access, existing latency/
   wakeup semantics, and the internal compute branch.
4. `MAA.py`/`MAAConfig.py`: vector port counts, connection topology, requestor
   identity, snoop-filter/routing capacity, and disabled compatibility.
5. Tests that currently assert fail-closed admission: update only in final stage
   so intermediate commits cannot accidentally activate the ABI.

`Port.cc`, `CacheSidePort.cc`, `MemSidePort.cc`, `StreamAccess.*`,
`IndirectAccess.*`, and `Tables.*` are not conflict hotspots because they are
explicit no-edit sentinels. Any need to edit them invalidates the planned
isolation and returns the verdict to STOP.

## Reconciliation handoff

Start from `2354407`, wait for the dedicated-transport review verdict, and then
implement the five commits in order. Manually re-express only the rows marked
KEEP or PORT; create the dedicated files for every REPLACE row; apply every DROP
as an explicit negative source gate. The first meaningful artifact is the
standalone C++ two-descriptor/two-slot/four-page FP64 host trace. The last
pre-gem5 artifact is an independently reviewed zero-native-edge report. Neither
artifact is simulator correctness or performance evidence.
