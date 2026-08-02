# Baseline DX100 reorder and storage audit

## Scope and source boundary

This audit covers baseline DX100 only. Source references use baseline revision
a40792a (2025-03-26), before the later virtual-tile series; they are written as
path@a40792a:line. The current worktree has substantial later virtual changes,
so it is not used to infer baseline storage.

Paper source: Alireza Khadem et al., *DX100: A Programmable Data Access
Accelerator for Indirection*, ISCA '25,
[DOI 10.1145/3695053.3731015](https://doi.org/10.1145/3695053.3731015).
Local camera-ready:
/data1/nier/worktrees/openevolve-postsearch-validation-planner-review-20260717-e19ef59/references/papers/dx100.pdf;
SHA-256 ec18bdc585f32e3da5c0fd467e686dd2137b3db88d4c327d510509213e7c44a3.

“Paper area” means synthesized 28 nm area reported by the paper. “Simulator
ledger” means a C++ allocation or type-level formula, not a physical-area model.

## Direct answers

| Question | Baseline answer and evidence |
| --- | --- |
| What does 16K tile mean? | 16,384 **elements**, not 16 KiB: num_tile_elements=16384 (src/mem/MAA/MAA.py@a40792a:13-15). The paper evaluates “16K 4B words” (p. 3 §3; p. 8 Table 3), so a 32-bit tile is 64 KiB. Baseline 64-bit data spans paired 4-byte tile storage (src/mem/MAA/SPD.hh@a40792a:47-70). |
| Are B and gathered A separate? | Yes. Decode makes src1SpdID the index tile and dst1SpdID the gather destination (src/mem/MAA/IndirectAccess.cc@a40792a:543-549). Dispatch asserts a destination differs from both sources (src/mem/MAA/MAA.cc@a40792a:563-567). They therefore occupy separate SPD backing regions during the gather. |
| Is there reuse? | Not in-place for one INDIR_LD. A tile ID can only be reused by software after its producer and consumers have completed. Completion marks the destination finished and releases dependency readiness (src/mem/MAA/MAA.cc@a40792a:605-621). |
| What metadata scales with tile size? | SPD payload and per-element completion tracking scale with num_tiles × num_tile_elements (src/mem/MAA/SPD.cc@a40792a:214-231). Per indirect unit, the Offset/Word Table allocates one OffsetTableEntry and valid bit per tile element (src/mem/MAA/Tables.cc@a40792a:139-154). |
| Are Row Table entries payload storage? | No. They contain cache-line address and first/last iteration metadata, plus valid state (src/mem/MAA/Tables.hh@a40792a:79-119). The response's transient 64-byte buffer supplies the data, which is written into SPD (src/mem/MAA/IndirectAccess.cc@a40792a:898-917). |
| Which storage dominates hardware cost? | The paper reports Scratchpad = 3.566 mm² of 4.061 mm² total (87.8%) and Indirect Access = 0.323 mm² (p. 11, Table 4). Scratchpad—not Row/Word Table metadata—is the hardware-area dominant storage. |

## Trace: one A[B[i]] indirect load

1. **Source index tile.** A prior Stream Load puts B[i] into an SPD destination
   tile (src/mem/MAA/StreamAccess.cc@a40792a:394-402). The SPD addressing
   expression includes the tile ID and num_tile_elements
   (src/mem/MAA/SPD.hh@a40792a:56-70). The indirect decoder binds this tile to
   my_idx_tile and a different tile to my_dst_tile
   (src/mem/MAA/IndirectAccess.cc@a40792a:543-561).

2. **Fill Row/Offset metadata.** For a ready element, the unit reads
   SPD[idx_tile][i], computes base + word_size × idx, translates/maps its cache
   line to DRAM coordinates, then inserts (row grouping, cache line, i, word
   offset) into a Row Table slice (src/mem/MAA/IndirectAccess.cc@a40792a:460-508).
   The Offset Table records wid and next_itr at entry itr
   (src/mem/MAA/Tables.cc@a40792a:156-184). B[i] is consumed from SPD; it is
   not copied into the Row Table.

3. **Reordering and its limit.** An insert first matches an unsent
   row+cache-line, then an unsent row, then a free row. If no row is free it
   returns failure and triggers a drain (src/mem/MAA/Tables.cc@a40792a:353-399).
   Repeated accesses to one cache line chain their iterations through the
   Offset Table (src/mem/MAA/Tables.cc@a40792a:238-266). Build walks valid
   columns of a chosen row before marking it sent
   (src/mem/MAA/Tables.cc@a40792a:449-465); the Indirect unit traverses its
   precomputed slice order and sends reads
   (src/mem/MAA/IndirectAccess.cc@a40792a:695-721).

   Hence 16K is the intended tile-scale opportunity, **not a promise that all
   16K requests reside simultaneously in the Row Table**. A capacity stop
   causes Build/Request to drain a subset and Request returns to Fill
   (src/mem/MAA/IndirectAccess.cc@a40792a:634-664, 742-763). Actual reorder
   scope is outstanding unsent row/column metadata, bounded by slices, rows,
   columns, and response progress. This matches paper §3.2/Fig. 4 (pp. 4-5):
   same-row columns issue together and slices are interleaved for
   channel/bank-group utilization.

4. **Response to destination tile.** A returned line is remapped to the Row
   Table and yields the linked Offset Table entries
   (src/mem/MAA/IndirectAccess.cc@a40792a:868-905). For every (itr,wid),
   INDIR_LD writes the selected word to SPD[dst_tile][itr]
   (src/mem/MAA/IndirectAccess.cc@a40792a:909-923). After all expected
   responses, Response calls finishInstructionCompute
   (src/mem/MAA/IndirectAccess.cc@a40792a:742-755, 766-805), which marks the
   destination tile Finished and notifies dependent instructions
   (src/mem/MAA/MAA.cc@a40792a:605-621; src/mem/MAA/IF.cc@a40792a:302-335).

5. **Later consumer/store.** A Stream Store takes its source from src1SpdID
   (src/mem/MAA/StreamAccess.cc@a40792a:137-152), waits for that tile to be
   Finished (src/mem/MAA/StreamAccess.cc@a40792a:306-315), reads
   SPD[src_tile][itr] into a response buffer, and sends a writeback
   (src/mem/MAA/StreamAccess.cc@a40792a:405-437). Thus it consumes the
   gathered destination SPD tile directly. No Row Table entry retains
   A[B[i]] as consumer-visible payload.

## Storage accounting and the area boundary

### Simulator byte ledgers

For the paper configuration, 32 tiles × 16,384 4-byte elements gives exactly
2,097,152 bytes (2 MiB) of **SPD payload allocation**:
32 * 16384 * sizeof(uint32_t). This is the allocation expression in
src/mem/MAA/SPD.cc@a40792a:214 and agrees with the paper's 2 MB Scratchpad
configuration (p. 8, Table 3). The simulator additionally allocates one bool
completion entry per element (src/mem/MAA/SPD.cc@a40792a:225-227): 524,288 bool
elements here. That is simulator bookkeeping, not evidence of a 512 KiB
physical bitmap.

The tile-length-scaled reorder metadata is exact at source level:

    num_tile_elements * (sizeof(OffsetTableEntry) + sizeof(bool))

per indirect unit. OffsetTableEntry is three int fields (itr, wid, next_itr)
(src/mem/MAA/Tables.hh@a40792a:45-49), separately allocated with its validity
array (src/mem/MAA/Tables.cc@a40792a:139-154). At 16K this is 16,384 entries.
Leaving it as sizeof is intentional: host object layout, allocator overhead,
and C++ bool representation are not a hardware SRAM/BCAM area result.

Row Table capacity is not directly multiplied by num_tile_elements. Its
defaults are 64 rows and 8 entries per sub-slice row
(src/mem/MAA/MAA.py@a40792a:17-25), and construction uses slice/row/column
capacity parameters (src/mem/MAA/IndirectAccess.cc@a40792a:142-164). Its
entries hold address/iteration/valid/sent information, not gathered words.
A longer tile can require more fill/drain epochs, but does not itself resize
those Row Table arrays in baseline source.

### Synthesized paper area

The paper states that DX100 RTL components were synthesized with a 28 nm TSMC
library and Row Table BCAM area/power was evaluated using 28 nm FDSOI data
(p. 8, §5). Its Table 4 (p. 11) is:

| Synthesized module | Area (mm²) | Power (mW) |
| --- | ---: | ---: |
| Scratchpad | 3.566 | 577.03 |
| Indirect Access | 0.323 | 83.70 |
| Total DX100 | 4.061 | 777.17 |

The paper explicitly says the 32 × 16K-element Scratchpad dominates (p. 11,
§6.5). Do not convert C++ new[] expressions into mm²: the raw simulator
ledger explains scaling and lifetime, while the paper establishes hardware
area.

## Conclusion

Baseline DX100 keeps B and gathered A[B[i]] in distinct tiles during an
indirect load; gathered values remain in the destination tile for a later
consumer/store. The Offset/Word Table scales with tile elements and maps
reordered responses back to original (i, word offset). The Row Table is
address/placement metadata and bounds the live reorder scope; it is not
payload storage. The correct physical-cost conclusion comes from the paper:
the 2 MB multi-tile Scratchpad dominates DX100 area, not Row/Word Table
metadata.
