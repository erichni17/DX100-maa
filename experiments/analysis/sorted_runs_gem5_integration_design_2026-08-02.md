# Four sorted runs: repaired response-timed design

Date: 2026-08-02

Original design: `11b56a2ea95a88299ea09f19a4f0edf080645b67`

Reviewed logical-response series: cumulative `08dc106`, `c5dd636`, `fdbce2a`,
`4787925`; independently rejected, used only to define repair obligations

Status: design and static validation only; no production change, gem5 run,
latency result, or speedup claim

## Decision

Keep the useful mechanism—four independently sorted 4,096-record runs in
coherent backing followed by a four-way merge—but reject the original ABI and
accounting. A record is **32 bytes**, not 16. The controller keeps exactly one
4K record array on chip; the four complete runs occupy a **524,288-byte
off-chip coherent image**. There is no 16K on-chip descriptor container.

The repaired design is correctness-closed only under all of these gates:

1. B, C, and descriptor-backing bases are 64-byte aligned; A is 8-byte
   aligned. Admission rejects a violation before generation allocation, owner
   acquisition, translation, or packet creation.
2. An MMU translation lease pins the submitting address-space generation and
   DRAM mapping configuration for the operation. The current MAA has no such
   lease hook, so production integration is blocked until it exists.
3. The comparator below is the sole `native16-reference` relation. Existing
   insertion-order Row-Table issue is not assumed to equal it. A comparison to
   current `direct16` is illegal until both arms use, or a host trace proves,
   this exact relation.
4. The cumulative logical-response ownership series ending at `4787925` is
   rejected. A repaired and independently accepted successor must be
   integrated first. Packet send acceptance is never action completion.
5. All controller queues and owners are constructed at fixed capacity. The
   current dynamically allocated Packet/Request and address-map path is an
   integration blocker, not omitted implementation state.

Failure to establish any gate leaves sorted mode disabled. This document does
not reinterpret a failure as a locality or performance result.

## Audited source boundary

The following current-source facts constrain the design:

- A B index is converted to a virtual A address, translated, decomposed by
  `MAA::map_addr`, and inserted using `getRowTableIdx` and `getGrowAddr` in
  [IndirectAccess.cc](../../src/mem/MAA/IndirectAccess.cc).
- `getRowTableIdx` encodes the configured channel/rank/bank-group/bank slice;
  `getGrowAddr` encodes the remaining bank-group/bank quotient plus row. Their
  pair is the exact current Row-Table placement identity, not the replay's
  archived 11-bit proxy.
- Current Row-Table send order also depends on slice traversal and insertion
  order. It is not a total record comparator. The design therefore freezes an
  explicit inverse slice-order rank and never claims that sorting raw physical
  addresses approximates native order.
- `IndirectAccessUnit::translatePacket` and
  `StreamAccessUnit::translatePacket` assume `translateTiming` calls back
  immediately. They do not provide an operation generation, page-generation
  lease, or independently owned asynchronous translation response.
- Current virtual destination retirement has configurable combiner capacity
  and response-bearing `WriteReq` writes, but uses `vector`, `map`, and `set`
  state. Those containers are semantic reference only for this fixed design.
- The current `INDIR_LD_VIRTUAL_INDEX` helper/decoder has no accepted field for
  a 524,288-byte descriptor span or an MMU lease. A new disabled-by-default
  descriptor form and complete pre-dispatch validation are required; existing
  opcode forms must continue to reject an extra instruction word.
- [MAA.hh](../../src/mem/MAA/MAA.hh) overrides `resetStats()` but does not
  override `drain()`, `drainResume()`, `serialize()`, or `unserialize()`.
  `allFuncUnitsIdle()` is a scheduler/statistics predicate, not a gem5 drain
  hook.

The rejected response series ending at `4787925` attempted four useful
semantics retained as design requirements here: full copied tag comparison
before state mutation, exact `PacketPtr` ownership, separate port/stream credit
settlement, and a wrapper that always consumes a timing response. Independent
review found five blocking transport defects: deferred/pending-send aliases
were omitted from the ownership search, an unsent read could leak its stream
credit, `WriteResp` size was unchecked, one retirement-owner panic could occur
before owned-pointer cleanup, and arbitrary residual sender-state chains were
not handled safely. The series is evidence of what must be repaired, not an
accepted dependency or implementation base.

## Operation and atomic admission

The only accepted operation is a dense, unconditional FP64 gather:

```text
C[i] = A[B[i]], i = 0..16383
B element: uint32_t
A/C element: 8 bytes
cache line: 64 bytes
```

The descriptor supplies `{A_base, A_elements, B_base, C_base,
descriptor_base, CID}`. Admission evaluates the following in a temporary
value object. It may publish no generation, owner, ready bit, or packet until
every check succeeds.

- `A_base % 8 == 0`.
- `B_base % 64 == C_base % 64 == descriptor_base % 64 == 0`.
- `A_elements` is nonzero; every B value is later checked `< A_elements`.
- Checked unsigned arithmetic proves the ends of the A span, 65,536-byte B
  span, 131,072-byte C span, and 524,288-byte descriptor span do not wrap.
- The four virtual spans are pairwise disjoint and are covered by permitted
  MAA address regions.
- Sorted geometry is exactly four runs of 4,096 records, one 64-byte line, and
  the configured Row-Table slice count is in `1..64`. The 16-bit record field
  is not permission to exceed the charged 64-entry inverse table.
- `my_RT_slice_order[rtConfig]` is a complete permutation. Its inverse and the
  six DRAM organization/bit fields are copied into the operation state.
- A nonzero translation lease binds CID/address space, page-table generation,
  and the copied DRAM mapping. The lease service must prove the physical page
  sets for the entire admitted A, B, C, and descriptor spans are pairwise
  disjoint. This deliberately rejects even a read-only alias rather than
  trying to prove byte-level safety after descriptor/C writes begin.
- Nonzero generation, transaction, and serial allocators have headroom for
  the worst-case 66,560 actions without wrapping.
- The indirect unit, its one shared stream executor, 22 action-owner slots,
  16 C owners, and the descriptor span are acquired atomically.

For reference, the exact line-count formula is

```text
lines(base, bytes) = ((base & 63) + bytes + 63) / 64  // integer division
```

An unaligned 65,536-byte B span occupies 1,025 lines, an unaligned
131,072-byte C span occupies 2,049, and an unaligned 524,288-byte descriptor
span occupies 8,193. The design rejects those bases; it never silently charges
1,024, 2,048, or 8,192 lines for them.

## A identity and the 32-byte record

The canonical record identity is a **virtual 64-byte A line under the active
translation lease**. Physical address is a validated snapshot used for
ordering, coalescing, and response routing; it is not sufficient identity by
itself.

The descriptor wire format is little-endian and exactly 32 bytes:

```text
offset  size  field
0       8     aLineVaddr       // virtual line, 64-byte aligned
8       8     aLinePaddr       // build-time translated physical line
16      8     grow             // exact getGrowAddr(rtConfig, ...)
24      4     destination      // 0..16383
28      2     sliceRank        // inverse my_RT_slice_order[rtConfig][rtIdx]
30      1     sourceWord       // 0..7
31      1     flags            // bit 0 valid; bits 7:1 must be zero
```

At build, the controller computes `A_base + 8*B[i]` with checked arithmetic,
derives the virtual line and word, submits one tagged translation, and records
the returned physical line plus exact slice/grow fields. A translation fault,
lease mismatch, non-line-aligned physical response, or out-of-range B value
stops before the record is committed.

At merge, every selected record is retranslated through the one bounded
translation owner before it can consume A data or mutate a C owner. The result
must equal `aLinePaddr`; recomputed slice rank and grow must equal the stored
fields. The first record of a new physical-line group uses that same validated
action to issue the A `ReadReq`; subsequent records, including virtual aliases,
still retranslate but reuse the retained A payload. A lease break or mismatch
is fatal and cannot fall back to the snapshot.

The current immediate-callback translation helper cannot implement this
contract. Required integration adds a one-entry `BaseMMU::Translation` owner
with copied generation/transaction/serial/virtual-line identity and explicit
fault handling. No translation response may allocate a map entry or vector.

## Exact comparator and baseline relation

For a valid record `r`, define:

```text
K(r) = (r.sliceRank,
        r.grow,
        r.aLinePaddr,
        r.sourceWord,
        r.destination,
        r.aLineVaddr)
```

All fields are stored in the record. `sliceRank` is the frozen position of the
record's exact `rtIdx` in `my_RT_slice_order`; `grow` is the exact current
DX100 `getGrowAddr` result. Together they retain the complete configured
Row-Table slice/grow relation. Physical line orders columns within that
placement, source word makes same-line word order deterministic, destination
is the semantic tie-breaker, and virtual line makes physically aliased but
distinct records total even under corrupted input. Two byte-identical records
are equal; any distinct valid records compare in exactly one direction.

Each 4K heap sort and the four-way merge use unsigned lexicographic `K` only.
The merge may select a head only when every nonempty run has a validated head.
It breaks equal head keys by run number, although valid destinations make that
case unreachable. Thus the output is exactly `stable_sort(all 16384 records,
K)`, independent of response arrival order or heap implementation.

This `K` is the repaired `native16-reference` reorder relation. It is a
correctness definition, not a claim about the current insertion-driven
`claim_entry_send_native_order` sequence. Before any native-latency comparison,
a shared pure helper must generate `K` for both the native reference and sorted
controller, or a deterministic host trace must prove equality. Otherwise the
comparison fails closed.

## Descriptor layout and causal timeline

Two records fit in one cache line:

```text
runBase(r)  = descriptor_base + r * 131072, r in 0..3
record(r,j) = runBase(r) + j * 32,            j in 0..4095
line(r,l)   = runBase(r) + l * 64,            l in 0..2047
```

One run is 131,072 bytes / 2,048 lines. Four runs are 524,288 bytes / 8,192
lines. Every descriptor request is separately translated under the lease;
physical page contiguity is never assumed.

The state sequence is:

```text
Idle -> Admit -> ScanB/BuildRecord -> HeapBuild -> HeapExtract
     -> Spill -> RunBarrier -> (next run, four times)
     -> PrimeHeads -> MergePick -> ValidateA -> NeedA/RetainA
     -> InsertC/DrainC -> FinalDrain -> Complete -> Idle
```

For each run, ScanB receives exactly 256 B lines and constructs exactly 4,096
records. Heap sort is in place with one 32-byte swap register. Spill issues
2,048 line writes, retaining the line and exact action owner across retries and
until its `WriteResp`. Only 2,048 matching ACKs make that run immutable; the
active array is not reused earlier. No run reload begins until all four run
barriers hold.

Merge maintains one 64-byte/two-record buffer per run. A consumed buffer is
refilled by one tagged read. If any nonempty run lacks a head, global selection
stalls. A selected record is not consumed until its A translation succeeds,
the correct A payload exists, and its destination word is accepted by a C
owner. That event advances exactly one cursor and sets exactly one destination
bit.

## Fixed owners, queues, and same-address rules

Construction allocates these exact capacities; zero and “unlimited” are not
legal sorted-mode values.

| Resource | Fixed capacity | Rule |
| --- | ---: | --- |
| Active records | 4,096 | One run only; no second sort image. |
| B/control payload | 1 line | One B response or spill staging action. |
| Run reload buffers | 4 lines | One per run, at most one read per run. |
| A payload | 1 line | Held while the maximal equal-physical-line group drains. |
| C owners | 16 lines | Fully associative, round-robin victim, one owner per C virtual line. |
| Action-owner slots | 22 | Slot 0 control, 1–4 run reads, 5 A, 6–21 C writes. |
| Simultaneously live packet actions | 21 | Four run reads + one A read + sixteen C writes. |
| Translation owners | 1 | Reuses the selected action tag; translations serialize. |
| Ready/retry queue | 0 entries | A fixed priority scan of owner states replaces a queue. |

A C owner contains a virtual line, 64-byte payload, 8-bit present mask, page,
and state. Duplicate destination bits are rejected by the 16,384-bit coverage
bitmap. A full line is issued immediately. On owner pressure, the round-robin
victim is issued as one 64-byte masked `WriteReq`; if that address already has
an action in translation, ready, sent, or ACK-pending state, insertion stalls.
The owner remains unavailable until the exact `WriteResp`. A later write to
the same line gets a new serial only after the prior ACK; logical actions never
coalesce by address.

An address collision in the shared port map leaves the original owner and
packet unchanged in `Ready` and retries it after the conflicting exact
retirement. A rejected `sendTimingReq` likewise preserves the same tag,
PacketPtr, payload, mask, and credit state. It creates neither a new action nor
a new serial.

Priority is: consume timing responses; complete translation callbacks; retry
sent/ready C writes; drain a full/victim C owner; refill missing run heads;
validate the selected A record/issue its A read; scan or spill. This order lets
C retire while A data is retained and prevents a circular wait.

## Action identity and provisional response semantics

Every translation or packet action owns this copied 48-byte tag:

```text
generation:u64, transaction:u64, serial:u64,
expectedVLine:u64, expectedPLine:u64,
maa:u16, lineIndex:u16,
action:u8, slot:u8, run:u8, command:u8
```

Unused fields are canonical zero, never wildcards. `lineIndex` covers B
0..1023, build/merge records 0..16383, run lines 0..2047, and C lines 0..2047.
The action owner is populated before a translation or packet becomes
externally visible. Its 64-byte packed ledger
entry is the 48-byte tag, an 8-byte exact packet/callback token, five lifecycle
bytes (`state`, `retry`, `streamCreditOwned`, `portCreditOwned`,
`senderStateOwned`), and three reserved zero bytes. A retired slot retains its
last tag long enough to classify an exact repeat as duplicate; allocation
replaces it only with a strictly newer serial.

The owner lifecycle is exact:

```text
Reusable -> Translating -> Ready -> Sent -> Retired/Reusable
                         -> Retired/Reusable       // translation-only
Ready --send rejected--> Ready                    // same packet and serial
Translating/Ready --abort--> Retired/Reusable     // release stream credit
```

Only an exact terminal response permits `Sent -> Retired`. Rejected extras do
not transition the owner; fatal corruption performs bounded cleanup and then
panics. B/run/A reads are fill-kind actions. Descriptor and C writes are
writeback-kind actions that retain their line payload and mask through the
terminal ACK.

The following ownership semantics are the sorted-controller target, not an
acceptance claim for `4787925`:

- `Retired`: only the exact map-owned PacketPtr with matching copied tag,
  address, command, ledger state, and safe sender state. Remove every owned
  reference, accept exactly one terminal response, settle action/stream and
  port credit once, pop sender state once, delete once.
- `DroppedExtra`: a safely releasable non-owned tagged packet. Count its precise
  stale/duplicate/wrong-* class, pop/delete the extra once, and do not touch the
  active owner or its port credit.
- `FatalOwnedCorruption`: an exact owned packet whose identity/state is
  inconsistent. Remove all references, abort its action credit, settle its
  port credit once, delete it, then panic.
- `FatalUnownedExtra`: an unowned packet whose sender state cannot safely be
  released. It owns no active port credit; delete what is safely owned, then
  panic.

A transport successor is acceptable only if it additionally proves all five
review repairs: ownership enumeration includes outstanding, deferred, and
pending-send aliases; aborting an unsent Read/ReadEx releases its enqueued
stream credit exactly once; every response checks command-specific size
(all sorted `ReadResp` and `WriteResp` payloads are 64 bytes); exact-owner
cleanup precedes every panic; and the sender-state stack is either exactly the
declared single sorted state or is rejected through a cleanup path that cannot
leave an arbitrary residual chain. Static sorted-controller tests cannot
substitute for that separate transport review.

The cache/memory wrapper returns `true` on every response path. Returning
`false` would ask the ResponsePort to retain an already classified response
forever and is forbidden. Validation precedes outstanding-map erase, ledger
ACK, cursor change, ready-bit publication, and owner reuse.

The action classes and terminal events are exact:

| Action | Owner | Terminal event |
| --- | --- | --- |
| B line | slot 0 + control payload | matching `ReadResp` installs 16 indices; the payload remains while slot 0 performs their 16 build translations, and only then may the next B line start |
| Build A translation | slot 0 | matching translation callback commits one record; no packet ACK |
| Spill line | slot 0 + run buffer | matching `WriteResp`; 2,048/run close the barrier |
| Run reload | slot 1–4 + run buffer | matching `ReadResp` installs two validated records |
| Merge A validation/read | slot 5 + A payload | every record has a translation callback; a new group additionally waits for matching `ReadResp` |
| C line | slot 6–21 + C owner | matching masked/full `WriteResp` ACKs exactly `popcount(mask)` destinations |

Admission reserves 66,560 nonwrapping serials:

```text
1,024 B actions
+ 16,384 build-record translations
+ 8,192 spill actions
+ 8,192 reload actions
+ 16,384 merge-record validation/A actions
+ at most 16,384 C write actions
= 66,560
```

The actual terminal packet-response count is
`17,408 + A_line_groups + C_write_actions`, hence 19,457 minimum and 50,176
maximum. Translation callbacks equal `50,176 + C_write_actions`, hence 52,224
to 66,560; the merge A action's translation and optional read share one serial.
For every class, `started = translation_failed + translation_completed`, and
for packet-bearing actions `packet_created = send_accepted = response_retired`
at clean completion. Retries change none of these equalities.

## Packed controller ledger

This is an exact byte-packed architectural ledger for the specified controller,
not a `sizeof` claim for gem5 Packet, Request, statistics, allocator, or STL
objects.

| State | Fields | Bytes |
| --- | --- | ---: |
| Active record array | `4096 * 32` | 131,072 |
| Four run line buffers | `4 * 64` | 256 |
| B/control payload | one 64-byte line | 64 |
| A payload | one 64-byte line | 64 |
| Sixteen C owners | each `vline:u64 + payload:64B + mask:u8 + state:u8 + page:u8 + reserved:u8` = 76 B | 1,216 |
| Twenty-two action owners | each 48-B tag + 8-B external token + 5 lifecycle bytes + 3 reserved bytes | 1,408 |
| Destination coverage | 16,384 bits | 2,048 |
| Operation state | eight endpoints `u64` = 64 B; generation/transaction/serial/lease `u64` = 32 B; `org[6]:u32`, `addrBits[6]:u8`, tx offset/config `u8`, slice count `u16`, inverse order `[64]:u8` = 98 B; IDs/phase = 8 B; reserved zero = 6 B | 208 |
| Four run states | four 16-bit cursors/counts, four flags, previous 32-B record = 44 B/run | 176 |
| Heap state | four 16-bit indices, four phase/valid bytes, pending cycles `u32`, and one 32-B swap record | 48 |
| Merge state | previous 32-B record, winner/valid, emitted count | 36 |
| Four page ledgers | generated/issued/ACKed 16-bit counts plus published/state | 32 |
| Conservation counters | six classes × `{started, translationCompleted, translationFailed, packetCreated, sendAccepted, responseRetired}:u32` | 144 |
| **Sorted controller total** | exact sum above | **136,772** |
| Of which active descriptor array | 4K, not 16K | **131,072** |
| Other sorted-controller state | total minus active array | **5,700** |
| Independent existing invalidator metadata | unchanged separate charge | 4,096 |
| **Reorder-related total including invalidator** | controller + invalidator | **140,868** |

The optional hidden-SPD substrate is neither used nor counted. The coherent
524,288-byte run image is off-chip metadata and not guaranteed to reside in
LLC. Visible physical SPD payload and unrelated existing MAA state are separate
budget rows; this repair does not repeat the old common-state placeholder as an
implementation total.

The target gem5 ABI remains unpriced because the rejected response path uses
Packet/Request/sender objects and dynamic address maps. Production promotion
requires a construction-time pool of 22 exact wrappers and fixed port leases,
then a host `sizeof`/allocation ledger. Until then, 136,772 bytes is only the
packed controller contract and the implementation verdict is blocked.

## Exact semantic traffic

| Component | Requests/actions | Payload bytes |
| --- | ---: | ---: |
| B reads | 1,024 line reads | 65,536 |
| Descriptor run writes | 8,192 line writes + 8,192 `WriteResp`s | 524,288 |
| Descriptor run reads | 8,192 line reads | 524,288 |
| A reads | 1..16,384 line reads | 64..1,048,576 |
| C writes | 2,048..16,384 64-B full/masked writes | 131,072..1,048,576 carried; exactly 131,072 byte-enabled useful bytes |

The minimum semantic packet payload is **1,245,248 bytes** and the maximum is
**3,211,264 bytes**. These totals include B, both descriptor directions, A,
and carried C data; response headers, coherence, write allocation, eviction,
and memory fills are additional measured traffic.

Relative to a matched descriptorless gather with the same B, A-line order, and
C write policy, sorted runs add exactly **1,048,576 descriptor bytes**. Relative
to an ideal 2,048-full-line C retirement, descriptor traffic plus extra carried
C bytes ranges from **1,048,576 to 1,966,080 bytes**. Neither range is a timing
or DRAM-command estimate.

## Completion, liveness, and faults

Each run reload validates flags, alignment, destination range for its run,
source word, frozen mapping-field bounds, and nondecreasing `K`. The global
coverage bitmap rejects a duplicate before C-owner mutation. A run cursor
advances only after successful C insertion. Page `p` publishes only when its
coverage bits are all set, `generated[p] = issued[p] = ACKed[p] = 4096`, and no
C owner/action can still write that page.

Instruction completion requires all four cursors at 4,096, emitted count
16,384, coverage all ones, every page published, all 22 action owners reusable,
all 16 C owners free, translation owner idle, B/run/A buffers invalid, and port
and action credits zero.

Liveness assumes only the normal timing-system fairness contract: an accepted
request eventually receives one response and a rejected request is eventually
retried. The controller itself introduces no wait cycle: response retirement
has priority; missing heads do not get skipped; an A payload can remain while C
drains; same-address C writes serialize; serial exhaustion rejects admission.

Before the first acknowledged spill, a translation fault can abort after all
owned requests retire. After any descriptor or C write is acknowledged, the
first slice drains exact owners and then panics rather than attempting rollback
or publishing partial C. Statistics reset never changes functional state.

## Drain, checkpoint, restore, reset, and panic hooks

Current MAA source cannot yet provide the claimed lifecycle. The required gem5
integration is explicit:

- `MAA::drain()` sets `sortedAdmissionBlocked`. If the controller and every
  response/translation/port owner is idle, return `DrainState::Drained`.
  Otherwise return `Draining`, continue only already-owned retries/responses and
  controller progress, and call `signalDrainDone()` exactly when the full
  completion predicate becomes true.
- `MAA::drainResume()` clears the admission block and schedules the ordinary
  issue event. It does not synthesize or replay a packet.
- `MAA::serialize(CheckpointOut&) const` is legal only after drain and with the
  controller Idle. It serializes next generation/transaction/serial values and
  the disabled/config geometry identity. It serializes no live PacketPtr,
  sender state, MMU callback, owner, or descriptor copy.
- `MAA::unserialize(CheckpointIn&)` requires all fixed owners in their
  construction-time empty state, restores only nonzero monotonic allocators and
  matching geometry/config identity, and leaves admission blocked until
  `drainResume()`.
- Functional `resetSortedController()` is legal only at construction or Idle
  with zero credits. `MAA::resetStats()` continues to call
  `ClockedObject::resetStats()` and must not invoke it.
- Contract violation uses `panic_if`/`panic` after first removing exact owned
  pointers as required by the provisional response disposition. No assertion-only
  cleanup, best-effort live checkpoint, or silent owner abandonment is legal.

Quiescent-only checkpointing is implementable with those new overrides. Live
operation serialization is deliberately unsupported and must panic if gem5
bypasses drain. The static model checks drain/restore rules; no checkpoint or
gem5 executable is run here.

## Claim boundary and required implementation order

Correctness claims from this document are limited to: alignment rejection,
record round trip, total comparator definition, bounded state, exact ownership
transitions, and arithmetic checked by the static unit model. The comparator
and request counts are structural locality proxies only. Fewer A-line groups or
row/grow transitions do not prove fewer DRAM commands.

Preserving a global 16K reorder relation is not evidence that latency matches
`native16`. Sorted runs add two full descriptor transfers, serial translation,
heap work, head stalls, masked C writes, and shared-port contention. Latency,
speedup, area, power, LLC residency, and application benefit are unmeasured.

Implementation order, each requiring a separate review, is:

1. Review the descriptor ABI, add atomic pre-dispatch alignment/span checks,
   and add the address-space/mapping lease plus bounded translation owner.
2. Repair all five transport defects after `4787925`, obtain independent
   acceptance, and replace sorted packet/address ownership with
   construction-time fixed wrappers/port leases.
3. Add a pure fixed controller and exact target-ABI/allocation tests; no public
   opcode yet.
4. Add drain/serialize/unserialize hooks and quiescent restore tests.
5. Wire B -> one-run sort -> spill/ACK -> reload, then four-run merge, then A/C.
6. Only after correctness and independent review, expose a disabled-by-default
   selector. Any gem5 or workload run needs separate authorization.

Residual blockers are the unreviewed descriptor ABI, absent MMU
lease/page-generation hook, absent MAA drain/checkpoint overrides, all five
rejected-response transport defects, current dynamic
Packet/Request/address-map allocation, and lack of a shared native16 reference
helper for `K`. The design is therefore ready for independent design review,
but **BLOCKED for production implementation or performance testing**.

Design-repair verdict: **READY_FOR_INDEPENDENT_REVIEW**. Production and
performance verdict: **BLOCKED** on the residual blockers above.

## Static validation matrix

`experiments/tests/test_sorted_runs_design_contract.py` is dependency-light and
does not invoke gem5. It checks:

- atomic rejection of unaligned B/C/backing and the 1,025/2,049/8,193 edge-line
  arithmetic;
- 32-byte record round trip with noncontiguous virtual/physical identities;
- comparator totality, deterministic ties, and four-way merge equality;
- fixed 4K/22-action/16-C-owner occupancy;
- retry invariance and exact action/ACK conservation;
- stale, duplicate, wrong-generation, wrong-address, wrong-command, and
  wrong-packet rejection without current-owner mutation;
- unsent-credit release, fixed response size, complete port-alias cleanup,
  residual sender-chain rejection, and same-address C-owner serialization;
- quiescent drain/serialize/unserialize and allocator preservation;
- every descriptor, traffic, action, and packed-ledger total in this document.

Any mismatch is a design failure. Static validation is not simulator evidence.
