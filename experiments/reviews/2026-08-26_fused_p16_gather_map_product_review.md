# Fused p16 gather-map-product review (2026-08-26)

## Recommendation

**Implement one guarded FP32 micro/`CG_NA=256` prototype; do not promote it
and do not model it as a zero-cost flag.**  The existing RowTable/OffsetTable,
bounded virtual-response slots, SoA/JIT value-line coalescer, virtual destination
combiner, acknowledged retirement writes, and ordinary FP32 ALU are sufficient
building blocks.  The current ABI and datapath are not sufficient unchanged:
they cannot name the dense coefficient span or perform a timed return-time MUL.

The smallest honest slice is a fused form of `INDIR_LD_VIRTUAL_INDEX`:

```
source       = p
index        = colidx
coefficient  = a                 // indexed by original logical ordinal k
backing      = products          // product[k] = p[colidx[k]] * a[k]
completion   = existing virtual-producer completion token
```

It must fill one complete 16,384-entry p Row/Offset epoch, fail closed rather
than drain it, and retire products only to `backing[k]`.  The current page-fed
q16 RMW may start only after that fused completion token closes every product
WriteResp.  Early p/q overlap and a combined p+q descriptor are explicitly not
part of the minimum slice.

No gem5 or native run was launched for this review.  Source authority is base
`43df157f20b3`; completed performance evidence is identified below.  The active
full cache-on service launched from `43de2d95e5ed` is prelaunch infrastructure,
not result evidence.

## Exact ownership today

### Row/Offset source ownership

- `src/mem/MAA/Tables.hh:52-57` stores logical `itr`, source word `wid`, linked
  `next_itr`, and bounded-pass identity in each Offset entry.
- `src/mem/MAA/Tables.cc:348-367` appends every alias for a physical cache line
  to its Offset chain.  Duplicate `colidx` values therefore create distinct
  logical entries; they are not collapsed.
- Direct-index fill derives the p address from `idx=colidx[k]` and the source
  word from that address (`src/mem/MAA/IndirectAccess.cc:3875-3921`), then
  inserts `(logical_itr=k, wid=source-word)` into Row/Offset
  (`src/mem/MAA/IndirectAccess.cc:4119-4121`).
- A bounded source claim retains its chain head, word count, exact RowTable
  slice/row/entry, grow address, and source address until response
  (`src/mem/MAA/IndirectAccess.cc:6460-6680`).  The issued read and reservation
  are created together at `src/mem/MAA/IndirectAccess.cc:2648-2688`.
- An arbitrary-order p response is accepted only by the reservation for its
  physical address; the response slot inherits that exact chain/claim identity
  and captures either the full line or only useful words
  (`src/mem/MAA/IndirectAccess.cc:8686-8805`).
- Retirement peeks the chain head, consumes exactly one Offset entry only after
  its output word is accepted, and releases the native Row claim only after the
  whole chain drains (`src/mem/MAA/IndirectAccess.cc:9453-9620`).

This is already the required p response authority.  A fused implementation
must not create another ordinal table or reconstruct `k` from return order.

### Logical output and WriteResp ownership

- `backingWordAddr(k)` is exactly `backing + k*word_size`, with registered-span
  bounds checks (`src/mem/MAA/IndirectAccess.cc:9101-9120`).
- The combiner is keyed by that logical backing line and rejects a duplicate
  output word (`src/mem/MAA/IndirectAccess.cc:9623-9852`).  Its useful-word
  payload is fixed-capacity and independently referenced from line tags
  (`src/mem/MAA/VirtualCombinePayloadStore.hh:13-21`, `:80-134`).
- Retirement issues cacheable `WriteReq` packets, tracks exact-address
  exclusion, counts an outstanding response, and records page/word visibility
  metadata before send (`src/mem/MAA/IndirectAccess.cc:9369-9448`).
- `MAA::recvTimingResp` routes a `WriteResp` back to the owning indirect unit
  (`src/mem/MAA/Port.cc:773-805`).  The indirect unit then removes the exact
  outstanding address and only then exposes line/page readiness
  (`src/mem/MAA/IndirectAccess.cc:10349-10477`, `:9336-9361`).

The fused product must use this retirement owner directly.  It should not
claim that a product is visible at multiplier completion or write issue.

### Existing coefficient, apply, publisher, and page-fed primitives

- `SoaJitValueCoalescer` has 128 physically provisioned 64-byte owner lines,
  512 injective waiter identities, generation/physical-line matching, fixed
  prefetch credits, and cache-on retention
  (`src/mem/MAA/SoaJitOverlapState.hh:131-155`, `:270-457`).  The selected
  evidence activates 32 lines, without
  adding bytes or ports.
- Current SoA/JIT maps a value by the Offset entry's logical `itr`, coalesces its
  coefficient line, and retains `(generation, context, slot, logicalItr,
  aWord, valueWord)` until delivery (`src/mem/MAA/IndirectAccess.cc:4807-4913`).
  Apply is limited to one/two/four owned lanes and consumes only the ordered
  Offset head (`src/mem/MAA/IndirectAccess.cc:4948-5085`).
- Those apply lanes currently implement only ADD/MIN/MAX, not MUL
  (`src/mem/MAA/IndirectAccess.cc:5127-5153`).  Treating them as free
  multipliers would be a hardware overclaim.
- The ordinary ALU is explicitly 16 lanes at one-cycle lane latency
  (`src/mem/MAA/MAA.py:270-272`) and implements FP32 MUL
  (`src/mem/MAA/ALU.cc:522-545`).  It already has a controller-internal,
  callback-bearing direct-line reuse precedent that charges ordinary lane
  latency (`src/mem/MAA/ALU.hh:47-56`, `src/mem/MAA/ALU.cc:78-113`), although
  that present form is FP64 line-times-scalar and cannot serve this operation
  unchanged.
- The response-bearing SPD publisher holds eight authoritative 64-byte copies
  through retry and arbitrary-order WriteResp, keyed by owner/generation/
  address/page/line (`src/mem/MAA/ResponseBearingSpdPublisher.hh:13-30`,
  `:50-70`, `:197-314`).  Its live path keeps the source SPD tile and completion
  token owned until all responses close (`src/mem/MAA/StreamAccess.cc:563-653`,
  `:656-750`, `:835-877`).  The fused path has no product SPD page, so it should
  remove this guest stage rather than pretend to reuse it.
- The page-fed ABI is exactly four ordered 4K index pages and a close, with
  16 bytes of persistent control and no index payload
  (`include/gem5/maa_page_fed_soa_abi.hh:18-79`, `:81-113`).  Admission inserts
  `itr=page*4096+lane` and refuses any Offset/Row pressure
  (`src/mem/MAA/IndirectAccess.cc:3370-3509`); execution cannot begin before
  exact close (`src/mem/MAA/IndirectAccess.cc:3541-3576`).  CG currently names
  coherent products as the q value span and waits product publication before
  close (`benchmarks/NAS/cg/cg.cpp:489-623`).

## Minimum new ABI, state, and control

### ABI

Use the existing 64-byte IF slot and a guarded shape, not a new descriptor:

1. `opcode=INDIR_LD_VIRTUAL_INDEX`, `datatype=FP32`, `optype=MUL` identifies
   the fused form; ordinary virtual-index loads retain `optype=NA`.
2. Word 2 is p, word 3 is product backing, word 4 is colidx, and previously
   illegal word 5 is the registered, 64-byte-aligned coefficient base `a`.
3. Existing min/max/stride registers must describe exactly 0/16384/1 for the
   first slice.  Product, p, colidx, and a registered spans must be pairwise
   non-overlapping where either side writes; the IF hazard set must declare
   p/colidx/a READ and product WRITE.  The current non-SoA access set records
   only the base region (`src/mem/MAA/IF.cc:243-256`), so explicit four-span
   ownership is part of the ABI change.
4. The descriptor is accepted only with 16K Offset capacity and epoch capacity,
   one direct-index partition, RowTable reordering enabled, no predicate, no
   bounded-global fallback, and a 16K product span.  Any Row/Offset pressure
   before ordinal 16384 is a terminal configuration error, not a drain.

`benchmarks/API/MAA_gem5.hpp:642-663` shows the current five-word
virtual-index form; `src/mem/MAA/CpuSidePort.cc:613-700`, `:702-833`, and
`:835-1021` show that word 5 is presently illegal for it.  The guarded sixth
word is therefore the minimum expressible delta.  It can reuse the decoded
`predicateAddr`/range fields as coefficient semantics, so the simulator's
instruction mirror need not gain another address field.

### Response-to-product state machine

For each of the existing eight virtual response slots, add four states:
`NeedCoefficient`, `AwaitCoefficient`, `AwaitMultiply`, and `ProductReady`.
The head Offset entry remains authoritative throughout.

1. Derive coefficient address `a + entry.itr*4`; use coalescer waiter
   `response_slot` with the fused operation generation.
2. Do not consume the Offset entry when the coefficient request is issued or
   returned.  Ask the ALU for one explicitly timed FP32 pair only when it is
   idle/credited.
3. Carry an exact ALU token `{generation, indirect_unit, response_slot,
   offset_slot}`.  On callback, verify the response slot still owns the same
   Offset head and overwrite the already-retained p word with the product.
4. Insert that product into the existing logical destination combiner.  Only a
   successful insert may consume the Offset head and advance the source chain.
5. Clear the coalescer generation, all waiters, ALU tokens, response states,
   combiner words, and WriteResp owners before completing the producer token.

Overwriting the retained p word means no new product payload is required.  A
source line with 16K duplicate indices may occupy one response slot for 16K
coefficient/multiply steps, but storage remains bounded and forward progress
is explicit.

### Byte and port ledger

| Item | Increment | Bound/charge |
|---|---:|---|
| Guest coherent backing | **-262,144 B** | Removes four cores x 16K x 4-byte virtual-p; retains the 262,144-B product array. |
| Per-window semantic traffic | **-65,536 B writes, -65,536 B reads** | Removes virtual-p materialization and reload for each 16K thread window. Product retirement writes remain; their line count is combiner-dependent. |
| Descriptor payload | 0 B modeled | Reuses IF word 5 and existing decoded address/range fields. |
| Row/Offset payload | 0 B | Reuses exactly 16K existing entries and one epoch. |
| Coefficient payload/owners | 0 B | Reuses the existing physical 128 x 64-B pool; the selected 32-line prefix remains 2,048 active payload bytes per indirect unit. |
| p response payload | 0 B | Reuses eight fixed 64-B response lines = 512 B per indirect unit. |
| Product combiner payload | 0 B | Reuses the default fixed 16 x 16 x 8-B maximum-width pool = 2,048 B per indirect unit. |
| Fused response substate | 2 bits/slot semantic; 8 B/unit if byte-coded | Eight existing response slots, no queue. |
| Timed ALU identity | 60 bits/in-flight lane with a 40-bit generation | 4 conservative shared lanes require 240 bits (30 B) of sideband pipeline state; a one-lane prototype needs 8 B when byte-rounded. |
| External memory/cache ports | 0 | p and coefficient reads plus product retirement use existing MAA ports and credit limits. |
| New internal ALU wiring | bounded | Per active lane: two 32-bit inputs, one 32-bit result, valid/backpressure, and the exact token above. |

The existing default slot counts follow `src/mem/MAA/MAA.py:84-115`; the
combiner's 8-byte maximum word and 16 references/line are explicit at
`src/mem/MAA/VirtualCombinePayloadStore.hh:23-31`.  The table does not claim
SRAM periphery, wire area, or multiplier area.  Reusing the ordinary ALU avoids
adding a multiplier; widening SoA apply lanes to MUL instead would need an
explicit multiplier-area/timing charge and is not the minimum recommendation.

For a non-magical first model, hold both matched arms at eight response slots,
16 combiner slots, four ways/four banks, one virtual word attempt per cycle per
indirect unit, 32 outstanding acknowledged writes, 32 active retained value
lines, zero sequential prefetch credits, and a one-cycle, backpressured direct
ALU request.  Unlimited word service (`virtual_words_per_cycle=0`), zero-bank
combining, instantaneous multiplication, or untagged callbacks are rejection
conditions, not optimistic variants.

## Hazards and required behavior

- **Duplicate `colidx`:** one p word may own many Offset entries.  Multiply each
  entry by its own `a[k]`; never merge logical products.  A duplicate product
  ordinal must remain fatal.
- **Reordered p/coefficient returns:** p uses source-address reservation plus
  Row/Offset head; coefficient uses `(generation,paddr,waiter)`; multiply uses
  the exact ALU token.  Arrival order cannot select the logical slot.
- **Coefficient locality:** a is dense in k, but p RowTable order can visit k in
  a hostile order.  The theoretical floor is 1,024 coefficient lines/window;
  the no-retention ceiling is 16,384 requests/window.  Do not assume the floor.
  Measure issues, fills, merged waiters, hits, evictions, deliveries, and stalls.
- **Write combining:** product destinations are logical-order addresses reached
  in p-return order.  Partial/masked line retirement and exact-address retries
  are legal, but q cannot read until every semantic word is issued and every
  final WriteResp closes.  Report semantic words and transport lines separately.
- **Coefficient/product alias:** reject all overlap with the product write span;
  also reject product overlap with p or colidx.  a and p may both be READ only,
  but the first ABI should still require disjoint spans for audit simplicity.
- **q consumer overlap:** the current useful overlap is stream product
  publication versus disjoint q index-page admission.  Fused production owns an
  indirect unit and the product WRITE range, while page-fed q reads that range.
  The minimum path waits fused completion before q open.  An early-page hazard
  exemption would also require generation-bound line visibility, a second
  indirect context for the core, and proof that Build cannot read an incomplete
  page; reject it from this slice.
- **Failure/terminal:** forbid epoch drain, global-merge fallback, hidden spill,
  host SPD access, virtual-p allocation, publisher traffic, stale ALU response,
  unmatched coefficient response, open coalescer waiter, or unacknowledged
  product write.

## Evidence comparison and inference boundary

Completed cache-on p16/q16 evidence is exact for bounded CG only.  At
`CG_NA=256`, page-fed cache-on is 420,140,526 simTicks and reduces value reads
from 163,840 to 10,305; at 1024 it is 2,363,254,855 simTicks and reduces value
reads from 1,064,960 to 66,862
(`experiments/analysis/cg_page_fed_q16_value_retention_2026-08-26.md:97-117`).
Both sizes preserve p16/q16, 524,288 B physical SPD, 524,288 B external backing,
exact fingerprints/reductions, and closed issue/response ledgers
(`:119-137`).  This supports reusing the retained value-line pool for dense
ordinal coefficients; it does not prove the p-return locality will match q.

Existing direct4/q16 cache-on is 184,629,936 simTicks at 256 and 837,625,247 at
1024, respectively 56.0552% and 64.5563% below page-fed cache-on
(`experiments/analysis/cg_page_fed_q16_value_retention_2026-08-26.md:139-156`).
It retains only the 262,144-B product backing but gives up p16, so that gap
confounds virtual-p removal with loss of p reorder (`:158-176`).  Earlier matched
cache-off direct4 results show the same direction at 1024 and 4096, but retain
the same confound
(`experiments/analysis/cg_direct4_product_page_fed_q16_2026-08-26.md:161-189`,
`:191-232`).

The fused candidate is valuable precisely because a same-binary,
same-checkpoint page-fed/fused pair would remove that confound: both arms keep
p16 and q16; only virtual-p materialization, page-local p reload/MUL, publisher
handoff, and the new timed return-time path change.  No speedup is predicted
from the source audit alone.

## Smallest exact validation plan

### 1. Unit/state validation (no simulator)

- Decode only the guarded FP32/MUL/six-word shape; reject legacy collisions,
  missing/misaligned/unregistered spans, non-16K geometry, capacity below 16K,
  a second epoch, and every aliasing write span.
- Drive one 16K model with all-same p index, repeated words across several p
  lines, adversarial coefficient-line reuse, reversed p responses, shuffled
  coefficient responses, ALU backpressure, combiner eviction, masked writes,
  and reordered WriteResps.
- Require bit-exact `product[k] == fp32(p[colidx[k]] * a[k])` in all 16,384
  logical slots, one consume/ALU/product/write completion per k, one p epoch,
  bounded high-water marks, and empty terminal state.

### 2. Exact 16K handoff micro

Create one guest micro with a four-segment collision pattern (all-same source,
same-line duplicates, cross-page source reuse, and pseudorandom source), unique
finite FP32 coefficients, sentinel product backing, and a CPU reference.  Run
fused p16, wait its completion, then feed the existing four page-ordered q index
pages to page-fed q16.  Require a full product bit dump/hash and q output hash,
zero sentinels/errors, exact p/coefficient/ALU/product/q ledgers, zero drains,
zero publisher lines, zero virtual-p bytes/traffic, and no fallback.  Include a
trace assertion that at least one p response and one coefficient response arrive
out of issue order; the unit test remains the deterministic reorder oracle.

### 3. Matched `CG_NA=256`

Use one guest, checkpoint, simulator, Ramulator, input, deterministic reduction
mode, eight tiles/core, 4K physical pages, 16K Offset/epoch, 32 RowTable slices,
and the finite port knobs above.  Run serially:

1. `page_fed_product_soa_jit` cache-on control;
2. fused-p16-product + current page-fed-q16 cache-on candidate.

Require byte-identical raw/quantized fingerprints and all deterministic
reduction records before reading first-ROI `simTicks`.  For the known ten full
windows, require exactly ten fused p epochs, 163,840 source ordinals,
coefficient deliveries, timed MUL accepts/completions, product insertions, and
product semantic write completions; zero epoch drains, virtual-p bytes,
virtual-p traffic, response-bearing product-publisher lines, global fallback,
or host payload access.  q must retain ten operations, forty page admissions,
fifty command responses, 163,840 value deliveries, and its closed A/value/write
ledgers.  Revalidate immutable artifacts and exact config delta before comparing
latency.

The implementation milestone passes on exactness, boundedness, ownership, and
the advertised 262,144-B backing removal.  Performance promotion additionally
requires lower matched `simTicks` than the new page-fed control.  The archived
direct4 number is descriptive context, not a threshold and not a matched arm.
Do not launch full CG from this gate.

## Handoff

Implement only the guarded producer and the exact micro/256 runner next.  Keep
the active full cache-on page-fed run separate; when it becomes terminal it may
update the control context, but it cannot retroactively validate the fused
mechanism.  If direct ALU reuse cannot retain a tagged one-cycle result without
an unbounded queue, or if one complete 16K p epoch fails the selected RowTable
geometry, reject this slice rather than adding hidden product/ordinal storage or
falling back to four physical gathers.
