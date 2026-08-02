# Independent review: baseline DX100 indirect-gather dataflow and storage

Date: 2026-08-02

Production-source base: `4efff4ea0add3f80ca188262d37ce51af0c6bf78`

External brief reviewed read-only: `/data1/nier/dx100-research` at
`33f90976d54dcc8b3852295b3ebefb398ffc015e`

Method: fresh production-source trace and derived arithmetic. No gem5 simulation,
large build, or production edit was performed.

## Findings first

1. **Baseline `A[B[i]]` is an SPD-producing operation.** The ordinary
   `INDIR_LD` reads each 32-bit `B[i]` from its `src1SpdID`, records only the
   derived A cache-line/DRAM-row identity plus `{output iteration, word within
   line, next}` reorder metadata, reads 64-byte A lines, and writes every
   selected returned A value to `dst1SpdID[i]`. It has no final-output bypass.
2. **“Writes all results before a consumer” has two different answers.** A CPU
   SPD read is rejected until the tile-ready credit returns, so the CPU observes
   the completed tile. A later MAA ALU or stream store may enter service while
   the producer tile is still `Service` and consume only finished elements, so
   production and consumption can overlap. The fused SPD-stream opcode is also
   SPD-mediated; it merely inserts an `INDIR_LD` plus a `STREAM_ST`.
3. **A 16K tile means 16,384 values, not 16 KiB.** One FP32 tile occupies one
   32-bit lane tile: 65,536 B. One FP64 tile occupies two adjacent lane tiles:
   131,072 B. The source defaults are four cores, eight visible lane tiles per
   core, and **one** MAA: 32 visible lanes = 2 MiB at physical=16K.
4. **The exact base allocates more than the visible 2 MiB.** `SPD::SPD`
   unconditionally appends four private 4K 32-bit lanes per MAA for the
   not-yet-wired two-slot cache. With the source-default one MAA, exact
   `tiles_data` is 2,162,688 B (2.0625 MiB) at physical=16K and 589,824 B
   (576 KiB) at physical=4K. The familiar 2 MiB/512 KiB figures are the visible
   prefix only.
5. **Baseline locality comes from line coalescing and DRAM-row issue order.** A
   RowTable slice groups A cache-line addresses under a derived `grow_addr`; an
   OffsetTable linked list attaches every logical output iteration and word ID
   to that line. Build walks slices in bank-interleaved order and drains all rows
   with the selected grow before moving on. It is not a general 16K sort buffer.
6. **The 16K-scaled reorder structure is the OffsetTable, not the RowTable.** By
   default the OffsetTable capacity and drain epoch are both 16,384 entries per
   indirect unit. RowTable allocation is a function of DRAM organization and
   row-table parameters. Its occupancy can force drains during a 16K epoch, but
   it has no “16K rows/entries” setting.
7. **A normal 16K gather over a 4K physical SPD is illegal.** Baseline response
   writes and `setSize` both reject element/size values beyond
   `physical_tile_elements`. The current hybrid is legal because the virtual
   gather retires selected results to coherent backing, then a separate narrow
   controller loads, computes, and stores four 4K pages. Calling this an
   ordinary reusable 16K SPD tile backed by 4K payload would be misleading.
8. **The old direct-output path and the two-slot substrate make different
   promises.** `INDIR_LD_VIRTUAL` preserves the baseline reorder engine but
   replaces result-SPD writes and a later stream store with acknowledged writes
   to the final/backing array; its tile ID is completion-only. The fixed
   two-private-slot cache core instead specifies reusable logical page identity,
   fills, leases, dirty state, and response-matched writeback, but it is not
   instantiated in the gem5 datapath; the decoded ABI deliberately panics.

The labels below are deliberate:

- **Source-proven** means executable production source at the stated commit.
- **Derived** means arithmetic or a lower bound from source-proven dimensions.
- **Unresolved** means synthesis, implementation, or performance evidence that
  this source cannot establish.

## 1. Baseline `A[B[i]]`: exact residency and movement

### B/index values and the A array

**Source-proven.** For ordinary `INDIR_LD`, `Instruction::getWordSize` fixes the
`src1SpdID` operand to four bytes even when A/result values are FP64
(`src/mem/MAA/IF.cc:98-121`). Decode assigns `src1SpdID` to `my_idx_tile`, the
A base address to `my_base_addr`, and `dst1SpdID` to `my_dst_tile`
(`src/mem/MAA/IndirectAccess.cc:1036-1044`). In Fill, the unit reads
`B[i] = SPD[my_idx_tile][my_i]`, computes
`A_base + result_word_size * B[i]`, translates its 64-byte-aligned address, and
derives both a RowTable slice and `grow_addr`
(`src/mem/MAA/IndirectAccess.cc:901-927`). Thus:

- B/index payload is a 32-bit SPD tile for baseline `INDIR_LD`.
- A payload remains in registered ordinary memory under `baseAddr` until its
  cache line returns.
- The newer `INDIR_LD_INDEX`/`INDIR_LD_VIRTUAL_INDEX` direct-index variants are
  different opcodes and are not the original baseline (`src/mem/MAA/IF.hh:35-52`).

The software allocator reserves one lane for a 32-bit type and two consecutive
lanes for a 64-bit type (`benchmarks/API/MAA_gem5.hpp:181-188`), which is why a
typical FP64 gather has an FP32 B tile and an FP64 result lane pair.

### What RowTable and OffsetTable retain

**Source-proven.** `OffsetTableEntry` is exactly three C++ `int` fields:
`itr`, `wid`, and `next_itr` (`src/mem/MAA/Tables.hh:52-56`). Insert records the
original logical output iteration, its word position within the returned A
cache line, and a linked-list successor (`src/mem/MAA/Tables.cc:143-160`).

`RowTableEntry::Entry` retains `Addr addr`, `first_itr`, and `last_itr`
(`src/mem/MAA/Tables.hh:95-101`). Here `addr` is the aligned translated A
cache-line address; the first/last fields are OffsetTable head/tail IDs. A line
hit appends another OffsetTable node; a new line allocates one RowTable line
descriptor (`src/mem/MAA/Tables.cc:278-306`). `RowTableSlice` adds the derived
DRAM `grow_addr` and validity/sent/claimed state (`src/mem/MAA/Tables.hh:137-143,
195-207`). Neither table retains B values or returned A values.

### Returned A values

**Source-proven.** Build obtains A cache-line addresses from the RowTable and
emits one read per descriptor (`src/mem/MAA/IndirectAccess.cc:1452-1489,
1598-1603`; packet construction is at `:1953-1992`). When the line returns,
`get_entry_recv` resolves the OffsetTable chain (`:2039-2078`) and the ordinary
path writes `dataptr[wid]` into `SPD[dst][itr]`, as FP32 or FP64
(`:2143-2171`). Repeated indices/words therefore still produce each logical
result element; they share a line response and have distinct OffsetTable nodes.

For a predicated gather, a false predicate has no A result to write: it marks
the destination element finished with `setFakeData` instead
(`src/mem/MAA/IndirectAccess.cc:985-999`; `src/mem/MAA/SPD.hh:100-107`). The
unpredicated expression `A[B[i]]` writes every returned result value to SPD.

### Consumer visibility and overlap

**Source-proven.** `SPD::setData` stores the word and marks its per-element
finished bit (`src/mem/MAA/SPD.hh:88-99`). The IF permits a source in either
`Service` or `Finished` state unless `src1MustBeFinished` is set
(`src/mem/MAA/IF.cc:500-565`). ALU and stream units then test
`getElementFinished` before reading an element (`src/mem/MAA/ALU.cc:227-268`;
`src/mem/MAA/StreamAccess.cc:234-254`). Therefore a later MAA consumer or
stream store can overlap the gather but cannot read an unwritten element.

The special `INDIR_LD_SPD_STREAM` does not bypass SPD. IF expands it into an
ordinary `INDIR_LD` whose destination is the SPD tile and a `STREAM_ST` whose
source is that tile (`src/mem/MAA/IF.cc:288-332`). It forces full-source finish
only when the gather and store use the same address region (`:306-307`);
otherwise element-ready flow permits overlap.

A CPU path is stricter. Cacheable SPD reads compute a tile ID using the logical
16K aperture and reject/retry while its tile-ready credit is nonzero
(`src/mem/MAA/CpuSidePort.cc:68-99,709-726`). Only after admission does the CPU
read `SPD::getDataPtr` (`:612-636`). Destination ready credit is returned when
the instruction finishes (`src/mem/MAA/MAA.cc:1103-1122`). Thus a CPU observes
the completed tile, not a partially produced one.

## 2. What “16K tile” means, with storage separated

### Payload arithmetic

**Source-proven dimensions.** Defaults are 8 visible SPD lane tiles/core,
16,384 logical elements, physical=logical when `physical_tile_elements=0`, four
cores, one MAA, and one indirect unit/MAA (`src/mem/MAA/MAA.py:14-21,
138-143`; duplicated by `configs/common/MAA.py:7-32`). MAA forms
`num_tiles = tiles_per_core * cores` and applies the physical fallback
(`src/mem/MAA/MAA.cc:52-71`). SPD indexes each visible lane with a 4-byte
stride per element (`src/mem/MAA/SPD.hh:76-99`). An 8-byte access is legal only
when `tile_id + 1` exists, and status/ready state is updated on that adjacent
lane too (`src/mem/MAA/SPD.hh:58-74`; `src/mem/MAA/SPD.cc:81-103,123-146`).
There is no even-ID rule in these checks; non-overlap comes from allocating two
consecutive lanes per FP64 value tile.

**Derived arithmetic.** One configured logical 16K tile is:

| value type | logical values | visible 32-bit lane tiles | payload |
|---|---:|---:|---:|
| FP32 | 16,384 | 1 | 65,536 B = 64 KiB |
| FP64 | 16,384 | 2 adjacent | 131,072 B = 128 KiB |

The complete source-default SPD payload ledger is:

| physical elements/lane | visible prefix: `32 lanes * elements * 4 B` | hidden tail: `1 MAA * 4 lanes * 4096 * 4 B` | exact `tiles_data` |
|---:|---:|---:|---:|
| 16,384 | 2,097,152 B (2 MiB) | 65,536 B (64 KiB) | 2,162,688 B (2.0625 MiB) |
| 4,096 | 524,288 B (512 KiB) | 65,536 B (64 KiB) | 589,824 B (576 KiB) |

The private tail geometry is two logical FP64 slots/MAA, two 32-bit lanes/slot,
4,096 elements/lane, or 65,536 B/MAA
(`src/mem/MAA/LogicalSPDHiddenPayload.hh:15-38`). It is appended to, not aliased
with, the visible prefix (`:109-137,171-203`), and `SPD::SPD` always uses that
combined allocation (`src/mem/MAA/SPD.cc:260-330`). Public SPD checks still
reject hidden IDs (`src/mem/MAA/SPD.hh:58-68`).

If a configuration explicitly uses four MAAs, the same source arithmetic adds
256 KiB rather than 64 KiB: 2.25 MiB exact at physical=16K and 768 KiB exact at
physical=4K. Four MAAs are **not** the source default.

Finally, `MAAConfig.py` maps two logical SPD address apertures (cacheable and
noncacheable), each sized with `num_tile_elements`, not physical elements
(`configs/common/MAAConfig.py:207-223`). Those are address ranges, not two more
payload arrays.

### Reorder metadata

**Source-proven.** Defaults of zero make both OffsetTable capacity and drain
epoch equal to the 16,384 logical tile size; reorder is enabled by default
(`src/mem/MAA/MAA.py:31-38,53-61`; `src/mem/MAA/MAA.cc:63-75`). Exactly one
OffsetTable is allocated per indirect unit with that configured capacity
(`src/mem/MAA/IndirectAccess.cc:185-187`). Fill drains when occupancy reaches
the epoch limit (`:934-941`).

For one default indirect unit, the C++ host representation contains 16,384
three-`int` entries, 16,384 validity booleans, and a reserved free-list capacity
of 16,384 `int`s (`src/mem/MAA/Tables.cc:123-140`). Under a 4-byte-`int`,
1-byte-`bool` ABI, that is a 278,528 B (272 KiB) raw payload/capacity
subtotal, excluding vector object, allocator, and temporary result-vector
overhead. This is a simulator-host accounting fact, **not** a synthesized SRAM
estimate.

**Derived lower bound, not an implementation claim.** An ideal 16K entry needs
14 bits for `itr`, 15 bits for `next` including null, 4 bits for an FP32 `wid`
(or 3 for FP64), and one valid bit: 34 bits/FP32 entry = 69,632 B (68 KiB), or
33 bits/FP64 entry = 67,584 B (66 KiB). This omits free-entry allocation,
ports, banking, ECC, timing, control, and RowTable state.

**Source-proven.** RowTable capacity is separate. The constructor derives its
slice/configuration counts from Ramulator channel/rank/bank-group/bank geometry,
then allocates every configuration (`src/mem/MAA/IndirectAccess.cc:189-238`).
For each configuration, slices double while subslices and entries/row halve
(`:215-283`); with defaults, line-descriptor capacity per configuration is
`64 rows/slice * 8 entries/subslice-row * total_subslices`, independent of 16K.
Its bit/byte implementation cannot be fixed without a chosen DRAM organization,
hardware encoding, ports, and synthesis.

### Queues and other bounded state

These are not SPD payload and must not be folded into the payload numbers:

- The IF has 8 instruction slots/core, hence 32 default slots
  (`src/mem/MAA/MAA.py:27-29`; `src/mem/MAA/MAA.cc:155-157`).
- Each default stream unit owns a RequestTable with 128 cache-line-address slots
  and 16 `{uint32_t itr, uint16_t wid}` entries/address
  (`src/mem/MAA/MAA.py:47-51`; `src/mem/MAA/Tables.hh:16-46`;
  allocation at `src/mem/MAA/Tables.cc:23-42`).
- Each indirect unit allocates 8 virtual response slots and 16 virtual combiner
  slots by default; each struct contains an explicit 64-byte line array, so the
  bounded line-data subtotal is 1,536 B/indirect unit. A direct-index window is
  limited to one cache line by default. These mechanisms are present but inactive
  for baseline `INDIR_LD` (`src/mem/MAA/MAA.py:63-89,94-97`;
  `src/mem/MAA/IndirectAccess.hh:80-147`;
  `src/mem/MAA/IndirectAccess.cc:125-169`).
- Virtual retirement admits at most 32 writes by default
  (`src/mem/MAA/MAA.py:88-90`), while the two-slot standalone controller's
  default fixed arrays are 2 descriptors x 4 pages, 2 slots, 4 miss entries,
  and 4 leases (`src/mem/MAA/LogicalSPDCacheController.hh:37-65`). Neither is
  baseline reorder storage.
- The Invalidator separately allocates one cache-line status per **logical** SPD
  cache line and one region status per MAA/tile
  (`src/mem/MAA/Invalidator.cc:32-53`). Defaults therefore mean 32,768
  `CLStatus` entries and 32 `RGStatus` entries. This coherence-model state scales
  with 16K even if visible physical payload is set to 4K.
- SPD also allocates one element-finished boolean per visible physical 32-bit
  lane element plus the fixed hidden lanes
  (`src/mem/MAA/LogicalSPDHiddenPayload.hh:141-168`;
  `src/mem/MAA/SPD.cc:300-325`): 540,672 booleans at default physical=16K or
  147,456 at physical=4K. It is simulator readiness state, not automatically a
  one-bit-per-element hardware requirement.

### Simulator-only or not hardware-bounded by this model

**Source-proven inventory; hardware status unresolved.** Do not count the
following STL representation as a physical structure without a separate bound
and encoding: per-tile waiting-unit vectors (`src/mem/MAA/SPD.hh:36-38`),
cache-latency history maps and unique-address sets
(`src/mem/MAA/IndirectAccess.hh:76-78,300-302`), virtual reservation/page maps
and packed-word vectors (`:80-147`), direct-index maps (`:252-267`), and the
central outstanding/deferred packet multisets, unordered maps, and deques
(`src/mem/MAA/MAA.hh:763-823`). Some modeled admission counts are finite, but
the C++ node/allocator representation is not a hardware implementation. The
declared `my_sorted_indices` vector has no production use at this base
(`src/mem/MAA/IndirectAccess.hh:278`); baseline locality does not come from it.

## 3. Baseline locality and the legality/cost of 16K metadata over 4K payload

### Exact locality mechanism

**Source-proven.** For each selected index, Fill computes the translated A line,
its DRAM slice, and `grow_addr`, then inserts `(grow, line, logical itr, wid)`
(`src/mem/MAA/IndirectAccess.cc:911-957`). Within a slice:

1. identical cache-line addresses share one RowTable line descriptor and extend
   its OffsetTable chain (`src/mem/MAA/Tables.cc:278-306`);
2. cache lines with the same `grow_addr` share a row bucket
   (`src/mem/MAA/Tables.cc:489-535`);
3. send walks all line descriptors for the selected grow before selecting the
   next grow (`src/mem/MAA/Tables.cc:573-609`);
4. MAA walks slice IDs in a channel/rank/bank-group/bank-interleaved order
   (`src/mem/MAA/IndirectAccess.cc:261-278`); and
5. response lookup recovers and sorts the attached output iterations before
   writes (`src/mem/MAA/Tables.cc:732-755`).

This provides cache-line coalescing and DRAM-row locality across as much of the
epoch as fits. It does not retain A payload and is not equivalent to sorting all
16K B values.

### What scales with the 16K setting

**Source-proven.** Directly scaled: OffsetTable capacity/epoch; the logical SPD
address aperture; Invalidator cache-line status; virtual page/generation vectors
indexed by visible tile; baseline unit iteration limits; and, when physical also
defaults to logical, visible SPD payload plus element-finished entries. RowTable
dimensions instead scale with DRAM organization/configuration. Instruction-file
depth, stream RequestTable capacity, virtual response/combiner slot counts, and
the two-slot controller capacities are separate parameters.

### 16K metadata with only 4K payload

**Source-proven legality result.** It is physically legal only if the design
changes where logical results live. It is **not legal for ordinary baseline
`INDIR_LD`**:

- each ordinary returned result calls `SPD::setData(dst, itr)`
  (`src/mem/MAA/IndirectAccess.cc:2150-2162`);
- SPD rejects `element_id >= physical_tile_elements`
  (`src/mem/MAA/SPD.hh:69-74`); and
- ordinary completion calls `setSize(dst, my_i)`, which rejects a size above
  physical capacity (`src/mem/MAA/IndirectAccess.cc:873-879`;
  `src/mem/MAA/SPD.cc:223-236`).

The virtual path makes it legal by removing result-SPD writes. It maps logical
iteration `itr` to `backingAddr + itr * word_size`
(`src/mem/MAA/IndirectAccess.cc:2427-2446`), combines returned words by backing
cache line (`:2866-2988`), and uses response-bearing coherent `WriteReq`s
(`:2630-2679`). Completion requires empty response/combiner state and zero
outstanding acknowledged writes (`:3042-3065,3237-3260`).

The narrow transparent controller then exposes only a special fixed flow: one
16K descriptor, one 4K input mapping, one output tile, and fixed page order
(`src/mem/MAA/TransparentSPDController.hh:11-25,56-91,141-263`). The live
integration requires exactly 16K/4K and FP64 scalar multiply, and reserves three
distinct FP64 tile spans (completion token, physical input, output)
(`src/mem/MAA/MAA.cc:649-720`). It emits `STREAM_LD backing -> input SPD`,
`ALU_SCALAR input -> output SPD`, then `STREAM_ST output SPD -> destination`
for each page (`:819-868`).

**Derived cost conclusion.** Retaining the 16K Offset epoch while shrinking
payload is therefore neither free nor inherently contradictory. It trades
visible payload for 16K-scaled Offset/coherence/readiness state, RowTable state,
coherent backing capacity and writes, page reloads, output-SPD residency, and
controller/control traffic. The source proves functionality and modeled
capacities; whether it is cheaper in SRAM area, ports, energy, or timing is an
**unresolved synthesis question**.

## 4. Baseline versus XRAGE direct bypass versus the two-slot cache substrate

| mechanism | preserves | removes/changes | reusable ordinary logical tile? |
|---|---|---|---|
| Baseline `INDIR_LD` | B in SPD; Row/Offset reorder; A-line coalescing; result in visible SPD; ordinary MAA/CPU consumers | nothing from the result path; a final memory result needs a later store | Yes, within physical SPD capacity |
| XRAGE/direct `INDIR_LD_VIRTUAL` | same Fill/Build Row/Offset source reorder and logical output order; exact response-bearing retirement to backing/final C | result SPD writes and the later SPD-to-memory stream store; destination tile becomes completion-only | No |
| Narrow current transparent hybrid | 16K virtual producer reorder/backing plus ordinary native 4K input/output SPD micro-ops | generality: one descriptor, fixed four-page order, special FP64 scalar-multiply-to-store chain | No general logical cache; only its fixed chain |
| Standalone two-slot cache substrate | logical descriptor/page/generation identity; fill, residency, pin/lease, dirty state, distinct overwrite destination, response-matched writeback | would avoid permanently allocating a full logical result tile if integrated | Designed yes; live gem5 datapath no |

**Source-proven.** A virtual load is recognized separately from baseline
(`src/mem/MAA/IndirectAccess.cc:542-560`). Its response path sends each logical
word to the backing combiner rather than SPD (`:2683-2841`). Its token is marked
completion-only and cannot feed an ordinary MAA instruction
(`src/mem/MAA/IF.cc:369-381,439-444`). In the XRAGE runtime arms, `compact16`
calls the direct virtual load, whereas arms literally named `fused16`/`fused4`
call `maa_indirect_load_spd_stream` and therefore remain SPD-mediated
(`benchmarks/spatter/src/Spatter/Configuration.cc:560-597`). “Fused bypass” is
safe only as a conceptual name for the virtual/direct path, not for the
source-named fused SPD-stream opcode.

The two-slot core is explicitly simulator-independent and payload-free; its
default arrays are two descriptors, four pages/descriptor, two physical slots,
four miss entries, and four leases
(`src/mem/MAA/LogicalSPDCacheController.hh:11-41`). Full overwrite atomically
pins a resident source and a distinct destination slot (`:384-460`), retains a
dirty destination until writeback (`:477-499,524-573`), and publishes it only
on an exact matching writeback completion (`:615-653`). Its fixed records are
shown at `:804-828`.

**Source-proven integration limit.** Production search finds no instantiation
of `LogicalSPDCacheController` outside its unit test. The logical ABI is decoded
and validated, then deliberately panics with “integration is not implemented”
(`src/mem/MAA/CpuSidePort.cc:317-424`). The appended two-slot hidden payload is
real allocation, but the controller, scheduler, data movement, and response
ownership are not connected end to end.

One subtle contrast is write completion. Virtual gather retirement issues
`WriteReq` and completes on `WriteResp` (`src/mem/MAA/IndirectAccess.cc:2630-2679,
3237-3260`; response dispatch at `src/mem/MAA/Port.cc:698-727`). Native
`STREAM_ST` emits no-response `WritebackDirty`
(`src/mem/MAA/StreamAccess.cc:465-475`); the port calls `writePacketSent` as soon
as the hierarchy accepts it (`src/mem/MAA/Port.cc:573-595`), and that callback
increments the stream's completion count (`src/mem/MAA/StreamAccess.cc:388-397`).
The transparent chain consequently retires after its final store is accepted,
not after a separate write response (`src/mem/MAA/MAA.cc:1146-1173`).

## 5. Audit of `august_3_virtual_tile_meeting_brief.md`

The following refers to
`/data1/nier/dx100-research/docs/briefs/august_3_virtual_tile_meeting_brief.md`
at the external commit named above.

### Incorrect or materially misleading at this exact source base

1. **“16K Row/Offset reorder epoch” is imprecise** (brief `:10-15,52-54`). The
   16K parameter directly sizes OffsetTable capacity/epoch. RowTable is sized by
   DRAM organization and its independent row/entry parameters. The phrase is
   acceptable shorthand for a 16K producer pass, but not as a storage claim that
   both tables have 16K entries.
2. **“Reusable 4K-element SPD pages” overstates the live hybrid** (brief
   `:21-24`). The 4K visible input and output tiles are reused internally, but
   only by one hard-coded 16K/4K FP64 multiply controller with fixed page order
   and three reserved FP64 spans. They are not a general page cache and do not
   expose an ordinary reusable 16K logical result. The brief narrows this more
   accurately at `:49-62`.
3. **The source-default payload total is not 768 KiB** (brief `:98-106`). The
   768 KiB arithmetic is correct only after explicitly choosing four MAAs. This
   base defaults to one MAA, giving 576 KiB exact at physical=4K. Moreover, at
   the exact source base the hidden tail is appended even at physical=16K. A
   like-for-like exact allocation comparison is 2.0625 MiB -> 576 KiB for one
   MAA (72.73% reduction), or 2.25 MiB -> 768 KiB for four MAAs (66.67%). The
   brief's 62.5% compares a hypothetical native design without the hidden tail
   against a four-MAA hybrid with it; that is a design comparison, not exact
   current allocator accounting.
4. **“Old XRAGE fused bypass” is source-name ambiguous** (brief `:19-24`). The
   described direct-to-final-output mechanism exists, but the runtime arm named
   `compact16` selects it. The arms/opcode named `fused16`, `fused4`, and
   `INDIR_LD_SPD_STREAM` explicitly retain the intermediate SPD result. Readers
   should say “virtual/direct-output bypass” when they mean zero result-SPD data.
5. **“Writes a 16K-element logical result” needs an unpredicated qualifier**
   (brief `:22`). The virtual producer writes every selected result. A false
   predicate is tracked but has no result/backing write. This does not affect the
   cited unpredicated direct-index case if that is indeed the measured workload.

### Correct source claims

- The baseline/direct virtual producer retains the large Offset epoch, while a
  4K payload by itself cannot create a 16K reorder window (brief `:10-15,40-43`).
- The live transparent controller is one descriptor, fixed page order, and one
  special gather-to-scalar-ALU-to-store chain; its final stream store has no
  separate `WriteResp` (brief `:49-62`).
- The private FP64 slot arithmetic is 32 KiB/slot and 64 KiB for a simultaneous
  source/destination pair per MAA (brief `:103-106`).
- The logical ABI is fail-closed and the standalone cache core is not a live
  gem5 datapath (brief `:64-77`).
- The brief correctly labels payload counts as not synthesized area/power or a
  complete controller estimate (brief `:98-106`).

### Unsupported by production source or this source-only audit

- The `14.27%`, `22.68%`, `0.40%`, and `11.93%` performance claims, the three
  `simTicks` rows, request/descriptor counts, identical-binary claim, terminal
  checks, and output hash (brief `:21-24,28-47`) cannot be derived from
  production source. The read-only research repository repeats the numbers but
  the brief provides no raw `stats.txt`, `config.ini`, manifest, command line,
  binary hashes, or immutable artifact path. This review neither validates nor
  disproves them; they require an independent artifact audit.
- “Close to native 16K **because** the producer retains the 16K metadata epoch”
  (brief `:40-43`) is mechanistically plausible and the source proves the
  retained reorder policy, but causal performance attribution requires matched
  traces/counters, not code inspection alone.
- “Two slots are the minimum functional source/destination pair” (brief
  `:21-24`) is supported for this controller's distinct-source/full-overwrite
  operation, not as a universal SPD-cache minimum or an area result. The
  template itself permits a one-slot instantiation for other operations
  (`src/mem/MAA/LogicalSPDCacheController.hh:48-52`), and the tests instantiate
  one (`tests/maa/logical_spd_cache_controller_test.cc:24-27`).
- Any conclusion that the retained 16K metadata plus 4K payload is cheaper in
  silicon remains unsupported until the Row/Offset implementation, readiness
  and invalidation state, ports/banking, controller queues, backing traffic, and
  technology-specific synthesis are costed together.

### Lightweight validation performed

No simulation was run. The three small source-contract/unit gates relevant to
the compared controller/storage structures passed in optimized, sanitized where
provided, and Python-contract forms:

```text
experiments/scripts/run_logical_spd_hidden_payload_unit.sh     PASS (2 C++ + 9 Python)
experiments/scripts/run_transparent_spd_controller_unit.sh     PASS (1 C++ + 11 Python)
experiments/scripts/run_logical_spd_cache_controller_unit.sh   PASS (2 C++ + 12 Python)
```

These gates validate their narrow code contracts; they do not turn the
standalone logical-cache core into a live gem5 datapath and do not validate any
performance number.

## Bottom-line answers

- **Q1:** B is a 32-bit source SPD tile; RowTable keeps derived A line/grow tags,
  OffsetTable keeps output iteration/word/link metadata; returned A values go to
  the destination SPD. All unpredicated results are written there. CPU waits for
  full ready; MAA/stream consumers may pipeline finished elements.
- **Q2:** 16K means 16,384 values: FP32 64 KiB/one lane, FP64 128 KiB/two adjacent
  lanes. Default visible payload is 2 MiB; exact current default allocation is
  2.0625 MiB including the one-MAA 64 KiB private tail. Payload, metadata,
  queues, and host-only containers are not interchangeable accounting terms.
- **Q3:** locality is A-line coalescing plus DRAM-grow grouping and
  bank-interleaved slice issue. Offset state scales to 16K. A baseline 16K
  gather on 4K SPD is illegal; a backing/paging hybrid is legal but has real
  metadata, traffic, and control cost. Silicon cost is unresolved.
- **Q4:** the direct XRAGE/virtual path preserves reorder and final C but removes
  reusable result SPD. The two-slot substrate aims to preserve reusable logical
  page semantics while removing full residency, but is currently allocation +
  control contracts, not an integrated datapath.
- **Q5:** the brief is directionally sound on the mechanism split, but its
  RowTable wording, live-page generality, source-default/four-MAA payload
  accounting, fused naming, and unconditional 16K-result phrasing need the
  qualifications above; its performance claims remain outside production-source
  proof.
