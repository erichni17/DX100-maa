# XRAGE zero-payload terminal-chain audit

Date: 2026-08-09

Source anchor: `6374f753daf6d0968a4e0cb0a1eda37505ca055f`

Scope: read-only product-code audit plus this report. No product source, configuration,
benchmark API, or test was changed; gem5 was not built and no simulation was run.
Accordingly, statements below prove code shape and required invariants, not that the
in-flight fused correctness repair has passed validation.

## Verdict

**The mechanism is logically possible, but it is not implemented by simply issuing or
relabeling the two current opcodes.** A new fail-closed operation can make the terminal
FP64 chain

```text
C[i] = A[B[i]] * scalar
```

consume `B` from coherent memory/cache, retain only finite address/provenance state for
each outstanding `A` request, use the already timed finite-lane FP64 multiplier and
finite-width/banked result handoff, and retire directly to acknowledged `C` writes. It
would eliminate both payload paths:

- no 32-bit `B` index payload is written to or read from an SPD tile; and
- no 64-bit gathered or scaled result payload is written to or read from SPD.

One 32-bit SPD **readiness token** still remains. It is a completion namespace entry,
not a result tile. Software may reuse that token only after it becomes ready.

The current source does not yet provide that operation:

- `INDIR_LD_VIRTUAL_INDEX` (13) enables direct `B` ingestion but not the timed scalar
  transform (`benchmarks/API/MAA_gem5.hpp:453-475`;
  `src/mem/MAA/IndirectAccess.cc:731-736`).
- `INDIR_LD_VIRTUAL_SCALAR` (17) enables the timed scalar transform/direct retirement,
  but takes its actual indices from a fully finished SPD tile
  (`benchmarks/API/MAA_gem5.hpp:427-451`; `src/mem/MAA/IF.cc:353-385`). Its `indexAddr`
  names architectural `B` for provenance/hazards; it does not feed the values.
- The implementation predicates are deliberately disjoint:
  `isFusedDirectTransform()` recognizes only opcode 17, while
  `isDirectIndexLoad()` recognizes opcodes 13 and 14
  (`src/mem/MAA/IndirectAccess.cc:705-736`).

There are two materially different bounded designs:

1. **Recommended first implementation:** one descriptor covers at most 4,096 logical
   iterations. Software chunks 20,000 elements into `4096 + 4096 + 4096 + 4096 +
   3616`. No structure is indexed by a 16K logical aperture, and `B` is scanned once.
2. **Possible later implementation:** retain a 16K logical descriptor while limiting
   active Row/Offset state to 4K and rescan `B` across range passes. The current range
   candidate does this with two logical-iteration bitmaps and at least four scans for a
   16K/4K configuration. It has no 16K payload array, but it does have state indexed by
   all 16K iterations. It therefore does not satisfy a strict “no hidden 16K state”
   claim.

No byte count in this report is an area estimate. The counts describe architectural
payload, semantic storage capacity, or useful traffic only; they do not imply SRAM
layout, ports, tags, ECC, control, synthesis area, or energy.

Evidence convention: Sections 1 and 2, the cited current mechanisms in Section 3, and
the current-state audit in Section 6 are **proven source facts** at `6374f753`. The new
opcode, strict-4K limits, aggregate credits, safety contract, projected combined counts,
and implementation sequence are **proposals**. “Logically possible” is an inference
from those source facts and stated invariants, not validation evidence.

## 1. Proven native and current virtual/fused dataflows

All facts in this section are resolved at the source anchor above.

### 1.1 Native XRAGE x3 chain

The benchmark registers `B` (`pattern_int`), `A` (`sparse`), and `C` (`dense`) as three
memory regions (`benchmarks/spatter/src/Spatter/Configuration.cc:513-525`). Per worker,
setup allocates one 32-bit index tile, one FP64 gather-result tile pair, and—only for
`native16x3`—a second FP64 scaled-result tile pair; the scalar register is initialized
to `3.0` (`benchmarks/spatter/src/Spatter/Configuration.cc:486-507`).

For each chunk the native x3 arm executes exactly the four requested stages:

1. `maa_stream_load<int>(B, ..., tile1)`;
2. `maa_indirect_load<double>(A, tile1, tile2)`;
3. `maa_alu_scalar<double>(tile2, scalar, tile3, MUL)`; and
4. `maa_stream_store<double>(C, ..., tile3)`.

The exact dispatch is at
`benchmarks/spatter/src/Spatter/Configuration.cc:576-603,617-627`, and software waits
for `tile3` on the native multiply path at
`benchmarks/spatter/src/Spatter/Configuration.cc:654-656`.

The product code materializes every boundary:

- a stream-load response writes each 32- or 64-bit word to SPD
  (`src/mem/MAA/StreamAccess.cc:449-472`);
- the gather waits for and reads each 32-bit index from SPD
  (`src/mem/MAA/IndirectAccess.cc:1081-1100,1215-1246`);
- a native `A` response writes the selected word to the destination SPD tile
  (`src/mem/MAA/IndirectAccess.cc:2894-2923`);
- the FP64 scalar ALU reads its input from SPD, reads the scalar register, performs one
  multiply, and writes the FP64 result to SPD (`src/mem/MAA/ALU.cc:850-872,916-922`);
  and
- stream-store reads the FP64 source tile into its outgoing line
  (`src/mem/MAA/StreamAccess.cc:477-485`).

### 1.2 Current direct-memory index path

The `direct4*` benchmark arms omit stream-load and call
`maa_indirect_load_virtual_index` with `A`, `B`, a completion tile, `C`, and
min/max/stride registers
(`benchmarks/spatter/src/Spatter/Configuration.cc:584-600`). The public encoding uses opcode
13, no index SPD source, three source registers, and descriptor words 2/3/4 for `A`,
`C`, and `B` respectively (`benchmarks/API/MAA_gem5.hpp:453-475`). The functional API
implements the same identity gather directly from `B` memory to `C`
(`benchmarks/API/MAA_functional.hpp:628-647`).

At decode, the direct path reads min/max/stride, derives the logical length, and marks
the index input ready without an SPD tile (`src/mem/MAA/IndirectAccess.cc:1717-1743`).
The feeder:

- bounds the number of pending plus ready `B` cache lines;
- calculates `B[min + i*stride]` addresses;
- translates and coalesces a safe 64-byte read;
- records the logical iteration and word offset for every word in the line; and
- issues through the ordinary cache/memory port

at `src/mem/MAA/IndirectAccess.cc:771-861`. `ensureDirectIndex()` exposes a word only
after its line response has populated `direct_index_words`
(`src/mem/MAA/IndirectAccess.cc:863-875,999-1043`).
`checkElementReady()` selects that feeder rather than SPD when
`isDirectIndexLoad()` is true (`src/mem/MAA/IndirectAccess.cc:1081-1100`).

### 1.3 Current timed fused scalar/direct-store path

The `fuseddirect16x3` arm still stream-loads `B` into `tile1`, then invokes opcode 17
with that index tile, a scalar register, a completion token, `A`, `B` provenance, and
`C` (`benchmarks/spatter/src/Spatter/Configuration.cc:601-612`). It allocates only a
32-bit completion tile instead of either FP64 result pair
(`benchmarks/spatter/src/Spatter/Configuration.cc:493-501`).

The current fused response path is finite and timed:

- an `A` response occupies one finite response slot, either as a 64-byte line or as
  packed useful 8-byte words, while retaining the OffsetTable chain cursor
  (`src/mem/MAA/IndirectAccess.hh:82-120`;
  `src/mem/MAA/IndirectAccess.cc:2781-2889`);
- up to the configured ALU lane count is copied as `(iteration, 8-byte word)` into a
  shared per-MAA ALU batch (`src/mem/MAA/IndirectAccess.cc:3560-3652`;
  `src/mem/MAA/ALU.hh:51-61,88-98`);
- the ALU claims shared ownership, waits its modeled lane latency, performs FP64
  multiplication in place, and makes results visible on the following MAA cycle
  (`src/mem/MAA/ALU.cc:43-79,106-124,165-184`);
- the result link enforces configured words/cycle and bank conflicts, and the ALU keeps
  an entry until the combiner accepts it
  (`src/mem/MAA/IndirectAccess.cc:3487-3557`); and
- the finite tagged `C`-line combiner holds a line address, valid-word mask, and up to
  64 bytes of data, evicting through acknowledged retirement writes
  (`src/mem/MAA/IndirectAccess.hh:121-155`;
  `src/mem/MAA/IndirectAccess.cc:3797-3979`).

The defaults are 16 ALU lanes and a four-word/four-bank result handoff
(`src/mem/MAA/MAA.py:149-151,191-195`). This path is therefore not an instantaneous or
unbounded host-side post-transform.

## 2. Exact lifetime of `B[i]`

There are three different objects that must not be conflated.

### 2.1 Architectural `B` memory

`B` is software-visible registered memory. It **cannot** be poisoned, freed, reused, or
modified merely because one index was admitted. It must remain stable for every not-yet
read index and, under the proposed simple memory contract, for the entire operation.
The global `B` read lease is released only when the complete instruction finishes.
Software may reuse `B` only after the completion token and its fence. The current
opcode-17 compound lease already names `A:Read`, `B:Read`, and `C:Write`
(`src/mem/MAA/Invalidator.cc:58-86,327-348`).

### 2.2 Native SPD index copy

The native `tile1[i]` is a separate architectural SPD value. The gather reads it at
`src/mem/MAA/IndirectAccess.cc:1234-1238`, but native tile ownership is whole-tile: it
is marked unavailable while the consumer owns it and released only at instruction
completion (`src/mem/MAA/MAA.cc:1841-1870,1944-1952`). It is not reclaimed element by
element. The current scalar-fused instruction goes further and
sets `src1MustBeFinished = true`, so the entire tile must be resident before direct `C`
retirement can start (`src/mem/MAA/IF.cc:381-384`). A direct-memory combined opcode has
no such tile and must set both SPD source IDs to `NA`.

### 2.3 Private direct-feeder copy

Only `direct_index_words[i].value` may die early. The exact dataflow commit is a
**successful return from `RowTableSlice::insert(grow, A_line_paddr, i, wid, ...)`**.
That call has already allocated the OffsetTable entry `{i, wid, next}` and linked it to
the RowTable entry holding the translated `A` cache-line address
(`src/mem/MAA/Tables.cc:143-166,278-306,489-535`;
`src/mem/MAA/Tables.hh:52-56,95-146`). If insertion fails, fill requests a drain,
does not declare descriptor admission, and does not advance `my_i`
(`src/mem/MAA/IndirectAccess.cc:1301-1321,1433-1455`).

With range-pass checking enabled, the practical commit point also includes successful
`recordAdmission(i, grow, pass)` and setting
`direct_index_descriptor_inserted = true`
(`src/mem/MAA/IndirectAccess.cc:1322-1345`). The current
code then proves exactly one terminal decision and calls
`discardDirectIndex(...DescriptorInserted)`
(`src/mem/MAA/IndirectAccess.cc:1427-1454`). That
function explicitly poisons and erases only the private feeder map; its comment excludes
architectural `B` and the SPD path (`src/mem/MAA/IndirectAccess.cc:942-997`). A shared ready-line
record remains until all other words from that `B` line reach a terminal decision.

For predicate or range-partition rejection, no `A` descriptor exists; the private word
may die after that rejection is irrevocably recorded. The recommended first opcode is
unpredicated and single-pass, so its normal terminal choices are “descriptor committed”
or “stall and retry,” not “skip.”

## 3. Minimum finite state and release events

The following is the semantic minimum. Current debug counters and host-container
overhead are not included.

| Stage | State that must survive | Earliest safe release |
|---|---|---|
| Waiting for a `B` line | Instruction identity/generation; `B` line address; pending `(i, word-in-line)` list; downstream request/retry ownership | When the line response creates individual feeder words; the line record remains until its last word is terminal |
| `B[i]` available, descriptor not admitted | Private `B[i]`, its `i`, `B` word provenance, translated candidate `A` line/word, and the current retry state | Never on Row/Offset pressure; only after successful descriptor admission or an irrevocable reject |
| Admitted, waiting for `A` | Row entry with translated `A` line address and request state; Offset entry `{destination i, A word ID, next}`; instruction CID/PC/MMU context and `A/B/C` leases | After the matching `A` response word and `i` have atomically moved to response/ALU state |
| `A` response retained | Finite response slot containing the 64-byte line or useful 8-byte words; Offset-chain cursor; reservation/claim identity | Per word, after `(i, A_value)` is accepted by the ALU batch; per line, after its chain is empty |
| FP64 operation active or result blocked | Shared-ALU owner, latched FP64 scalar, at most lane-count `(i, 8-byte value/result)` entries, cursor, and ready state | Per result, after the result-link and destination combiner atomically accept it |
| `C` result not issued | `C` line tag, valid-word mask, FP64 payload, victim/retry state, and page/generation accounting | After an internally accepted write packet owns a copy of the payload and an outstanding-write record owns its completion metadata |
| `C` write sent, ACK pending | Packet payload; physical write key; exact-address serialization owner; page-to-word completion metadata; outstanding/expected counters | Only on the matching `WriteResp` |
| Whole operation | Input bounds, scalar, token generation, scan/admit/retire/ACK closure counters, global leases, and any queued port retry | Only when every iteration is terminal, every `A` response is consumed, ALU and combiner are empty, every `C` ACK arrived, and no packet/deferred request remains |

The current implementation demonstrates the last two gates. `createRetirementWrite()`
returns without mutation on an address conflict; on success it copies data into a
`WriteReq`, increments expected/outstanding state, records page metadata, and hands the
packet to the forced coherent retirement path
(`src/mem/MAA/IndirectAccess.cc:3379-3441`). A
downstream refusal leaves the packet queued for retry (`src/mem/MAA/Port.cc:499-568`;
`src/mem/MAA/CacheSidePort.cc:90-138,152-162`). `WriteResp` dispatches to
`retirementWriteComplete()`, which erases the outstanding key and completes page counts
(`src/mem/MAA/Port.cc:700-729`;
`src/mem/MAA/IndirectAccess.cc:4232-4263`). Full completion additionally requires
no retained fused result, no combiner entry, and zero outstanding writes
(`src/mem/MAA/IndirectAccess.cc:3989-4007`).

For a strict aggregate “4K word state” interpretation, the implementation should use a
shared 4,096-credit pool across retained `A` words, ALU entries, combiner words, and
in-flight `C` packet words, transferring rather than duplicating a credit at each stage.
The current code has independent finite limits; it proves each pool is bounded, not that
their sum is at most 4,096. The recommended `N <= 4096` descriptor nevertheless ensures
that no pool or exact-once structure can be indexed by more than 4,096 logical items.

## 4. Proposed opcode, API, and completion contract

This section is a proposal, not present behavior.

### 4.1 Fail-closed operation shape

Add a distinct opcode, provisionally
`INDIR_LD_VIRTUAL_INDEX_SCALAR = 18`, rather than overloading 13 or 17. Its first version
should accept only:

- FP64 source and destination;
- `MUL_OP`;
- no predicate and no prefetch token dependency;
- positive index stride;
- `0 < ceil((max-min)/stride) <= 4096`;
- separately registered, non-overlapping `A` and `C` regions;
- no overlap between the consumed `B` span and `C`; and
- at most 4,096 active Row line slots and 4,096 Offset entries;
- finite response-word, combiner-word, feeder-line, write, and ALU limits, with no
  capacity derived from a larger logical tile aperture.

Fail decode if the selected RowTable geometry exceeds 4,096 active line slots or the
OffsetTable exceeds 4,096 entries, even when the global range-pass option is off. Bound
the 32-bit feeder to at most 4,096 resident/pending words (at most 256 64-byte lines),
and bound response and combiner words explicitly. The default one-line feeder is well
inside that limit; the generic current maximum of 1,024 feeder lines is not
(`src/mem/MAA/IndirectAccess.cc:170-182,298-312,771-861`).

For the first XRAGE arm, configure both the guest logical aperture and the physical
tile capacity to 4,096 and chunk in software; do not reuse the runner's current
`direct_index_4k` label, which retains a 16K logical aperture. If a later system must
mix native 16K instructions and this operation under one global configuration, it needs
explicit opcode-local 4K capacities rather than inheriting the current zero-means-
logical defaults.

A matching API can be:

```cpp
maa_indirect_load_virtual_index_scalar<double>(
    A, B, completion_token, C,
    min_reg, max_reg, stride_reg, scalar_reg, Operation_t::MUL_OP);
```

The existing descriptor is 64 bytes, of which the API currently writes five 64-bit
words (`benchmarks/API/MAA_gem5.hpp:43,76-80,103-129`). Four source registers are needed
for the general min/max/stride/scalar form, while word 1 names only three generic source
registers (`src1RegID..src3RegID`) plus two generic destination-register bytes
(`src/mem/MAA/IF.hh:161-173`; `src/mem/MAA/CpuSidePort.cc:293-311`). A narrow compatible
encoding is:

| Descriptor field | Proposed value |
|---|---|
| word 0 opcode | 18 |
| word 0 datatype/optype | `FLOAT64_TYPE` / `MUL_OP` |
| word 0 `tdst1` / `tdst2` | 32-bit completion token / `NA` |
| word 1 `tsrc1`, `tsrc2`, condition | `NA`, `NA`, `NA` |
| word 1 `rsrc1`, `rsrc2`, `rsrc3` | min, max, stride registers, preserving opcode-13 feeder decoding |
| word 1 raw `rdst1` byte | scalar register, reinterpreted for opcode 18 as a fourth **source** register |
| word 1 raw `rdst2` byte | `NA` |
| words 2, 3, 4 | `A` base, `C` base, `B` base |

The decoder must move the raw `rdst1` byte into a new, explicitly named
`auxSrcRegID`/`src4RegID` and clear `dst1RegID` before instruction/register hazard
processing. Leaving it semantically as a destination would create false or unsafe RF
dependencies. An alternative is a sixth descriptor word, but that expands decode and
submission sequencing for no benefit to this first operation. An even narrower
XRAGE-only encoding could fix stride to one and use three source registers, but the
table above preserves the existing direct-index API semantics.

At decode, latch min, max, stride, and the 64-bit scalar for the instruction. Current
opcode 17 rereads the RF scalar for every ALU batch
(`src/mem/MAA/IndirectAccess.cc:3636-3642`); latching makes the streaming operation's
snapshot explicit and reduces the live RF contract.

### 4.2 Completion-token semantics

The token is one 32-bit tile ID with no FP64 data. Current opcode 17 already overrides
its tile span to four bytes while preserving an eight-byte payload through gather, ALU,
link, combiner, and retirement (`src/mem/MAA/IF.cc:190-198`). The new opcode must be
added to that rule and to `completion_only_tiles`, which prevents a completion token
from being consumed as SPD data (`src/mem/MAA/IF.cc:420-434,592-602`).

There are three different acknowledgements:

1. the CPU-side response to descriptor submission means only “accepted for dispatch”
   (`src/mem/MAA/MAA.cc:1889-1897`);
2. an optional virtual-page token may become ready only after that page's scanned,
   expected, issued, and acknowledged word counts match
   (`src/mem/MAA/IndirectAccess.cc:3282-3316`); and
3. the full completion token becomes ready only after
   `boundedRetirementComplete()`, final consistency checks, and
   `finishInstructionCompute()`
   (`src/mem/MAA/IndirectAccess.cc:2290-2353,2609-2650`;
   `src/mem/MAA/MAA.cc:1913-1956,2043-2071`).

`wait_ready(token)` performs the readiness read followed by `mfence`
(`benchmarks/API/MAA_gem5.hpp:137-145`). Token reuse must remain generation-safe; current
page-token reset rejects reuse with an outstanding waiter and increments a generation
(`src/mem/MAA/MAA.cc:2073-2093`).

## 5. Required safety, alias, concurrency, retry, and ordering rules

These rules are part of the proposed operation's contract.

### 5.1 Address ranges and aliases

- **`A` versus `C`: forbidden overlap.** An early `C` write could change an `A` word
  that a later gather has not read. Current opcode 17 already rejects source/destination
  region equality and interval overlap (`src/mem/MAA/IF.cc:353-380`). Keep the same
  conservative separately registered rule in v1.
- **`B` versus `C`: forbidden overlap for the direct feeder.** Unlike opcode 17's
  complete SPD snapshot, the combined path reads `B` incrementally. A `C` write must
  not overwrite a future index. Check the actual conservative consumed `B` byte span,
  not just base-pointer inequality.
- **`A` versus `B`: read/read overlap is semantically legal** if both remain immutable,
  the registered ranges and element alignments are valid, and `C` overlaps neither.
  V1 may reject it as a simpler fail-closed registration rule; it must not silently
  treat it as a write conflict.
- `C[i]` must name one unique, naturally aligned FP64 word for every logical `i`.
  Duplicate `B[i]` values are legal because they duplicate `A` reads, not `C` writes.
- Every consumed `B` address must lie in the registered `B` range and every resulting
  `A + 8*B[i]` in the registered `A` range. The direct feeder and gather already panic
  on their respective range failures (`src/mem/MAA/IndirectAccess.cc:780-800,1234-1245`).
  Translation faults are currently fail-stop, not restartable
  (`src/mem/MAA/IndirectAccess.cc:4265-4283`).
- Do not support MMIO, atomics, volatile side effects, a condition tile, or a partial
  “false element” rule in v1.

### 5.2 CPU and memory ownership

- The submitting CPU must publish initialized `A`, `B`, scalar, and bounds before the
  descriptor. It must not modify/unmap/free `A` or `B`, or access `C` as a completed
  array, until the full token (or a precisely documented page token) is ready.
- The direct feeder may route a line through cache or direct memory after an express
  cache-presence snoop; retirement writes are forced through the coherent retirement
  cache path (`src/mem/MAA/Port.cc:169-245`;
  `src/mem/MAA/IndirectAccess.cc:2736-2749,3438-3441`). CPU immutability and fences are
  still required; cache routing is not a substitute for ownership.
- The scalar and bounds must be snapshot operands. A later CPU RF write must not alter
  an in-flight batch.

### 5.3 Same-MAA and multi-MAA ordering

CPU requestors are mapped to `maa_id = core_id % num_maas`
(`src/mem/MAA/CpuSidePort.cc:203-215`), and a MAA may have multiple indirect units. The
new opcode must be recognized everywhere as one compound operation with `{A:Read,
B:Read, C:Write}`:

- same-MAA IF hazards must compare both reads against every backing write and serialize
  backing write/write conflicts. The current triple-range logic is entered only when
  opcode 17 participates (`src/mem/MAA/IF.cc:511-559`);
- the global invalidator must acquire the normalized three-range lease atomically and
  retain it through final ACK. Read/read leases may coexist; any write conflicts
  (`src/mem/MAA/MultiRangeAccessTracker.hh:16-24,52-107`;
  `src/mem/MAA/Invalidator.cc:89-132,327-348`); and
- every indirect unit on one MAA must arbitrate the shared timed ALU. The current claim
  API provides the required backpressure (`src/mem/MAA/MAA.cc:574-612`;
  `src/mem/MAA/ALU.cc:43-57`). Different MAAs have separate ALUs but still share global
  address leases and ports.

This is an important current gap: `Invalidator::isFusedDirect()` recognizes only opcode
17, so opcode 13 receives only its ordinary single `addrRangeID` lease rather than an
`A/B/C` compound lease (`src/mem/MAA/Invalidator.cc:58-86`). Current direct-index code
alone is therefore not proof of multi-MAA safety for the proposed composition.

`A` responses may be reordered by Row/Grow grouping and `C` writes may complete out of
logical order because each `C[i]` is unique. Provenance `{i, wid}` must follow the word,
and the full token is the only global completion order. Exact-address port serialization
must continue to defer conflicting packets behind a retirement owner
(`src/mem/MAA/Port.cc:48-77`).

### 5.4 Retry and exact-once rules

- A refused/stalled `B` line request retains its pending line and iteration list. Never
  create a second feeder owner for the same `(generation, i)`.
- Row/Offset insertion is the admission transaction. On failure, retain `B[i]`, do not
  increment `i`, drain, and retry. On success, record admission once, then erase the
  private feeder word.
- An `A` request reservation and its Offset chain survive downstream retry and remain
  owned until the response word moves forward.
- A result remains in the ALU if link/combiner capacity refuses it. A combiner word
  remains valid if `createRetirementWrite()` returns false.
- After a retirement packet is internally enqueued, downstream request retry retains
  the packet and outstanding record. Only `WriteResp` counts as retirement.
- Completion requires `scanned == N`, exactly one admission and retirement for each
  selected `i`, `A_expected == A_received`, and `C_issued == C_acked`, with all queues
  empty. Retrying a transition must never increment either side twice.

### 5.5 Drain, checkpoint, reset, and reuse

There is no live-operation serialization. `MAA::drain()` closes admission and panics if
logical state, any function unit/IF entry, address lease, callback, outstanding packet,
or deferred packet remains (`src/mem/MAA/MAA.cc:1678-1706`). Therefore checkpoint only
before submission or after the full token. Live statistics reset is also rejected for
current fused work (`src/mem/MAA/MAA.cc:2209-2223`), and the new opcode must be added to both the
decoding check and `IF::hasFusedDirectInstruction()`
(`src/mem/MAA/IF.cc:664-674`). `check_reset()` already requires Row/Offset, response,
combiner, ALU, write, packet, and direct-feeder state to be empty before unit reuse
(`src/mem/MAA/IndirectAccess.cc:562-621`).

## 6. Can the current opcodes be combined without hidden 16K state?

**No, not as they stand.** They can supply most datapath pieces, but both control
integration and state-shape work are required.

### 6.1 Exact integration points

At minimum, a new opcode must be added to:

| File/function | Required change after the active repair |
|---|---|
| `benchmarks/API/MAA_gem5.hpp:46-65,427-475` | enum, API encoding, no index/result payload tiles |
| `benchmarks/API/MAA_functional.hpp:628-647` | functional FP64 direct-index scalar reference; no scalar variant exists today |
| `src/mem/MAA/IF.hh:35-75,161-173` | enum/name and explicit fourth source-register field |
| `src/mem/MAA/CpuSidePort.cc:218-311,313-516` | access type, word sequencing, three address regions, and raw `rdst1`-to-source remap |
| `src/mem/MAA/IF.cc:104-198,353-434,511-602` | word/tile shape, legality, no `src1MustBeFinished`, RF/tile hazards, memory hazards, completion-only token |
| `src/mem/MAA/IndirectAccess.cc:705-736` | recognize the new operation in both `isDirectIndexLoad()` and `isFusedDirectTransform()` |
| `src/mem/MAA/IndirectAccess.cc:1508-1810` | latch scalar plus min/max/stride; enforce the strict 4K capacities |
| `src/mem/MAA/IndirectAccess.cc:771-1455,2781-2889,3445-3652` | reuse feeder/admission, bounded response, timed ALU/link, and combiner without adding an N-sized side buffer |
| `src/mem/MAA/Invalidator.cc:58-132,327-348` | global `A:Read/B:Read/C:Write` compound lease |
| `src/mem/MAA/MAA.cc:1824-1892,1913-1956,2209-2223` | token dispatch/finish and live-reset detection |
| `benchmarks/spatter/src/Spatter/Configuration.cc:486-507,576-656` | a new arm that does not allocate/stream-load `tile1` and chunks at 4K |

The ALU batch itself need not gain an N-sized structure: it already holds only
lane-count `(i, 8-byte)` entries (`src/mem/MAA/ALU.hh:88-98`).

### 6.2 State audit

The current smoke label `direct_index_4k` is not by itself a strict 4K logical design.
The runner defaults physical SPD to 4K and the combiner to 4K words, but maps both
`direct_index_16k` and `direct_index_4k` to a 16,384-element logical aperture and chunk
(`experiments/scripts/run_xrage_direct_index_smoke.sh:18-36,135-143`). It forwards
`num_offset_table_entries=0`, whose C++ default becomes `num_tile_elements`, hence 16K
Offset entries (`src/mem/MAA/MAA.py:42-47`; `src/mem/MAA/MAA.cc:93-114`;
`experiments/scripts/run_xrage_direct_index_smoke.sh:313-330`).

The opt-in range-pass candidate does cap the active OffsetTable and active RowTable
line slots at 4,096 (`src/mem/MAA/MAA.cc:192-215`;
`src/mem/MAA/IndirectAccess.cc:298-312`). However:

- it requires at least `ceil(logical/active)` passes, LLC-visible `B` rescans, finite
  filtering, and a retained combiner (`src/mem/MAA/MAA.cc:192-215`);
- it allocates two bitmaps, one admission and one retirement bit per logical iteration.
  The source explicitly calls these a four-KiB checker for a 16K gather
  (`src/mem/MAA/BoundedRangePass.hh:12-24,77-107,156-224`); and
- the C++ model allocates every RowTable geometry even though only the selected active
  configuration is checked (`src/mem/MAA/IndirectAccess.cc:210-289`). Those alternative
  tables are model scaffolding, not `B` or result payload, but they preclude a claim
  about a synthesized true-4K structure without a separate design.

Thus the current range candidate has no hidden 16K **payload**, but it does have
logical-16K-indexed exact-once metadata. Removing those bitmaps from a large logical
descriptor would require a new proof: for example, immutable pass bounds, a monotonic
`(pass,i)` scan, advance only after a terminal transaction, per-pass scanned/admitted/
retired/ACK counters, and a proof that pass ranges partition the complete grow space.
That proof is not present today. The safer first implementation is five software-visible
4K descriptors for 20,000 elements.

## 7. N = 20,000 arithmetic and storage/traffic comparison

Assumptions: FP64 `A`/`C`, uint32 `B`, unit-stride dense `B` and `C`, 16,384-element
native chunks, strict 4,096-element combined chunks, no predicate, and useful-word
counts rather than cacheline overfetch. Cache misses, duplicate `A` indices, write
allocation, masking, coherence, and alignment can change physical traffic.

| Quantity | Native stream-load + gather + scalar ALU + stream-store | Proposed strict 4K combined operation |
|---|---:|---:|
| Chunks | 2 (`16384 + 3616`) | 5 (`4*4096 + 3616`) |
| MAA descriptors | 8 total: 2 of each stage | 5 fused descriptors |
| FP64 multiplies | 20,000 | 20,000 |
| Useful `B` memory/cache bytes consumed | 80,000 | 80,000, one feeder scan |
| Index SPD writes | 80,000 bytes | 0 |
| Index SPD reads | 80,000 bytes | 0 |
| Useful selected `A` bytes | 160,000 | 160,000 |
| Gather-result SPD writes + ALU reads | 160,000 + 160,000 = 320,000 bytes | 0 SPD; 160,000 useful bytes move through finite response/ALU state |
| Scaled-result SPD writes + stream-store reads | 160,000 + 160,000 = 320,000 bytes | 0 SPD; 160,000 bytes traverse the timed ALU-result-to-combiner link |
| Total index + result SPD payload traffic | **800,000 bytes** | **0 bytes** |
| Useful `C` stores | 160,000 bytes | 160,000 bytes, acknowledged before completion |
| SPD state | index payload tile plus two FP64 payload tile pairs | one 32-bit completion-token span; no per-element payload |

The native named payload capacity per active 16K worker/chunk is 65,536 bytes for the
index tile plus 131,072 bytes for each FP64 tile pair, 327,680 bytes total. Chunks reuse
those names. This is semantic capacity, not an area claim, and the benchmark allocates
separate names per OpenMP worker (`benchmarks/spatter/src/Spatter/Configuration.cc:486-507`).

The combined path does not make `A` or result transport disappear. It substitutes a
finite response buffer, at most 16 current ALU entries, a finite-width/banked 160,000-byte
aggregate result handoff, a finite `C` combiner, and acknowledged packets for the two
result SPD round trips. All those capacities and stalls must remain in performance
accounting (`src/mem/MAA/IndirectAccess.cc:3487-3652`).

If instead a 16K logical descriptor uses the current 4K range-pass candidate, at least
four passes are required. Each pass scans the descriptor's full `B` sequence and selects
its grow range. For 20,000 total elements that is 80,000 feeder word observations, or
**320,000 useful `B` bytes**, although later scans may hit LLC rather than DRAM. It still
performs only 20,000 selected FP64 multiplies and writes 160,000 useful `C` bytes. This
rescan cost is not part of the recommended five-descriptor single-scan comparison.

## 8. Narrow implementation and test sequence after the fused repair

Do not begin this sequence by modifying the active opcode-17 repair. Land and validate
that repair first, then use it as the fixed arithmetic/retirement substrate.

1. **Freeze the repaired baseline.** Require the existing source contract, exact
   functional result, alias rejection, live-drain rejection, live-reset rejection,
   result-link counters, and final write-ACK gate to pass. Existing anchors are
   `experiments/tests/test_fused_direct_transform_contract.py:32-228` and
   `benchmarks/API/test_fused_direct_transform.cpp:17-104`.
2. **Add only opcode 18 and strict `N <= 4096` semantics.** Add the API/functional
   reference, decoder source-four remap, enum/token shape, dual feeder+fused predicates,
   and compound lease. Reuse the feeder, Row/Offset transaction, bounded response,
   shared ALU, result link, combiner, and ACK path; do not add an N-sized queue or enable
   range passes.
3. **Add source-level contract tests before simulation.** Assert that the opcode is in
   both direct-index and fused predicates; has no SPD source; marks exactly one 32-bit
   completion-only destination; uses all three memory ranges in same- and multi-MAA
   hazards; is recognized by drain/reset checks; and does not reinterpret the scalar as
   an RF destination. Extend the lifetime checks already expressed in
   `experiments/tests/test_direct_index_liveness_contract.py:8-109`.
4. **Add one small guest correctness test.** Cover `N = 1, 15, 16, 17, 4095, 4096`;
   same-line, fanout, duplicate, cross-line, and deterministic random `B`; positive,
   negative, zero, and bit-sensitive FP64 scalars; prefix/suffix guards; and a bitwise
   reference where NaNs/signed zero matter. Reject `N = 4097` at the descriptor boundary
   and verify the software wrapper splits it. Reuse the current direct-index pattern
   vocabulary at `benchmarks/API/test_virtual_index_gather.cpp:16-56,84-114`.
5. **Force every retry boundary with tiny capacities.** Use one feeder line, one
   response slot, a small packed-word pool, one result word/cycle, one result bank, a
   tiny combiner, and one outstanding write. Check one admission and one private feeder
   discard per iteration, 20,000 fused ALU words, 20,000 combiner retirements, and exact
   issue/ACK closure. Include repeated `A` lines and exact-address contention.
6. **Run negative ownership tests.** Reject exact and partial `A/C` and `B/C` overlap;
   either document and pass immutable `A/B` read sharing or reject it in v1. Add
   cross-MAA cases where shared `A/B` plus disjoint `C` may coexist, while `C/C`, `C/A`,
   and `C/B` conflicts serialize. Extend
   `tests/maa/multi_range_access_tracker_test.cc:16-34`.
7. **Exercise lifecycle gates.** A live checkpoint and live stats reset must fail
   closed; a checkpoint immediately after the full token must succeed. Reuse a token
   across sequential 4K chunks only after readiness, and verify no stale page generation
   is observed.
8. **Add the XRAGE arm last.** Chunk 20,000 at 4K, omit `tile1` allocation/stream-load,
   compare bitwise output and guards with `native16x3`, and gate stats on zero index-SPD
   accesses, zero result-SPD accesses, exactly 20,000 direct feeder words (not range-pass
   rescans), 20,000 FP64 fused words, 20,000 retired outputs, and full ACK completion.
   Only then run a small smoke; performance or promotion evidence is outside this audit.

## Final answer

The zero-payload terminal chain is **logically feasible** with existing datapath pieces,
provided it is exposed as a new fail-closed compound operation and preserves the finite
provenance, response, ALU, combiner, packet, lease, retry, and ACK state enumerated above.
The private feeder copy of `B[i]` may die immediately after successful Row/Offset
descriptor admission (and exact-once admission bookkeeping); architectural `B` may not.
Current opcodes 13 and 17 cannot be safely “combined” by API composition alone, and the
current 16K/4K range mechanism cannot honestly be called free of logical-16K state.
Five strict 4K descriptors for `N=20,000` are the narrowest defensible implementation
after the active fused correctness repair lands.
