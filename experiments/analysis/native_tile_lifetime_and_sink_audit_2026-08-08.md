# Native DX100 tile lifetime and terminal-sink audit

Date: 2026-08-08

Native source anchor: `b1c793ae1a0c7550f7ac979a6678b4689761be2b`

Scope: read-only audit; no product-code changes

## Executive verdict

1. `B[i]`, reorder metadata, and `A[B[i]]` are three different objects with three different lifetimes. `B[i]` is an explicitly named SPD value. After its address has been translated and successfully inserted, the current indirect instruction no longer needs that numeric value. The row/offset metadata must survive until the matching source-cacheline response. The returned word is transient until copied to the destination SPD element, after which the SPD copy remains software-visible until its last explicit consumer finishes or software overwrites the tile.
2. The SPD is **not** an automatic cache of source values for later instructions. It has tile/element addressing and no source-address tags or lookup. Later instructions reuse a result only when software names that destination tile. A normal LLC hit on a later `A` request is cache reuse, but it is not SPD reuse.
3. Larger native tiles provide a larger per-instruction address/coalescing/reorder window and issue fewer commands. Validated 4K/16K pairs show both effects. They do **not** support an automatic-retained-result explanation. Explicit result retention matters only for workloads that actually have later tile consumers, and must be measured separately.
4. A terminal dense sink `C[i] = f(A[B[i]])` can legally omit the result SPD for identity, scalar multiply, or scalar add when the intermediate is unobservable, arithmetic semantics match, memory aliases are excluded or ordered, and a finite, backpressured ALU/write-combiner path completes before a real completion token. It is not implemented by the native instruction set.
5. Setting `num_tile_elements=4096` shrinks native SPD payload/readiness and the per-iteration OffsetTable to 4K, but it does **not** shrink the default RowTable geometry. In the audited two-channel DDR4 configuration the active RowTable still has 16K cacheline slots; the C++ model also allocates every alternative configuration. Such a run is a 4K logical/SPD configuration, not a fully area-scaled “true 4K” design.
6. Exact arbitrary 16K global ordering cannot be obtained from a true 4K engine for free. The design must retain/spill the missing descriptors somewhere, enqueue them in another 16K-capable structure, or reread/recompute `B`. Merely retaining gathered `A` payloads does not reconstruct the source-request order.

## Evidence discipline

All native semantic and state claims below are resolved against `b1c793ae`, using `git show b1c793ae:<path>`. Line numbers therefore refer to that commit, not to a moving branch. Experimental results are used only where completion, correctness, configuration, and first-ROI metrics are available. Post-`b1c793ae` controlled results are labeled as supplementary experimental evidence and are not used to redefine native code behavior.

Experimental path labels used below are exact absolute directories:

- `BFS-4K` = `/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/gapbs_recovery2/bfs_s22_t4096_m2GB_gem5.opt.ovl_base`
- `BFS-16K` = `/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/gapbs_recovery2/bfs_s22_t16384_m2GB_gem5.opt.ovl_base`
- `UME-4K` = `/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/ume_recovery2/gradzatp_n1000000_t4096_m2GB_gem5.opt.ovl_base`
- `UME-16K` = `/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/ume_recovery2/gradzatp_n1000000_t16384_m2GB_gem5.opt.ovl_base`

The two detailed sweep pairs meet the required gates:

- BFS 4K and 16K have the same simulator SHA, first-ROI statistics, clean `m5_exit`, no panic/fatal, and the same full semantic fingerprint. Evidence: `/data1/nier/dx100-research/tile_sweep_graphs/tile_sweep_snapshot.tsv:1,25,27`; `BFS-4K/.run.log.scan-v1.json:2-10`; `BFS-16K/.run.log.scan-v1.json:2-10`.
- UME gradzatp 4K and 16K have the same simulator SHA, first-ROI statistics, clean `m5_exit`, no panic/fatal, identical output hash, and exact scalar-reference pass. Evidence: `/data1/nier/dx100-research/tile_sweep_graphs/tile_sweep_snapshot.tsv:1,60,62`; `UME-4K/.run.log.scan-v1.json:2-10`; `UME-16K/.run.log.scan-v1.json:2-10`.
- `simTicks` below always means the first ROI dump. The later duplicate dump in each `stats.txt` is not mixed into the comparison.

## Mechanism diagram

```text
software-named index tile                         instruction-local reorder state
SPD[idx_tile][i] = B[i] --read once--> translate --> RowTable {row, CL, first/last i}
                                |                  + Offset[i] {i, word-in-CL, next-i}
                                |                              |
                                |                     row-ordered 64-B reads
                                v                              v
                         numeric B[i] dead*      A / LLC / DRAM response cacheline
                                                               |
                                              lookup (i, word-in-CL), invalidate metadata
                                                               |
                                 native path: SPD[dst][i] = A[B[i]]
                                               |               |
                                      explicit named consumers | response packet dead
                                                               |
                             legal terminal option: f() -> finite C-line combiner -> C

* Dead for this instruction after a successful insert; the native tile remains protected and
  software-visible until all explicitly named consumers release it.
```

## 1. Native value and metadata lifetimes

### 1.1 Numeric `B[i]`

`B[i]` lives in the SPD data array at an explicitly selected index-tile ID. The SPD is one flat allocation of `num_tiles * num_tile_elements * 4` bytes, and access is direct arithmetic on `(tile_id, element_id)`; there is no address tag or associative lookup (`b1c793ae:src/mem/MAA/SPD.hh:29-43,47-72`; `b1c793ae:src/mem/MAA/SPD.cc:202-234`). The public API likewise allocates integer tile IDs and computes an SPD aperture address from `SPD_id * TILE_SIZE` (`b1c793ae:benchmarks/API/MAA.hpp:50-63`; `b1c793ae:benchmarks/API/MAA_gem5.hpp:147-155`).

The indirect unit may pipeline behind a producer: it tests per-element readiness before reading an index (`b1c793ae:src/mem/MAA/IndirectAccess.cc:399-439`). For a taken element it reads `B[i]` exactly at fill, computes `base + word_size * B[i]`, translates the cacheline, derives the word offset, and inserts `(physical cacheline, row, original i, word offset)` (`b1c793ae:src/mem/MAA/IndirectAccess.cc:480-520`). If insertion fails, it does not advance `my_i`, so the numeric index must remain available for retry (`b1c793ae:src/mem/MAA/IndirectAccess.cc:507-512,527`).

Earliest safe discard has two meanings:

- **For this indirect instruction's computation:** after a successful RowTable/OffsetTable insert and translation, the derived metadata contains everything needed to issue the source read and route its response. The numeric `B[i]` need not be read again. For a false condition, no index is read or inserted (`b1c793ae:src/mem/MAA/IndirectAccess.cc:491-525`).
- **In the native machine/software contract:** the whole source tile remains protected. Dispatch increments its not-ready/reference state, and completion releases it (`b1c793ae:src/mem/MAA/MAA.cc:584-603,628-644`; `b1c793ae:src/mem/MAA/SPD.cc:120-139`). Native code does not reclaim a processed prefix. The bytes are not erased when a tile becomes Idle; only element-ready bits are cleared (`b1c793ae:src/mem/MAA/SPD.cc:78-87`). Software may explicitly name the same tile in a later instruction, so physical overwrite is safe only after the last such consumer has completed.

Thus a future streaming implementation may reclaim `B[i]` after insertion only if it adds per-element/generation ownership and proves there is no other consumer. That optimization is not present at `b1c793ae`.

### 1.2 Reorder metadata

The metadata is not the numeric index and not the returned value:

- Each OffsetTable entry contains `{itr, wid, next_itr}` (`b1c793ae:src/mem/MAA/Tables.hh:52-84`). It is allocated with exactly `num_tile_elements` entries (`b1c793ae:src/mem/MAA/Tables.cc:123-139`). Same-cacheline indices form a linked chain (`b1c793ae:src/mem/MAA/Tables.cc:140-151`).
- Each RowTable cacheline entry contains `{addr, first_itr, last_itr}` and is nested under a DRAM-row `grow_addr` (`b1c793ae:src/mem/MAA/Tables.hh:86-127`; `b1c793ae:src/mem/MAA/Tables.cc:222-250,337-383`).
- The Build state walks RowTable slices in DRAM grouping order and creates one read for each represented cacheline (`b1c793ae:src/mem/MAA/IndirectAccess.cc:668-723`). With `no_reorder`, the first occurrence of a cacheline creates its request during Fill instead (`b1c793ae:src/mem/MAA/IndirectAccess.cc:517-520,655-664`).

This state is instruction-local. Decode resets the OffsetTable and active RowTable (`b1c793ae:src/mem/MAA/IndirectAccess.cc:588-598`). It cannot be discarded when a request is merely issued, because the return path still needs original `i` and word-in-cacheline. On response, the matching RowTable entry is invalidated, its OffsetTable chain is returned and invalidated, and the resulting mappings are sorted by original `i` (`b1c793ae:src/mem/MAA/Tables.cc:278-287,452-475`; `b1c793ae:src/mem/MAA/Tables.cc:153-168`). Completion asserts that all packets and latency histories have drained, checks the tables are empty, then clears per-instruction unique-address sets (`b1c793ae:src/mem/MAA/IndirectAccess.cc:766-805`).

The `RT_config_cache` name is potentially misleading: it maps a base address to a RowTable **geometry choice**, not to data or completed loads (`b1c793ae:src/mem/MAA/IndirectAccess.cc:243-264`). It is the only RowTable-related state intentionally retained across instructions.

### 1.3 Returned `A[B[i]]`

A source response is a 64-byte packet. `recvData` retrieves and consumes its mappings, copies the cacheline to a stack-local `new_data`, and for an indirect load writes every mapped word to `SPD[dst_tile][original_i]` (`b1c793ae:src/mem/MAA/IndirectAccess.cc:868-918`). The response packet is deleted after the callback (`b1c793ae:src/mem/MAA/CacheSidePort.cc:30-40`; `b1c793ae:src/mem/MAA/MemSidePort.cc:30-36`). The packet/cacheline copy therefore becomes dead once all mapped words have been copied or otherwise retired.

The native destination copy has a longer lifetime. `setData` marks the individual element finished (`b1c793ae:src/mem/MAA/SPD.hh:65-71`), allowing an already queued ALU/stream/indirect consumer to proceed element-by-element. Whole-tile completion sets size/status and releases dependents (`b1c793ae:src/mem/MAA/IndirectAccess.cc:469-476,766-805`; `b1c793ae:src/mem/MAA/MAA.cc:628-644`; `b1c793ae:src/mem/MAA/IF.cc:305-339`). It remains in the explicit SPD slot until overwritten. A CPU can read it only through the cacheable SPD aperture, which directly returns the selected SPD bytes (`b1c793ae:src/mem/MAA/CpuSidePort.cc:347-372`).

For a false condition, native `setFakeData` marks the destination element ready without overwriting its bytes (`b1c793ae:src/mem/MAA/SPD.hh:73-77`; `b1c793ae:src/mem/MAA/IndirectAccess.cc:523-525`). A fused conditional sink must therefore define whether false elements leave `C` unchanged, are masked from all consumers, or receive a specified value; blindly storing stale SPD bytes would be illegal.

### 1.4 SPD cache verdict

The SPD is software-addressed storage, not an automatic cache for future instructions:

- It has no source address/tag field and no lookup from an `A` address to a prior destination element (`b1c793ae:src/mem/MAA/SPD.hh:29-72`).
- Every indirect-load descriptor names an index tile, destination tile, and source base address (`b1c793ae:benchmarks/API/MAA_gem5.hpp:280-297`).
- The instruction set has explicit stream, indirect, and ALU operands; it has no “probe gathered values by source address” operation (`b1c793ae:src/mem/MAA/IF.hh:34-77,144-175`).
- Per-instruction reorder tables reset, and their only cross-instruction cache contains geometry, not values.

Normal cache hierarchy behavior is separate. A later source request may hit the LLC, and simultaneous outstanding reads to the same physical cacheline may be coalesced in the MAA port. Neither behavior turns the SPD into a source-address cache.

## 2. What the tile-size sweep measures

### 2.1 Verdict: reorder/coalescing and command granularity are real; automatic result reuse is not

Increasing `num_tile_elements` simultaneously changes several things in native code:

1. SPD payload and per-element readiness capacity scale with it (`b1c793ae:src/mem/MAA/SPD.cc:217-234`).
2. The OffsetTable and therefore the maximum per-instruction provenance window scale with it (`b1c793ae:src/mem/MAA/IndirectAccess.cc:99,113-115`; `b1c793ae:src/mem/MAA/Tables.cc:123-139`).
3. Software loops issue fewer tile commands because `TILE_SIZE` is their step and descriptor capacity (`b1c793ae:benchmarks/API/MAA.hpp:14-16`; examples below).
4. The default RowTable storage geometry does **not** scale with it, but a longer instruction can expose more addresses across successive fill/build/drain episodes.

Consequently, a plain tile sweep cannot by itself assign causality. The native source does refute one broad interpretation: there is no automatic SPD reuse by future instructions. Explicit consumers may reuse a tile, but that is a property of the workload dataflow, not a cache behavior caused by tile capacity.

The inspected workloads demonstrate both one-use and materialization-required cases:

- XRAGE/Spatter gather is `stream_load B -> indirect_load A -> stream_store C`; the result tile has one consumer (`b1c793ae:benchmarks/spatter/src/Spatter/Configuration.cc:508-520`). It is a terminal identity-sink candidate, not retained-result reuse.
- UME gradzatp gathers `zone_field[z]` into `tile0`, consumes it once in a vector multiply, and consumes that product once in an indirect RMW (`b1c793ae:benchmarks/UME/gradzatp.cpp:163-179`). A larger tile can reduce command boundaries and improve request grouping even though the gathered value has only one explicit consumer.
- CG similarly gathers `p[colidx[k]]`, multiplies once, then RMWs `q` (`b1c793ae:benchmarks/NAS/cg/cg.cpp:749-768`).
- PageRank gathers into `tile5` and consumes it in an RMW (`b1c793ae:benchmarks/gapbs/src/pr.cc:260-272`). GZZ explicitly obtains cacheable SPD pointers and performs CPU arithmetic on the gathered tiles, so its scratchpad materialization cannot be bypassed without changing that interface (`b1c793ae:benchmarks/UME/gradzatz.cpp:155-189`).

### 2.2 Validated native-pair evidence

| First ROI | BFS 4K | BFS 16K | What it separates |
|---|---:|---:|---|
| `simTicks` | 243,849,400,158 | 217,694,194,930 | 16K uses 10.7% fewer ticks |
| `numInst_INDRD` | 103,598 | 25,945 | nearly 4x command count at 4K |
| `IND_NumWordsInserted` | 414,320,254 | 414,326,414 | essentially identical work |
| `IND_NumCacheLineInserted` | 154,325,365 | 153,788,423 | only 0.35% fewer insertions at 16K |
| `IND_NumUniqueCacheLineInserted` | 154,325,365 | 151,155,687 | 2.05% fewer unique-per-instruction CLs at 16K |
| `IND_NumRowsInserted` | 72,578,945 | 58,348,900 | 19.6% fewer row records at 16K |
| `IND_NumRTFull` | 2 | 1,321,783 | longer windows also create substantial bank/row pressure |

Evidence is the first stats block at `BFS-4K/stats.txt:4,5643,5679-5685` and `BFS-16K/stats.txt:4,5668,5704-5710`. Identical semantic fingerprints are recorded in the run scans cited above. The same number of inserted words, much lower instruction count, and fewer row records support command amortization plus broader grouping. They do not show automatic result reuse. The high 16K `RTFull` count also disproves an “unlimited 16K sort” interpretation.

| First ROI | UME gradzatp 4K | UME gradzatp 16K | What it separates |
|---|---:|---:|---|
| `simTicks` | 7,678,060,272 | 5,837,368,307 | 16K uses 24.0% fewer ticks |
| `numInst_INDRD` / `numInst_ALUV` | 245 / 245 | 62 / 62 | nearly 4x commands at 4K |
| `IND_NumWordsInserted` | 2,849,877 | 2,849,877 | identical indirect element work |
| `IND_NumCacheLineInserted` | 2,391,141 | 1,531,307 | 36.0% fewer CL insertions at 16K |
| `IND_NumRowsInserted` | 326,889 | 198,937 | 39.1% fewer row records at 16K |
| ALU SPD read/write cycles | 159,490 / 111,734 | 183,135 / 123,134 | no reduction in measured SPD materialization cost |

Evidence is the first stats block at `UME-4K/stats.txt:4,5240,5244,5273-5275,5322-5323` and `UME-16K/stats.txt:4,5226,5230,5259-5261,5308-5309`. The exact scalar reference and output hash match. The source dataflow has one consumer of the gather. The equal word count, much lower command count, and reduced cacheline/row insertions again identify amortization and request grouping, not retained-result reuse, as the supported mechanisms for this pair.

Supplementary controlled GZZ evidence isolates command/feed granularity from physical capacity. Holding the logical feed at 16K made physical 32K and 64K identical and 0.92% faster than physical 16K; all seven production points and all six mechanism-control points passed rc=0, first-ROI, `m5_exit`, scalar-reference, and fingerprint gates (`/data1/nier/dx100-research/tile_sweep_graphs/gzz_attribution_report.md:3-15,19-41,58-82`). This control is post-`b1c793ae`; it corroborates the mechanism distinction but is not a native-code claim.

### 2.3 Counters and A/Bs that distinguish the causes

| Question | Existing counters / paths | Decisive experiment |
|---|---|---|
| Did a larger window coalesce more words into source cachelines? | `IND_NumWordsInserted`, `IND_NumCacheLineInserted`, their average, and unique variants. Definitions: `b1c793ae:src/mem/MAA/MAA.cc:911-918,978-983`; increments: `b1c793ae:src/mem/MAA/Tables.cc:140-150,222-250`. | Matched tile-size x `--maa_no_reorder` factorial; compare equal-word cohorts. |
| Did it group source cachelines into fewer DRAM rows? | `IND_NumRowsInserted`, `IND_NumUniqueRowsInserted`, `IND_NumRTFull`; Fill/Build paths and `my_RT_slice_order` (`b1c793ae:src/mem/MAA/IndirectAccess.cc:503-520,668-723`). Source-request latency/hit/access counters further test the downstream effect. | Reorder ON/OFF with identical addresses/work; check RowTable and DRAM row-hit/ACT/PRE or equivalent controller counters. |
| Is the gain mostly command/setup amortization? | `numInst_*`, `IND_CyclesFill/Build/Request`, MAA busy/idle, stream/ALU instruction counts (`b1c793ae:src/mem/MAA/MAA.cc:911-931,955-992`). | Hold address sequence and materialization fixed; vary only logical chunk/descriptor count. The controlled GZZ design is an example. |
| Was a gathered result explicitly reused? | Production writes occur at `b1c793ae:src/mem/MAA/IndirectAccess.cc:909-918`; consumers read at `b1c793ae:src/mem/MAA/ALU.cc:263-274`, `b1c793ae:src/mem/MAA/StreamAccess.cc:405-413`, indirect store/RMW at `b1c793ae:src/mem/MAA/IndirectAccess.cc:924-1038`, and CPU SPD reads at `b1c793ae:src/mem/MAA/CpuSidePort.cc:347-372`. Existing SPD-cycle counters are aggregate proxies only. | Add provenance counters: produced words, reads per destination tile/generation, distinct consumer instructions, CPU reads, and last-use distance. Compare materialize vs fused sink with the same source order. |

There is no native counter that says “this `A[B[i]]` result was reused N times.” Aggregate SPD cycles cannot establish provenance. A defensible causal study is a 2x2 (or 2x2x2) design: 4K/16K, reorder ON/OFF, and materialize/direct-sink (or one/multiple consumers), with exact work/correctness and simulator identity held fixed. If the tile advantage disappears only when reorder is disabled, it is reorder-driven. If direct retirement removes SPD traffic but leaves the size trend, materialization was not the cause. If the trend persists with reorder disabled and traffic fixed while instruction count changes, it is command/setup amortization.

## 3. Terminal `C[i] = f(A[B[i]])` direct retirement

### 3.1 What is and is not native

The native ISA has separate indirect load, scalar/vector ALU, and stream store instructions (`b1c793ae:src/mem/MAA/IF.hh:34-77`; APIs at `b1c793ae:benchmarks/API/MAA_gem5.hpp:184-221,260-297`). An indirect-load response always writes a named SPD destination (`b1c793ae:src/mem/MAA/IndirectAccess.cc:909-923`). Therefore direct retirement is **not implemented at `b1c793ae`**. Its legality can nevertheless be proved under an explicit contract.

### 3.2 Equivalence proof

Let `R_i` be the exact bits returned by the native source read for `A[B[i]]`. Let the unfused sequence have no externally observable read of the intermediate tile except the transform/store chain, and let it eventually make `C[i] = f(R_i)` visible. Replacing

```text
source response -> SPD_tmp[i] -> optional scalar ALU -> SPD_tmp2[i] -> dense store C[i]
```

with

```text
source response -> same f -> dense store C[i]
```

preserves the final architectural value for every `i` if all of the following hold:

1. the fused command observes the same `A`, `B`, condition, and scalar snapshots;
2. `f` is pure and pointwise, so results for distinct dense destinations commute;
3. its datatype, overflow, exceptions, rounding, NaN behavior, and operation contraction match the unfused path;
4. no agent can observe the removed intermediate or a completion event too early; and
5. changed read/write timing cannot affect a possibly aliased input or a data race.

For identity, `f(x)=x`, item 3 is a bit-preserving route and no ALU is required. For one scalar multiply or add, the existing native ALU semantics already define elementwise operations and model `ceil(elements / num_ALU_lanes) * lane_latency` cycles (`b1c793ae:src/mem/MAA/ALU.cc:25-60,263-355`). A fused path must charge comparable finite throughput. An affine `alpha*x+beta` needs two operations or a defined FMA. FMA is not equivalent to separate floating-point multiply and add when intermediate rounding is architecturally visible. Unsigned integer arithmetic can be defined modulo width; signed overflow and floating-point exception behavior must be specified rather than inherited accidentally from host C++.

Because `C[i]` is dense and each `i` is unique, duplicate `B[i]` values do not create output collisions: one source word may fan out to multiple distinct `C` elements. This is different from scatter/RMW duplicate-address semantics.

### 3.3 Required finite hardware

1. **Fused descriptor and retirement mapper.** It needs source `A` base/range, destination `C` base/range, logical start/length/stride, condition semantics, transform opcode, scalar IDs/versions, and the existing `(i,wid)` response mapping. Native `Instruction` has only one `baseAddr`/`addrRangeID` (`b1c793ae:src/mem/MAA/IF.hh:144-175`), so this is a real ISA/state extension.
2. **ALU bandwidth.** Identity needs routing/masking only. Multiply or add needs at least one operation per returned element; affine multiply-plus-add needs two pipelines, two occupied cycles on a shared pipeline, or an explicitly equivalent FMA. The response path must backpressure when ALU acceptance is lower than source-return bandwidth.
3. **Finite destination-line combiner/store queue.** Each entry needs a `C` cacheline tag, data, byte/word valid mask, owner/generation, coherence state, and outstanding-write state. It must merge out-of-order source completions, apply first/last/conditional masks, and block or spill safely when full. A partial line needs byte-enabled coherent stores or a read-for-ownership/merge; emitting a whole `WritebackDirty` without preserved old bytes is illegal. Native stream store demonstrates the read-exclusive, merge, full-line-write pattern (`b1c793ae:src/mem/MAA/StreamAccess.cc:351-365,379-442`) but is a separate instruction and reads values from SPD.
4. **Backpressure and replay.** Source issue/return, ALU, combiner allocation, ownership acquisition, and store-port occupancy must all be bounded. No host `std::map`/`std::set` may silently stand in for unbounded hardware.
5. **Completion token/scoreboard.** Descriptor acceptance is not completion: native dispatch acknowledges a command immediately after it enters the instruction file (`b1c793ae:src/mem/MAA/MAA.cc:584-607`). A fused token becomes complete only after all required `B` elements are consumed, all `A` responses are transformed, all combiner lines are flushed, and the chosen visibility acknowledgement for every `C` write has arrived. Native `wait_ready` is tied to an SPD tile (`b1c793ae:benchmarks/API/MAA_gem5.hpp:108-115`; `b1c793ae:src/mem/MAA/MAA.cc:675-700`); a no-destination-tile operation needs an independent token/generation.
6. **Coherence/visibility contract.** “Store queued,” “cache has exclusive ownership and data installed,” and “globally visible” are different completion points. The token must name one. Dependent CPU/MAA reads must be held until the required point.

A small combiner does not make bypass impossible, but it changes traffic or throughput: it must backpressure, flush partial lines with ownership/merge, or spill. A model that accepts all out-of-order returns while retaining arbitrary destination-line masks in an unbounded host container is not a finite implementation.

### 3.4 Required alias and hazard rules

The safe default is to require registered, non-overlapping `A`, `B`'s producer range, and `C`, plus exclusive write permission for `C` through completion. More permissive cases need proofs:

- **`C` overlaps unread `A`:** an early direct store may change a later gather value. Either reject overlap, snapshot/issue and protect every source read before any conflicting store, or reproduce the unfused ordering.
- **`C` overlaps the memory source still producing `B`:** even though consumed `B[i]` is in SPD, a direct store can corrupt a not-yet-produced index. Reject, wait for the entire index producer, or track exact unread ranges.
- **Concurrent readers/writers of `C`:** acquire an exclusive range/line lease and order later readers after the completion token. CPU caches must be invalidated or coherently updated.
- **Concurrent writers to `A` or scalar registers:** hold a read lease/snapshot for `A`; snapshot scalar bits/version until the final transformed response. Native register writes already stall while any queued instruction names that register (`b1c793ae:src/mem/MAA/IF.cc:235-250`).
- **Conditions/tails:** preserve byte masks and the specified false-element behavior. Native false gather elements are readiness-only, not zero writes.
- **Faults/replay:** define whether partial `C` visibility is allowed. Precise rollback requires buffering/undo or delayed publication; otherwise faults must be ruled out before publication.
- **Cross-MAA ordering:** apply the same range ownership globally, not just within one instruction file.

The native hazard mechanism is insufficient for a fused command as-is. It rejects SPD destination conflicts and performs coarse memory RAW/WAR/WAW checks only when two instructions have the same single `addrRangeID` (`b1c793ae:src/mem/MAA/IF.cc:187-221`). Decode assigns that one ID from the one base address (`b1c793ae:src/mem/MAA/CpuSidePort.cc:202-245`). A fused read-`A`/write-`C` command needs at least two range roles and overlap tests. Software-registered regions are intended to be disjoint (`b1c793ae:src/mem/MAA/MAA.cc:205-243`), but a new descriptor cannot rely on the old one-range classification.

### 3.5 Cases where the scratchpad cannot simply be bypassed

“Cannot” here means that deleting the SPD without providing equivalent storage/forwarding changes the interface or result:

- a later MAA instruction consumes the gathered tile as an index, condition, ALU operand, store/RMW operand, reduction input, or repeated/multiple consumer;
- CPU code reads the cacheable SPD pointer, as GZZ does;
- the transform is cross-element, order-dependent, a reduction/scan, or otherwise needs random/repeated access to earlier values;
- unresolved input/output aliasing requires the unfused materialize-then-write order;
- scatter/RMW duplicate destinations require atomicity/collision ordering rather than dense single assignment;
- precise exceptions, replay, rollback, or speculative cancellation require retained intermediate state;
- conditional false elements or partial destination lines cannot be represented safely by the available store interface; or
- a compatibility contract exposes destination tile readiness/data to software, even if the current kernel happens not to read it.

XRAGE's one-consumer identity chain is the clean native candidate. UME/CG's gather-multiply-RMW chains could fuse more deeply only if the final operation's collision/atomic semantics are included; bypassing just the first temporary while leaving a named downstream tile requires explicit forwarding. GZZ is not a candidate without changing its CPU-visible SPD interface.

## 4. True 4K physical state and exact 16K ordering

### 4.1 What actually scales at `b1c793ae`

The audited run configuration has 4 cores, 8 tiles/core, 4K elements, two memory channels, 32 initial RowTable slices, 64 rows/slice, and 8 entries/subslice-row. Evidence: `BFS-4K/config.ini:3448-3480`. The source defaults and exposed knobs are independent (`b1c793ae:src/mem/MAA/MAA.py:13-32`; `b1c793ae:configs/common/Options.py:218-238`).

| State per one MAA / indirect unit | Native 16K parameter | Native 4K parameter | True-4K requirement |
|---|---:|---:|---|
| SPD payload, 32 32-bit tile slots | 2 MiB | 512 KiB | 512 KiB; cacheable/noncacheable apertures are aliases, not two physical copies |
| SPD `element_finished` namespace | 32 x 16K | 32 x 4K | only 32 x 4K readiness bits/generations |
| OffsetTable entries | 16,384 | 4,096 | 4,096, with no logical-16K shadow table |
| Per-instruction exact unique word/CL/row sets | at most logical-tile work | at most 4K work | instrumentation only, or bounded/accounted hardware; never a free ordering store |
| Active default RowTable cacheline slots | 16,384 | **16,384** | at most the explicitly budgeted 4K-scale capacity unless extra area is declared |
| Active default RowTable row buckets | 32 x 64 = 2,048 | **2,048** | explicitly scaled/budgeted rather than inherited |

SPD payload follows directly from the single allocation at `b1c793ae:src/mem/MAA/SPD.cc:217-234`; the address apertures scale with `num_tiles * num_cores * num_tile_elements * 4` (`b1c793ae:configs/common/MAAConfig.py:81-101`). OffsetTable allocation follows `num_tile_elements` exactly (`b1c793ae:src/mem/MAA/IndirectAccess.cc:99,113-115`; `b1c793ae:src/mem/MAA/Tables.cc:123-139`). The per-instruction exact-address sets are declared at `b1c793ae:src/mem/MAA/IndirectAccess.hh:141-143`, populated at `b1c793ae:src/mem/MAA/IndirectAccess.cc:514-516`, and cleared only at completion (`b1c793ae:src/mem/MAA/IndirectAccess.cc:798-804`).

The hidden RowTable capacity is independent. For the DDR4 organization, total subslices are

```text
channels x ranks x bank-groups x banks = 2 x 1 x 4 x 4 = 32.
```

The DDR4 preset defines rank=1, bank-groups=4, banks=4 (`b1c793ae:ext/ramulator2/ramulator2/src/dram/impl/DDR4.cpp:10-23`); Ramulator multiplies channel organization by the system count (`b1c793ae:ext/ramulator2/ramulator2/src/memory_system/impl/generic_DRAM_system.cpp:159-175`). Native RowTable allocation computes these subslices and, for every configuration, allocates

```text
slices x 64 rows/slice x (8 x subslices/slice) cacheline entries
= 64 x 8 x 32
= 16,384 cacheline entries.
```

Evidence is `b1c793ae:src/mem/MAA/IndirectAccess.cc:116-163,182-210` and the nested allocations at `b1c793ae:src/mem/MAA/Tables.cc:193-213,305-335`. The non-reconfigurable default selects the 32-slice x 64-row x 8-column configuration, but the C++ model allocates all four alternatives: `(slices x columns) = (4 x 64), (8 x 32), (16 x 16), (32 x 8)`, each with 64 rows/slice and therefore 16K cacheline slots. Only one is active; allocating all four is a simulator implementation artifact, not a defensible claim that four physical copies are free.

Because a 4K instruction has only 4K OffsetTable entries, it cannot make more than 4K distinct source-cacheline records useful at once. The remaining active RowTable slots are overprovisioned physical state. A true 4K comparison must shrink or explicitly charge them—for example, keep the bank/row organization but reduce the per-subslice entry factor from 8 to 2, or choose another geometry whose total exact CL/provenance state meets the declared 4K budget. It must also bound request histories, routing entries, statistics-only address sets, and any combiner masks rather than treating C++ containers as zero-area hardware.

### 4.2 Information-theoretic lower bound

Suppose an implementation promises the exact same global source-request order that an unrestricted 16K sorter could choose for every possible 16K index vector, while retaining only a true 4K working set and doing one pass over `B`.

For arbitrary distinct source addresses, any of the `16,384!` permutations can be the row-sorted order. Distinguishing them requires at least

```text
log2(16,384!) = 205,747.21 bits = 25.12 KiB
```

merely to identify the permutation. That lower bound excludes the source addresses, duplicate/equality structure, word offsets, validity, and completion state. Raw 32-bit `B` itself contains 16,384 x 32 = 524,288 bits = 64 KiB. Routing returned values back to original positions also needs the equivalent permutation/provenance information; explicit 14-bit original-`i` tags alone are 28 KiB before word offsets and addresses.

The pigeonhole argument is direct: with fewer representable exact states than possible input-induced orders, two `B` sequences collapse to the same hardware state even though they require different later request/routing decisions. The engine cannot be correct for both unless information remains elsewhere or is recovered again.

Legal implementation choices are therefore:

1. **Backing/spill:** write descriptors or sorted runs to a bounded LLC/DRAM region and merge them; charge its bytes, traffic, ports, latency, and completion.
2. **External request storage:** enqueue all requests into another 16K-capable scheduler. That scheduler is the missing ordering storage and must be counted; it is not a 4K-only design.
3. **Reread/recompute:** make multiple passes over `B` for row/address buckets or regenerate indices, trading storage for deterministic extra reads and latency.
4. **Weaken the promise:** accept 4K-local/approximate ordering and measure the loss.

Four 4K sorted runs do not by themselves equal a 16K global order. An exact merge needs the runs stored somewhere (plus merge heads), or needs the input reread. Likewise, caching or directly retiring returned `A` values addresses payload storage **after** source requests; it cannot recreate the ordering information needed **before** those requests.

## Acceptance checklist for a proposed direct-sink true-4K implementation

- Source behavior is compared to `b1c793ae`, with exact file/line mapping for every changed lifetime.
- SPD, OffsetTable, RowTable, ready/generation bits, outstanding histories, combiner entries, and completion tokens have explicit finite entry/bit counts.
- No 16K host vector/map/set silently retains indices, addresses, rows, offsets, output masks, or packets.
- Reorder scope is stated as 4K-local, backed/spilled, reread, or externally stored; “exact 16K” names where its information lives.
- Identity, multiply, add, and affine transforms state precise datatype/rounding/overflow/FMA semantics and modeled throughput.
- Destination partial-line ownership, masks, backpressure, and visibility acknowledgement are modeled.
- Hazards cover both `A` read and `C` write ranges, the producer of `B`, scalar versions, CPU coherence, and cross-MAA traffic.
- Completion cannot precede the last required source response, ALU result, combiner flush, and chosen store visibility event.
- Validation uses correctness-complete matched cohorts, a tile-size x reorder x materialization factorial, and the existing row/coalescing/stage counters plus provenance-specific result-consumer counters.

## Bottom line

Native DX100 does not “keep `A[B[i]]` in a cache.” It converts each explicitly stored `B[i]` into temporary source-order metadata, issues/coalesces row-aware reads, and writes returned words into another explicitly named SPD tile. Larger tiles can improve that ordering/coalescing window and amortize commands; later value reuse exists only when the program names the result tile.

For truly terminal dense output, that destination tile is semantically removable. The replacement is not free: it is a finite transform/combiner/store path with multi-range hazards and a real completion token. For a true 4K design, removing payload storage does not authorize retaining hidden 16K Offset/Row/exact-address state. Exact 16K ordering must be paid for with explicit backing, an equally large external queue, or rereads; otherwise the honest contract is 4K-local ordering.
