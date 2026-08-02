# Logical SPD cache: first honest vertical slice

Date: 2026-08-02
Audited tree: `a63ac39` (`mem: add atomic logical SPD overwrite pair`)
Status: implementation design only. This document changes no simulator or
benchmark source and authorizes no gem5 run.

## Decision and narrow boundary

Implement one bounded, per-MAA FP64 scalar full-overwrite path:

```text
one coherent 16K-element logical source S
  -> four source-page fills
  -> four ordinary physical 4K-element ALU_SCALAR micro-ops
  -> four acknowledged full-page destination writebacks
  -> one distinct 16K-element logical destination D
```

`S` and `D` have different logical IDs, backing ranges, and generations.
`D` is allocated empty, never fetched, and a page becomes ready only after
the matching response to every 64-byte write for that page. This is a
functional vertical slice, not a replacement cache architecture, a performance
model, a producer conversion, or an area claim.

The first source is a coherent, already-materialized backing-memory range. Its
four pages become ready together only after its complete 128-KiB range has been
validated. This deliberately excludes an `IndirectAccess` producer from the
first patch. A later producer integration must carry
`{logical,generation,producerTransaction,page}` on page publication and may
not use this synchronous source declaration as evidence that retirement writes
completed.

The sole supported operation is FP64 `ALU_SCALAR` with a full-page
overwriting operation: `ADD`, `SUB`, `MUL`, `DIV`, `MIN`, or `MAX`.
Comparisons, masks/conditions, reductions, vector ALU, a second logical source,
partial destinations, and overlapping source/destination ranges are rejected
before controller mutation. The restriction makes the FP64 storage and
response-ledger bound exact.

## Current-source constraints

These are audited facts, not proposed interfaces.

- `LogicalSPDCacheController` is payload-free and fixed-array based. Its
  default point is two descriptors, four pages per descriptor, two slots, four
  miss entries, and four leases
  ([LogicalSPDCacheController.hh](../../src/mem/MAA/LogicalSPDCacheController.hh#L37)).
  It already uses a nonzero 32-bit generation and a 64-bit transaction serial
  ([lines 45-110](../../src/mem/MAA/LogicalSPDCacheController.hh#L45)).
- `reserveFullOverwrite` already atomically requires a ready resident source,
  a distinct unready destination, a distinct empty/clean slot, two leases, and
  compute/writeback serials
  ([lines 384-460](../../src/mem/MAA/LogicalSPDCacheController.hh#L384)).
  Its exact `Reserved -> Computing -> Dirty -> Writeback` completion rules
  are at [lines 463-654](../../src/mem/MAA/LogicalSPDCacheController.hh#L463).
- `pendingAction`, `acceptAction`, `completeFill`, and
  `completeWriteback` define the current deterministic action/response
  surface ([lines 524-654](../../src/mem/MAA/LogicalSPDCacheController.hh#L524)).
  General pins are finite and managed pair leases cannot be generically
  released ([lines 656-720](../../src/mem/MAA/LogicalSPDCacheController.hh#L656)).
- The header-only transparent controller is a separate one-descriptor scheduler
  with `validate`, `submit`, `pending`, `accept`, and `complete`
  ([TransparentSPDController.hh](../../src/mem/MAA/TransparentSPDController.hh#L94),
  [#L142](../../src/mem/MAA/TransparentSPDController.hh#L142),
  [#L173](../../src/mem/MAA/TransparentSPDController.hh#L173),
  [#L206](../../src/mem/MAA/TransparentSPDController.hh#L206),
  [#L239](../../src/mem/MAA/TransparentSPDController.hh#L239)). It is not a
  response-acknowledged logical cache.
- `MAA` constructs one SPD, stream unit, and ALU per MAA
  ([MAA.cc](../../src/mem/MAA/MAA.cc#L149),
  [#L158](../../src/mem/MAA/MAA.cc#L158),
  [#L171](../../src/mem/MAA/MAA.cc#L171)). The current transparent scheduler
  lives in `tryIssueTransparentMicroOp` and `finishInstructionCompute`
  ([MAA.cc](../../src/mem/MAA/MAA.cc#L912),
  [#L1102](../../src/mem/MAA/MAA.cc#L1102)).
- SPD is allocated as 32-bit lane tiles; FP64 accesses consume adjacent lanes
  ([SPD.hh](../../src/mem/MAA/SPD.hh#L47),
  [#L60](../../src/mem/MAA/SPD.hh#L60)). Its payload and tile metadata are
  allocated in [SPD.cc:254-286](../../src/mem/MAA/SPD.cc#L254).
  `tiles_dirty` is coherence state, not logical-cache dirty state
  ([SPD.cc](../../src/mem/MAA/SPD.cc#L105)).
- Native ALU execution already reads/writes physical SPD IDs
  ([ALU.cc](../../src/mem/MAA/ALU.cc#L74)); the logical path must generate a
  physical micro-op, not make legacy instructions name logical IDs.
- Normal stream stores form read/modify/write `WritebackDirty` packets
  ([StreamAccess.cc](../../src/mem/MAA/StreamAccess.cc#L398),
  [#L465](../../src/mem/MAA/StreamAccess.cc#L465)), and
  `writePacketSent` counts acceptance as completion
  ([#L388](../../src/mem/MAA/StreamAccess.cc#L388)). That is inadequate for
  the destination contract.
- Port ordering is address based at `sendPacket`,
  `sendNextDeferredPacket`, `sendOutstandingCachePacket`, and
  `recvTimingResp` ([Port.cc](../../src/mem/MAA/Port.cc#L49),
  [#L247](../../src/mem/MAA/Port.cc#L247),
  [#L499](../../src/mem/MAA/Port.cc#L499),
  [#L698](../../src/mem/MAA/Port.cc#L698)). An address is not enough identity
  for logical slot reuse.
- CPU MMIO currently ignores word-0's upper three bytes
  ([CpuSidePort.cc](../../src/mem/MAA/CpuSidePort.cc#L216)); word 2 normally
  dispatches an instruction ([#L288](../../src/mem/MAA/CpuSidePort.cc#L288));
  word 3 is currently restricted to virtual/fused forms
  ([#L315](../../src/mem/MAA/CpuSidePort.cc#L315)).
  `Instruction` has no logical identity today
  ([IF.hh](../../src/mem/MAA/IF.hh#L159)).
- The current virtual-page-ready mechanism is physical-token indexed
  ([MAA.cc](../../src/mem/MAA/MAA.cc#L1217),
  [#L1239](../../src/mem/MAA/MAA.cc#L1239),
  [#L1246](../../src/mem/MAA/MAA.cc#L1246)); it is not a
  generation-safe logical wait interface.

## Pending commits are inputs, not interfaces

Neither `57d8aae` nor `08dc106` is an ancestor of the audited tree.
They are not accepted ABI, response transport, or correctness authority.

| Commit | Inspected concept | Decision here |
| --- | --- | --- |
| `57d8aae` | Logical header discriminator and pre-mutation operand/range validation. | Keep the principles only. Do not adopt its overloaded opcode-8 image, headers, helper names, or enums. |
| `08dc106` | Full response tag, sender state, and bounded line ledger. | Keep full identity and line-exact ACK requirements only. Do not adopt its structures, port behavior, or response-owner API. |

The active independent response review is a further reason not to cherry-pick
either patch as an implied acceptance.

## Exact ABI: new opcode, legacy untouched

Use `LOGICAL_ALU_SCALAR = 17`. Do **not** overload opcode 8: old physical
software may place arbitrary values in currently ignored word-0 high bytes.
Adding a positive opcode keeps every old opcode-0--16 encoding and behavior
unchanged, including `ALU_SCALAR = 8` and
`VIRTUAL_TILE_ALU_SCALAR = 16`.

```text
word 0, bits 63:56: logical source ID       (0 or 1)
word 0, bits 55:48: logical destination ID  (0 or 1, different)
word 0, bits 47:40: LogicalABIv1 magic      (0xa1)
word 0, bits 39:32: opcode 17
word 0, bits 31:24: FLOAT64 datatype (5)
word 0, bits 23:16: {ADD,SUB,MUL,DIV,MIN,MAX}
word 0, bits 15: 0: physical destination IDs = 0xff, 0xff

word 1: src1 scalar register valid; all physical SPD IDs, other registers,
        destination registers, and condition fields = 0xff
word 2: source backing base, aligned to 8, complete 128-KiB registered span
word 3: destination backing base, aligned to 8, complete 128-KiB span
word 4: forbidden
```

Word 2 reuses `baseAddr`; word 3 reuses `backingAddr`. A small CPU decode
sidecar records `{srcLogical:uint16,dstLogical:uint16,magic:uint8,word3Seen}`
until word 3 arrives. It is not a reinterpretation of physical SPD fields.
`Instruction` gains exactly 8 bytes for logical IDs/flags/padding; a target
build must assert that delta.

Admission is atomic:

1. Validate every wire field, scalar-register range, 128-KiB spans, alignment,
   non-overlap, IDs, and operation before mutation.
2. A free source ID becomes `SourceLive` with all four pages ready. A live
   source must match base/type/span exactly. No speculative producer page is
   accepted.
3. Destination ID must be free; allocate it `DestinationProducing` with no
   ready pages. Capture the FP64 scalar bits now.
4. Allocate one nonzero operation ID, take source/destination descriptor
   references, and only then accept the high-level instruction.

A malformed software instruction is a pre-mutation `panic_if`, consistent
with current MMIO programming errors. A malformed or late asynchronous response
is classified and dropped, never turned into a panic merely because it arrived
late.

## Finite resources and identities

All capacities are compile-time constants; no new runtime knob is introduced.

| Resource per MAA | Capacity | Ownership |
| --- | ---: | --- |
| Logical descriptors | 2 | Exactly `S` and `D`. |
| Pages / descriptor | 4 | `16384 / 4096`. |
| Physical slots | 2 | Source page plus full-overwrite destination page. |
| Miss FIFO | 4 | Deduplicated full `PageKey`. |
| Lease records | 4 | Pair consumes exactly two managed leases. |
| Active logical operations | 1 | No operation queue. |
| Stream actions | 1 | Shares the one existing stream unit. |
| Line ledger | 512 lines/page | FP64 page = `4096 * 8 / 64`. |
| Logical line window / sender states | 8 / 8 | Bounds packets outstanding or deferred by one action. |
| Logical CPU waiters | 8 | Four generation-tagged waiters per descriptor. |

```text
DescriptorKey = { logical:uint16, generation:uint32 }                 // nonzero
PageKey       = { logical:uint16, page:uint16, generation:uint32 }
OperationID   = uint64, nonzero, unique per accepted opcode 17
ActionSerial  = uint64, nonzero, unique per fill/compute/writeback
ActionTag     = { OperationID, ActionSerial, generation,
                  maa:uint16, logical:uint16, page:uint16, slot:uint16,
                  action:uint8 }
```

`ActionTag` is an explicit 32-byte object:

```text
uint64 operationID       8      uint64 actionSerial  8
uint32 generation        4      uint16 maa           2
uint16 logical            2      uint16 page          2
uint16 slot               2      uint8 action         1
uint8 flags               1      uint16 reserved_zero 2
                                                   total 32 bytes
```

Generations, operation IDs, and action serials never wrap. Before accepting a
logical operation, reserve every serial it will need; keep the current
controller's preallocation of compute and writeback serials. Exhaustion fails
closed rather than reusing an old identity.

The sole ownership transitions are:

```text
descriptor: Free -> SourceLive | DestinationProducing -> DestinationLive
slot:       Empty -> Filling -> Clean
                     Clean -> Reserved -> Computing -> Dirty -> Writeback -> Empty
```

`access` queues one source page, `pendingAction` advertises the exact next
fill/writeback, and `acceptAction` begins it only after the single stream
ledger is acquired. A fill reaches `Clean` only after 512 exact read
responses. `reserveFullOverwrite` is the only pin/reserve operation:
generic `markDirty`/`release` keep rejecting its managed leases. Matching
ALU completion calls `completeOverwrite`; matching page-write ACK completion
calls `completeWriteback`. A descriptor cannot free while it has a reference,
waiter, queued page, slot, lease, action, or response obligation.

## Macro event timeline: one 16K S to one 16K D

Let hidden FP64 slots `A` and `B` each contain 4096 elements / 32 KiB. Let
`S.p` and `D.p` be page `p`, each with 512 cache lines. This specifies
ordering, not latency.

| Event | Exact state/action | Concrete hook |
| --- | --- | --- |
| T0 | CPU writes opcode-17 words 0--3; all fields and spans validate. | Extend `CpuSidePort::recvTimingReq` cases at [CpuSidePort.cc:182](../../src/mem/MAA/CpuSidePort.cc#L182). |
| T1 | `S:gS` becomes source-live; `D:gD` allocated empty; scalar and `OperationID O` captured. | New `MAA::admitLogicalScalar`, called through `dispatchInstruction` ([MAA.cc:979](../../src/mem/MAA/MAA.cc#L979)). |
| T2 | `access(S.0:gS)` queues a miss; advertise `Fill(A,S.0,F0)`. | Existing controller [access](../../src/mem/MAA/LogicalSPDCacheController.hh#L359), [pendingAction](../../src/mem/MAA/LogicalSPDCacheController.hh#L536), [acceptAction](../../src/mem/MAA/LogicalSPDCacheController.hh#L581). |
| T3 | Fill issues up to 8 tagged `ReadReq` lines at a time, until all 512 for `{O,F0,S.0,gS,A,Fill}` are sent. | New bounded stream method beside `createReadPacket` ([StreamAccess.cc:363](../../src/mem/MAA/StreamAccess.cc#L363)). |
| T4 | The 512th exact read response installs the line and calls `completeFill(A,S.0,F0)`. A is clean source. | New `logicalReadResponse`; route from [Port.cc:698](../../src/mem/MAA/Port.cc#L698). |
| T5 | `reserveFullOverwrite(S.0,D.0)` creates two managed leases and `C0,W0`; B is reserved destination. | Existing atomic pair [LogicalSPDCacheController.hh:397](../../src/mem/MAA/LogicalSPDCacheController.hh#L397). |
| T6 | Exact `beginOverwriteCompute`; generated physical opcode-8 ALU reads A, writes B, and uses captured scalar bits. | New logical scheduler dispatches existing `ALUUnit`; legacy ALU path unchanged. |
| T7 | Matching ALU completion calls `completeOverwrite`; pair leases release atomically and B is dirty. | Tag-aware branch in [MAA.cc:1102](../../src/mem/MAA/MAA.cc#L1102). |
| T8 | Advertise and accept preallocated `Writeback(B,D.0,W0)`; issue direct full-page response-bearing `WriteReq` lines. | New `StreamAccessUnit::beginLogicalWriteback`; never call normal store/RMW path. |
| T9 | 512th exact `WriteResp` calls `completeWriteback(B,D.0,W0)`; B becomes empty and `D.0` becomes ready. | Tag/line validation in `MAA::recvTimingResp`, then stream ledger terminal callback. |

The page schedule forces two physical slots to service all four logical pages:

| Page | Fill source | Reserve/compute destination | ACKed writeback | State after ACK |
| ---: | --- | --- | --- | --- |
| 0 | `S.0 -> A` | `D.0 -> B` | `B -> D.0` | A clean S.0; B empty |
| 1 | `S.1 -> B` | Evict clean S.0; `D.1 -> A` | `A -> D.1` | A empty; B clean S.1 |
| 2 | `S.2 -> A` | Evict clean S.1; `D.2 -> B` | `B -> D.2` | A clean S.2; B empty |
| 3 | `S.3 -> B` | Evict clean S.2; `D.3 -> A` | `A -> D.3` | A empty; B clean S.3 |

`D` is live only when its ready mask is `0b1111`. The high-level instruction
completes only then: neither admission, ALU completion, packet acceptance, nor
a response-less store is an architectural completion boundary. The full FP64
transform has exactly 2,048 tagged read responses and 2,048 tagged write
responses.

## True ACK ownership and bad-event behavior

Every logical packet pushes a fixed 48-byte sender state:

```text
ActionTag tag (32) + lineAddress:uint64 (8) + previousSenderState*:8
```

The state remains owned until its exact terminal response. Address lookup still
orders port traffic, but response identity is the sender-state pointer plus the
complete tag. Logical requests never coalesce.

On each response, `MAA::recvTimingResp` must: (1) find the address-ordered
outstanding request; (2) prove the sender-state pointer is its exact state;
(3) verify MAA/action/operation/serial/page/generation/slot/command/alignment
and an issued-but-unACKed line bit; (4) retire this one packet, return its
sender-state credit, and set exactly one ACK bit; and (5) notify the controller
only when `acked == issued == 512` and the line window is empty.

Normal `STREAM_ST` remains exactly as it is. Only logical writeback uses a
direct full-line `WriteReq` on the retirement cache path and waits for
`WriteResp`. It reads line data from the hidden SPD slot; it does not enter
`StreamAccessUnit::recvData`'s normal store/RMW branch.

| Event | Required result |
| --- | --- |
| Malformed opcode-17 input | Pre-mutation `panic_if`. |
| Tagged response with no outstanding request | Increment stale counter; free only that sender state; do not recreate state. |
| Wrong MAA/action/op/serial/page/generation/slot/address/command | Increment specific wrong counter; no mutation. |
| Already ACKed line | Increment duplicate counter; no mutation. |
| Correct tag for never-issued line | Increment malformed counter; no mutation. |
| Correct tag but impossible slot phase | Fatal internal invariant: implementation corruption. |
| Duplicate ALU/cancel/fill/writeback completion | Controller returns stale; record and do not mutate. |
| Port refusal/retry | Do not `acceptAction`; keep identical action advertised and retry via an existing issue/stream event. |

## Private hidden SPD and full byte accounting

Keep CPU-visible SPD exactly at its architectural lane count:

```text
visibleTileCount   = existing num_tiles
allocatedTileCount = visibleTileCount + NUM_MAAS * 2 slots * 2 FP64 lanes
hiddenBase(maa,slot) = visibleTileCount + maa * 4 + slot * 2
```

For `NUM_MAAS=4`, 16 FP32 lane tiles are appended. CPU data MMIO, physical
instruction checks, invalidator paths, and all legacy instructions still require
`tile < visibleTileCount`; only internal generated micro-ops may use the
hidden range. Thus **reserved visible SPD is 0 bytes**. The private hidden
payload is 256 KiB and must be charged, not described as free capacity.

The only capacity not otherwise fixed by the requested `NUM_MAAS=4` point is
the CPU front end.  The numerical total below fixes the first vertical test
configuration to `NUM_CORES=NUM_MAAS=4`; a general build adds 16 bytes for each
additional core (8-byte decode sidecar plus 8-byte `Instruction` extension).
All other records below are fixed arrays with required target-build
`static_assert(sizeof)` checks and exclude allocator headers.

The following separates private payload from controller metadata so neither is
counted twice.

| Added object | B / MAA | 4 MAAs | Exact calculation |
| --- | ---: | ---: | --- |
| Hidden FP64 payload | 65,536 | 262,144 | `2 * 4096 * 8` |
| Descriptor records | 64 | 256 | `2 * 32` |
| Slot records | 96 | 384 | `2 * 48`, tag + preallocated writeback serial |
| Miss FIFO, head/tail/count | 40 | 160 | `4 * PageKey(8) + 8` |
| Lease records | 128 | 512 | `4 * 32` |
| Active-operation record | 48 | 192 | one captured scalar/op/page-mask context |
| Stream-action record | 48 | 192 | tag, page backing base, counts/window |
| Issued + ACK bitmaps | 128 | 512 | `2 * 512 / 8` |
| Sender-state pool | 384 | 1,536 | `8 * 48` |
| Logical waiter table | 128 | 512 | `8 * {PacketPtr,PageKey}(16)` |
| Counters and high waters | 160 | 640 | 20 `uint64_t` values |
| Next-action serial | 8 | 32 | nonwrapping allocator |
| **Fixed logical-controller metadata subtotal** | **1,232** | **4,928** | excludes the hidden-payload row and SPD implementation arrays below |

The fixed four-core CPU front end adds 32 bytes of decode sidecars plus 32
bytes of `Instruction` extensions. `SPD` adds 8 bytes for
`visibleTileCount`/`allocatedTileCount`. Those 72 global bytes are not hidden
in the per-MAA subtotal.

Appending lanes also extends the exact current SPD constructor allocations. With
`sizeof(bool)==1` and the target ABI assertion
`sizeof(std::vector<T>)==24`, the added four-MAA SPD allocation is:

| Added SPD allocation for 16 lanes | Bytes |
| --- | ---: |
| `tiles_data` | 262,144 |
| `tiles_status` | 16 |
| `tiles_dirty` | 16 |
| `tiles_ready` | 32 |
| `tiles_size` | 64 |
| `element_finished` | 65,536 |
| Empty `waiting_units_funcs` vectors | 384 |
| Empty `waiting_units_ids` vectors | 384 |
| **SPD implementation subtotal** | **328,576** |

The fixed four-MAA/four-core simulator object total is therefore
`328,576 + 4,928 + 72 = 333,576` bytes. The eight-line window adds at most
`4 * 8 * 64 = 2,048` bytes of packet payload in flight. Existing generic
`Packet` object and allocator overhead are not architectural cache storage,
but the build gate must record target `sizeof(Packet)` and allocator policy
instead of hiding them in an area claim.

For hardware accounting, only the 262,144-byte private payload and fixed
controller state are an architectural proposal. The C++ `element_finished`
and vector objects are simulator bookkeeping, but they are counted above so a
simulator-memory claim cannot omit them.

## Source-file implementation map

| File/function | Required change |
| --- | --- |
| `LogicalSPDCacheController.hh` | Reuse fixed controller and atomic pair unchanged for control. Do not add page payload. Backing/type/reference data is an explicitly sized integration extension, not a shadow slot FSM. |
| `IF.hh:33-195`, `IF.cc:12-55` | Add opcode 17/name only. Preserve numbers and behavior of 0--16. Generated ALUs use physical hidden IDs and internal tag fields. |
| `CpuSidePort.cc:216-350` | Decode opcode-17 high bytes only for opcode 17; defer its dispatch until word 3. Extend virtual-ready shell with finite generation-tagged logical waiters. |
| `MAA.hh:468-513` | Own fixed per-MAA logical state; add admission, waits, action scheduler, response, drain, and serialization declarations. Do not remove `transparentController`. |
| `MAA.cc:487-587` | At existing retry point, schedule responses first, then logical writeback, fill, ALU, and new admission. Busy unit/port means retry with no mutation. |
| `MAA.cc:648-970` | Leave transparent submission/dispatch/issue behavior unchanged; add disjoint `tryIssueLogicalSliceAction`. A call cannot be owned by both schedulers. |
| `MAA.cc:1102-1180` | Match an internal logical ALU tag before existing transparent completion, call `completeOverwrite`, then schedule writeback. Finish high-level opcode only after fourth ACK. |
| `SPD.hh:29-111`, `SPD.cc:237-286` | Split visible/allocated counts, append four hidden lanes per MAA, reject hidden IDs outside generated work. Reuse existing storage/latency; do not reuse `tiles_dirty` or `setVirtualSize`. |
| `ALU.cc:74` | Run existing physical FP64 page ALU. Add only an internally tagged captured-scalar branch; legacy operand reads and semantics remain unchanged. |
| `StreamAccess.hh:84-135`, `StreamAccess.cc:363-479` | Add bounded logical fill/direct-writeback and response methods. Preserve ordinary load/store, `recvData`, and `writePacketSent` behavior. |
| `MAA.hh:799-850`, `Port.cc:30-295,499-745` | Preserve address ordering; prohibit logical coalescing; retain logical writes until `WriteResp`; validate sender state and complete tag before line ACK. |
| `IndirectAccess.*` | No first-slice change. A producer conversion is a separate response-ACK/generation-identity feature. |
| API/config/tests | Add only opcode-17 helper and logical waits after unit contracts pass. Old helpers and visible-SPD ranges stay unchanged. |

## Payload caching does not preserve 16K reorder metadata

The slots contain only one 4K page of FP64 payload and a page tag. They do not
contain a 16K request table, row/offset state, producer order, filtered-index
state, outstanding indirect requests, or an ordering proof. The existing stream
request table is local to an instruction and reset at completion
([StreamAccess.cc](../../src/mem/MAA/StreamAccess.cc#L200),
[#L356](../../src/mem/MAA/StreamAccess.cc#L356)).

Therefore payload caching itself does **not** preserve 16K reorder metadata,
enable a reordered consumer, prove producer drain completion, or justify an
indirect-cache speedup. Any later reorder claim needs a separately bounded,
charged, response-safe, checkpoint-safe reorder ledger.

## Reset, drain, and checkpoint

`resetStats` is statistics-only today ([MAA.cc](../../src/mem/MAA/MAA.cc#L1340));
it must never reset logical state.

1. Initialization zeros logical records and hidden lanes; generation zero is
   never live.
2. Reset while an operation, miss, lease, sender state, waiter, fill, or
   writeback exists returns busy. It never drops dirty payload or cancels a
   response-bearing write.
3. `drain()` blocks new logical admission/waiters, retries dirty writebacks,
   and waits for every response. It cannot report drained while any logical
   action, credit, queue, lease, or waiter exists.
4. After no descriptor reference remains, drain may discard clean hidden slots.
   Dirty contents have already reached exact writeback ACKs. Descriptor
   generation/base/type/ready mask persist.
5. Checkpoint is quiescent-only: serialize descriptors and nonwrapping
   allocators; serialize empty slots/queues as assertions; restore hidden slots,
   ledger, sender pool, and waiters empty. Coherent backing memory remains data
   authority, so hidden payload is not serialized.
6. A pre-quiescence checkpoint is a drain failure, never a best-effort snapshot.

## Smallest validation order and hard promotion gates

Do not run gem5 for this design task. After implementation, proceed only in
this order:

| Stage | Smallest test | Promotion gate | Failure gate |
| ---: | --- | --- | --- |
| 0 | Source contract: fixed capacities, opcode-17 isolation, hidden-ID rejection, tag fields, no logical `WritebackDirty`. | Required before compile. | Dynamic logical queue/ledger, visible hidden lane, or opcode-8 reinterpretation. |
| 1 | Header-only controller test: existing atomic-pair tests plus A/B/A/B four-page schedule and stale serials. | Optimized and ASan/UBSan pass. | Partial reservation, pin leak, serial/generation reuse, or reuse before ACK. |
| 2 | Pure C++ stream/port ledger: 512 lines, 8 credits, reordered good responses, duplicate, wrong tag/address, and port refusal. | Exactly one terminal callback at `acked==issued==512`. | Send acceptance completes write, dynamic growth, or bad response retires good request. |
| 3 | Incremental then configured `gem5.opt` build. | Warnings-as-errors; byte assertions yield 333,576 for the four-MAA/four-core point and record `sizeof(Packet)`. | Opcode renumbering, padding/accounting drift, or transparent-test regression. |
| 4 | Focused gem5 SE, one MAA then four: source -> opcode 17 -> page waits -> byte/guard check. | Trace: 4 fills, 4 computes, 4 complete 512-line writeback ACKs, then completion. | Early ready/completion, mismatched bytes, hidden-visible alias, or legacy trace change. |
| 5 | Delayed/reordered/duplicate response, cache retry, reuse, FIFO-full, drain/checkpoint/restart gem5 tests. | High waters bounded 4/4/8/8; old generation never publishes. | Hang, growth, stale mutation, lost ACK, dirty discard, or live checkpoint. |
| 6 | Existing physical opcode-8, opcode-16, stream-store, and CPU-visible-SPD regressions. | All legacy behavior unchanged. | Difference for an instruction without opcode-17 marker. |

Stages 0--6 establish functional safety only. No performance or architecture
promotion may occur until a separate cost study charges the 256-KiB private
payload rather than calling it free SPD capacity.

## Completion criterion

The slice is complete only when one FP64 source and a distinct full-overwrite
destination execute through the deterministic four-page/two-slot trace, every
destination page becomes visible after its 512 matching `WriteResp` events,
all 16K elements plus guards match a scalar reference, and drain/checkpoint
restore contains no live tag. Every legacy physical instruction must retain its
existing wire encoding and behavior.
