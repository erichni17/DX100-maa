# Virtualization storage audit — 2026-08-10

The checked-in JSON is the machine-readable result. This audit is source-only and deliberately does not turn host `sizeof` values (which depend on ABI/padding) into hardware-byte claims.

## Result

The current transparent hybrid is **not** a 4K payload end-to-end. The runner fixes the logical instruction span at 16,384 elements and selects a 4,096-element physical SPD page. Because the tested data are FP64, one 4K page occupies two 32-bit SPD tile spans, or 32 KiB. The controller reserves distinct input and output pages, so one active descriptor uses 64 KiB of SPD staging (32 KiB input + 32 KiB output), not one 4K-value buffer.

The virtual producer also retires 16,384 `A[B[i]]` values to a 128 KiB coherent backing span. The transparent consumer reads that backing span before producing the final 128 KiB application destination. The destination allocation is required by the program and is not hidden virtualization hardware; the extra producer backing and the two physical SPD pages are the relevant virtualization state. “4K” therefore describes each physical staging page, not the complete live input/output payload.

Native16 uses 16,384 physical SPD storage words/tile (65,536 bytes/tile); native4 and transparent_hybrid4 use 4,096 storage words/tile (16,384 bytes/tile). SPD storage is explicitly `uint32_t`-addressed, so the FP64 tile ABI consumes paired visible tiles: a 4K-value FP64 page is 32 KiB, while a 16K-value FP64 page is 128 KiB. With the default eight 32-bit tile spans/core, the configured physical SPD data allocation is 524,288 bytes/core at native16 and 131,072 bytes/core at 4K. The hybrid descriptor simultaneously occupies half of that 4K-configured pool with its separate input/output page pairs. Metadata/status allocations exist too, but their exact host object layout is intentionally not an on-chip-area claim.

The virtual path has finite response slots, line combiners, a direct-index feeder, offset entries, and row tables. The full-line default response/combiner payload capacities are 512 B (8×64) and 1,024 B (16×64); the active virtual-tile runner instead configures 96 response slots, 480 pooled useful words, 384 combiner lines, 4,096 configured combiner words, and four feeder lines. These are private bounded state; producer backing, consumer destination, and descriptor-spool external segments are shared coherent/LLC-backed memory.

Fully bounded4 restricts the resident working set to one 4,096-descriptor pass. Three nonresident populations are represented in timing-visible backing as six-byte `(iteration,B[i])` descriptors, each segment rounded to 64 B. Its worst case is `3 * ceil64(4096 * 6) = 73,728 B`. It is bounded metadata, not a claim that the gathered FP64 output disappears: `A[B[i]]` is combined and retired to backing before the consumer can use it.

## Liveness

For normal direct virtual-index admission, `receiveDirectIndex` puts B words in `direct_index_words`; after successful row/offset insertion, `discardDirectIndex(... DescriptorInserted)` poisons then erases that private B copy. It is dead after admission. The bounded descriptor-spool path is the explicit exception: it retains B as a six-byte external descriptor until replay.

The gathered `A[B[i]]` is not dead: a source response drains into the virtual combiner, then `createRetirementWrite` targets `backingWordAddr(itr)`. The benchmark’s transparent consumer receives `backing` and uses that path before producing the application destination. Treating B as transient is supported; treating the gathered result as transient is not.

## Provenance

- `src/mem/MAA/SPD.cc` allocates payload from `physical_tile_elements * sizeof(uint32_t)`.
- `experiments/scripts/run_virtual_tile_consumer_case.sh` fixes `--maa_num_tile_elements=16384` while forwarding the case physical size.
- `src/mem/MAA/IndirectAccess.cc` owns B feeder discard, row/offset admission, virtual response/combiner, and backing retirement.
- `src/mem/MAA/BoundedDescriptorSpool.hh` declares four passes, 4K active descriptors, six-byte descriptors, four read lines, and sixteen write credits.
- `src/mem/MAA/LogicalSPDHiddenPayload.hh` declares a separate 32KiB/MAA logical-cache private payload; it is not silently charged to the virtual-index hybrid.
