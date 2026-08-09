# DX100 tile liveness and scratchpad-bypass audit

Date: 2026-08-08
Exact analyzed source base: `93159cf9aadaf1cd657a38249e280093b469b02f`
Machine-readable inventory: `experiments/evidence/2026-08-08_tile_liveness_inventory.json`
Reproducer: `experiments/analysis/inventory_tile_liveness.py`

## Bottom line

DX100 needs tiles for **data that must remain addressable after one functional
unit has consumed or produced it**: communication between Stream, Indirect,
Range, and ALU units; multiple or repeated consumers; condition/range operands;
indirect store/RMW values and old-value results; and CPU-visible gathered data.
The paper makes this the programming model: scratchpad tiles are instruction
sources/destinations and the communication medium between functional units and
cores (p. 3, Section 3), while Section 3.1 explicitly uses intermediate tiles
to support conditions, multiple indirections, range loops, and address
calculation (p. 4).

Tiles are **not intrinsically required to reorder one terminal gather**. Once a
selected `B[i]` has been converted into `{A cache-line address, output i, word
offset, link}` and admitted to Row/Offset metadata, that ingestion copy is
dead. Likewise, once returned `A[B[i]]` has no future data consumer other than
the final ordered memory destination, it can retire directly to memory. Those
two bypasses remove different tiles and must not be conflated.

The exact source supports both distinctions:

- `INDIR_LD_INDEX` reads B directly from memory but still produces a result SPD
  tile; `INDIR_LD_VIRTUAL_INDEX` reads B directly and writes the gather to
  coherent backing (`benchmarks/API/MAA_gem5.hpp:427-523`).
- A virtual destination tile is marked completion-only and is rejected as an
  ordinary SPD source (`src/mem/MAA/IF.cc:375-388,483-488`).
- `INDIR_LD_SPD_STREAM` is not a result bypass: the IF expands it into ordinary
  `INDIR_LD -> result SPD -> STREAM_ST` (`src/mem/MAA/IF.cc:294-338`).
- The special paged scalar consumer eliminates a full 16K logical result, but
  still uses distinct physical input/output SPD pages
  (`benchmarks/API/MAA_gem5.hpp:450-477`; `src/mem/MAA/MAA.cc:779-817,
  1026-1080`).

## Evidence boundary and paper provenance

Primary source: Alireza Khadem, Kamalavasan Kamalakkannan, Zhenyan Zhu, Akash
Poptani, Yufeng Gu, Jered Benjamin Dominguez-Trujillo, Nishil Talati, Daichi
Fujiki, Scott Mahlke, Galen Shipman, and Reetuparna Das, *DX100: A Programmable
Data Access Accelerator for Indirection*, Proceedings of the 52nd Annual
International Symposium on Computer Architecture (ISCA 2025), DOI
[`10.1145/3695053.3731015`](https://doi.org/10.1145/3695053.3731015),
[arXiv:2505.23073v2](https://arxiv.org/abs/2505.23073).

The verified local document is the 18-page author/arXiv version containing the
ISCA text and DOI:

- local path:
  `/data1/nier/worktrees/dx100-research-weekly-synopsis-20260726/references/papers/dx100.pdf`
- SHA-256:
  `ec18bdc585f32e3da5c0fd467e686dd2137b3db88d4c327d510509213e7c44a3`
- retrieved/verified: 2026-08-08
- metadata discrepancy: the PDF identifies itself both as arXiv v2 and the
  authors' ISCA version; all page references here name that exact PDF.

Paper statements, current-source facts, static source inferences, and dynamic
measurements are labeled separately. A static call site is not treated as a
dynamic instruction.

## Three different liveness questions

### 1. Current-instruction descriptor liveness

An instruction descriptor is live from IF admission until its functional unit
finishes. The IF sets its valid bit on insertion and clears it at completion;
completion then updates already-admitted dependent descriptors
(`src/mem/MAA/IF.cc:478-492,616-650`). This descriptor lifetime carries tile
IDs, hazards, ranges, operation, and completion state. It is **control
liveness**, not proof that the underlying data may be discarded.

For an ordinary indirect instruction, the index tile retains a ready credit
until the instruction finishes (`src/mem/MAA/MAA.cc:1828-1857`). The individual
`B[i]` value has a shorter semantic lifetime: Fill reads it, derives the A
address, and inserts the address plus output-order metadata. The direct-index
path proves that its private feeder copy can then be poisoned and erased while
the architectural B memory is untouched
(`src/mem/MAA/IndirectAccess.cc:919-1001,1192-1234`). An insertion failure or
capacity drain occurs before this terminal discard, so retry retains the value.

### 2. Cross-instruction data liveness

Ordinary SPD payload is separate from the descriptor. `SPD::setData` stores a
word and marks its element finished (`src/mem/MAA/SPD.hh:59-89`). A dependent
MAA instruction may issue while its producer is still in `Service`, and its
functional unit consumes only finished elements
(`src/mem/MAA/IF.cc:578-605`; indirect element gating at
`src/mem/MAA/IndirectAccess.cc:1060-1098`). Thus producer and consumer
descriptors may overlap while the tile is the stable cross-instruction name.
After the producer descriptor retires, a consumer descriptor or future CPU
access can still require the payload.

Row/Offset metadata does not substitute for that payload. `RowTableEntry`
retains an A cache-line address and Offset head/tail; `OffsetTableEntry` retains
`itr`, `wid`, and `next_itr` (`src/mem/MAA/Tables.hh:52-101`). Same-line words
share a Row entry and link through Offset entries
(`src/mem/MAA/Tables.cc:143-160,278-306`); grow rows are grouped at issue and
responses recover original iteration order (`src/mem/MAA/Tables.cc:489-535,
573-609,732-755`). None is general later-consumer storage for B or A values.

### 3. CPU consumption

CPU consumption is stricter than MAA element-ready pipelining. A cacheable SPD
read is deferred while the tile-ready credit is outstanding, and only an
accepted request reads `SPD::getDataPtr`
(`src/mem/MAA/CpuSidePort.cc:68-99,640-665`). A source `wait_ready(tile)` is
therefore synchronization, not itself a payload read. The inventory reports
three categories separately: 128 producer-to-raw-pointer subscript edges, 111
producer-to-ready waits, and 32 producer-to-size reads across all scanned
translation units (production-only: 65, 59, and 14). It also reports 16 pointer
bindings made after a producer (production-only: 8). Raw subscripts are exact
visibility sites, although this lexical audit does not classify each as a read
versus write.

## Reproducible source inventory

The tool removes comments, parses balanced API calls, and connects a tile use
only to the latest lexical definition whose brace scope dominates it. It scans
the API tests and the NAS, UME, GAPBS, HashJoin, and Spatter workload sources.
Every input file is SHA-256-bound in the JSON.

Static MAA instruction call sites at the exact base:

| suite | call sites |
|---|---:|
| API workloads/tests | 387 |
| GAPBS | 120 |
| NAS/CG | 56 |
| NAS/IS | 2 |
| UME | 49 |
| HashJoin | 20 |
| Spatter | 21 |
| **Total** | **655** |
| **Production suites only** | **268** |

The principal call counts are 178 Stream Loads, 211 ordinary Indirect Loads,
96 scalar ALUs, 16 vector ALUs, 41 Range operations, 55 indirect RMWs, 16
indirect stores, and 9 Stream Stores. Newer paths contribute 19 direct gathers
with an SPD index, 7 direct-index/direct-result gathers, one direct-index load
that retains a result tile, four SPD-mediated gather/stream forms, and one
special paged scalar/direct-store call.

Exact lexical producer-consumer edge counts are:

| chain class | all | production only |
|---|---:|---:|
| Stream-produced index -> indirect operation | 141 | 57 |
| Indirect result -> ALU | 71 | 22 |
| Result -> Stream Store | 9 | 3 |
| Condition operand | 170 | 47 |
| Range input/output operand | 78 | 42 |
| Indirect store/RMW tile operand | 94 | 42 |
| Result -> CPU payload subscript | 128 | 65 |
| Result -> CPU ready wait | 111 | 59 |
| Result -> CPU size read | 32 | 14 |

There are 166 producer call sites with multiple lexical payload consumers; 61
production producers have the multiple-consumer reason. The JSON preserves all
1,001 edges and their source locations. Compile-time alternatives remain
separate source sites and may not coexist in one binary; this is why these are
static inventory numbers rather than workload frequencies.

## When tiles can be eliminated

### 1. Streamed `B[i]` may feed reorder metadata directly

This is legal when all of the following hold:

1. the current ingestion copy has exactly one semantic consumer: address
   generation for this indirect instruction;
2. no CPU, range, ALU, second indirect operation, later instruction, or repeated
   consumer needs that same materialized tile value;
3. admission retains original iteration/order and response word metadata;
4. predicate/partition rejection and insertion failure preserve retry or make a
   terminal no-use decision; and
5. later rereads, if any, use the architectural B array, not the destroyed
   feeder copy.

The direct-index destructive smoke observed 16,384/16,384 descriptor-admitted
words poisoned after admission with exact output and zero predicate/partition
discards (`experiments/analysis/direct_index_liveness_0108d9b_2026-08-08.md`
and JSON companion). This proves the current-instruction value boundary; it
does not prove that B memory is globally dead.

The static inventory finds 33 single-use candidates: 14 match an existing
direct-index load/gather API shape, 10 are semantically single-use store/RMW
indices for which the current API has no direct-index store/RMW opcode, eight
need predicated direct-index support, and one needs a direct-index plus terminal
sink form. Ten are in production sources: seven current-load shapes, two
store/RMW gaps, and one predication gap. These are candidate counts, not
automatic rewrites.

### 2. `A[B[i]]` may retire directly to final memory

This is legal when the gather has one terminal sequential destination, the
direct write preserves architectural iteration placement, conditions produce
the same selected-write semantics, no CPU/ALU/range/indirect operation consumes
the gathered tile, and completion is defined by the required write
acknowledgements. Current virtual retirement maps `itr` to
`backing + itr * word_size` and issues response-bearing `WriteReq`s
(`src/mem/MAA/IndirectAccess.cc:3063-3082,3263-3325`). Its completion token is
control-only, not a reusable result.

The inventory finds two ordinary `INDIR_LD -> STREAM_ST` candidate pairs, one
in production Spatter (`Configuration.cc:639-640`) and one API attribution
case. It also finds 19 already-direct result sinks and seven already-direct
index+result sinks (production: 11 and 4). Address/range equivalence still must
be proved for each candidate. The four `INDIR_LD_SPD_STREAM` sites are recorded
separately because they materialize the result.

### 3. A simple scalar transform may be fused before direct store

This is legal for a pure elementwise scalar operation, a full overwrite, one
direct sink, no condition requiring old-destination merge, and no second or CPU
consumer. The existing special ABI supports only this narrow pagewise shape:
backing fill -> scalar ALU -> direct Stream Store. At this base the transparent
path is restricted to FP64 multiply and 16K logical/4K physical geometry
(`src/mem/MAA/MAA.cc:779-791`), while the separate logical live slice supports
FP64 scalar ADD/SUB/MUL/DIV/MIN/MAX but is still fixed to one MAA and 16K/4K
(`src/mem/MAA/MAA.cc:1198-1267`). Neither is a general indirect-producer logical
tile.

The static scan finds one exact ordinary gather -> scalar ALU -> Stream Store
candidate, in the API tests, and no production-suite instance. The special
fused API itself has one API call site and no production call site. Importantly,
this eliminates full logical residency but retains a physical source page and
a distinct physical destination page; it is not a zero-SPD datapath.

### 4. Cases that require SPD or coherent LLC-backed logical storage

Storage is required whenever a value outlives its immediate producer-consumer
handshake. Current examples include:

- multiple/repeated consumers;
- indirect result -> vector/scalar ALU or reduction unless the exact one-sink
  scalar fusion above applies;
- CPU payload consumption;
- range boundaries and generated `i/j` tiles;
- condition tiles whose values are reused or not transported in lockstep;
- indirect store/RMW index, vector-value, condition, or returned-old-value
  operands; and
- a result that must remain an ordinary architectural tile after its producer
  descriptor retires.

The inventory marks 368 producers with a concrete storage reason (181 in
production): production reasons include 122 cross-instruction/irregular
operands, 61 multiple-consumer producers, and 51 CPU-payload producers; reasons
overlap. Physical SPD is sufficient only when capacity and simultaneous
lifetime fit. A smaller physical store needs coherent LLC-backed logical
storage with stable `{logical ID, generation, page}` identity, fill/dirty/writeback
state, pins/leases, and acknowledgement before reuse. Backing memory without
that identity is merely a terminal result or software-visible array, not an
ordinary reusable tile.

## Dynamic evidence already present

No new simulator counter or smoke was needed: current frozen traces already
separate the relevant mechanisms.

1. The accepted reorder-survival trace reports `simInsts=31,107`, 16,384
   selected/admitted descriptors, 9,858 issued A-line records, and 845 RowTable
   full drains for its row-64 arm. The 9,858 value counts memory-line issue
   records, **not MAA API instructions**. Exact output passed. This trace proves
   descriptor survival/reconciliation, not frequencies for all benchmarks
   (`experiments/evidence/2026-08-08_reorder_survival_smoke.json`).
2. The validated deterministic 20K XRAGE/Spatter trace dynamically counted:

| arm | MAA total | indirect | Stream LD/ST | scalar ALU | CPU committed |
|---|---:|---:|---:|---:|---:|
| native scale 1 | 6 | 2 | 2 / 2 | 0 | 382,893 |
| direct scale 1 | 4 | 2 | 2 / 0 | 0 | 370,374 |
| native scale 3 | 8 | 2 | 2 / 2 | 2 | 383,238 |
| direct scale 3 + CPU transform | 4 | 2 | 2 / 0 | 0 | 687,037 |

   Direct scale 1 removed the two Stream Stores and was faster in that small
   test. Moving scale 3 to the CPU removed MAA ALU/store instructions but nearly
   doubled CPU instructions and was slower. This is direct evidence that
   result-tile elimination is legal for a terminal gather, while an unfused
   transform has not disappeared—it moved to CPU consumption
   (`experiments/analysis/xrage_direct_multiply_2026-08-03.tsv` and report).

## What the paper says larger tiles improve

The primary paper does not attribute the 1K-to-32K sweep to generic cache reuse
or reusable result tiles. Section 6.4 (p. 11, Figure 13) gives two explicit
causes:

1. larger tiles coalesce duplicate addresses, reducing memory accesses by
   `1.4x` at 32K versus 1K; and
2. they raise bandwidth utilization by 25%, primarily through a 27% higher DRAM
   row-buffer hit rate.

It further says DRAM-controller occupancy/memory-access rate changes minimally
because outstanding-request capacity is unchanged. Sections 3.2 and Figures
3-4 (pp. 4-5) explain the mechanism: same-line requests share Word/Offset
chains, same-row columns issue together, and slices interleave banks/channels.
Therefore the supported interpretation is **duplicate-line coalescing plus a
larger reorder window and better row locality**, not cache-capacity reuse. The
paper's 1.4x reduction is reuse only in the narrow sense that duplicate target
addresses are coalesced within a tile.

This matters because shrinking SPD payload without preserving Row/Offset epoch
capacity can lose the reported mechanism. Conversely, preserving a 16K/64K
logical reorder epoch with a small physical payload does not make the design
free: reorder metadata, coherent backing traffic, page state, and controller
storage remain.

The paper also quantifies why eliminating unnecessary tiles is attractive:
Table 4/Section 6.5 (p. 11) reports Scratchpad at 3.566 mm² of the 4.061 mm²
total (87.8%) and 577.03 mW of 777.17 mW. These are synthesized paper numbers,
not a conversion from gem5 C++ allocations.

## Required storage arithmetic

All cases below have four physical pages per logical tile. An FP64 semantic
page occupies two adjacent 32-bit-lane tile IDs in the current SPD; FP32 uses one
(`src/mem/MAA/SPD.hh:55-89`). `SPD` allocates
`visible_tiles * physical_elements * 4 B` for its visible lane payload
(`src/mem/MAA/SPD.cc:261-286`).

| geometry | type | one physical page | one logical tile | physical src+dst pair | logical src+dst pair |
|---|---|---:|---:|---:|---:|
| 4K physical / 16K logical | FP32 | 4,096 x 4 = 16 KiB | 16,384 x 4 = 64 KiB | 32 KiB | 128 KiB |
| 4K physical / 16K logical | FP64 | 4,096 x 8 = 32 KiB | 16,384 x 8 = 128 KiB | 64 KiB | 256 KiB |
| 16K physical / 64K logical | FP32 | 16,384 x 4 = 64 KiB | 65,536 x 4 = 256 KiB | 128 KiB | 512 KiB |
| 16K physical / 64K logical | FP64 | 16,384 x 8 = 128 KiB | 65,536 x 8 = 512 KiB | 256 KiB | 1 MiB |

The B/index stream is always 32-bit in the current ordinary indirect path. Its
own one-page/logical capacities are therefore 16/64 KiB for 4K/16K and 64/256
KiB for 16K/64K. Those bytes are separate from an FP32/FP64 gather result. A
terminal direct-index path can remove the B physical page; a terminal virtual
result path can remove the result physical/logical tile; a paged transform may
remove full logical residency while retaining the physical src+dst pair.

## Reproduction

```bash
python3 experiments/analysis/inventory_tile_liveness.py \
  --analyzed-revision 93159cf9aadaf1cd657a38249e280093b469b02f \
  --check experiments/evidence/2026-08-08_tile_liveness_inventory.json

python3 -m unittest experiments.tests.test_inventory_tile_liveness -v
```

The committed JSON includes verified paper provenance, the analyzed revision,
SHA-256 of every scanned translation unit, aggregate call-site counts, every
classified flow edge's producer/consumer location and tile name, legality
candidates, storage arithmetic, and hashes/extracts of the reused dynamic
evidence.
