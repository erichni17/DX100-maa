# XRAGE FP64 fused direct transform, 2026-08-08

## Scope and conclusion

This experiment starts from exact commit `93159cf9aadaf1cd657a38249e280093b469b02f` and tests only `C[i] = A[B[i]] * scalar` for FP64.  It adds opcode `INDIR_LD_VIRTUAL_SCALAR` without changing any existing opcode or API.  The implementation is a real timed path: gathered response words occupy the existing shared MAA ALU, wait its configured lane latency, remain in a finite lane-sized result buffer under downstream backpressure, enter the existing line combiner, and complete only after response-bearing direct writes are acknowledged.

On the matched 20,000-element XRAGE input, native gather-to-SPD plus scalar ALU plus stream-store took 22,548,520 ROI ticks.  Fused gather-to-ALU-to-direct-store took 20,988,841 ticks with the identical scale-3 hash, removing 1,559,679 ticks (6.916991%).  Both arms reproduced exactly.  This is promising small-scale evidence, not a broad workload promotion.

## Legality contract

The fused opcode is accepted only when all of the following are true:

- The operation is unpredicated FP64 `MUL` with one scalar register.
- The destination is implicit and unique: iteration `i` produces exactly one write to `C[i]`; the gathered value has no SPD-visible or second consumer.
- A and C resolve to registered, explicitly disjoint half-open address ranges.  Different region IDs alone are insufficient; interval overlap is checked too.  The opcode fails closed on missing, same, or overlapping regions.
- The complete B/index tile is finished before the indirect instruction may start.  Therefore all B memory reads precede every direct C write, making B/C aliasing safe for this narrow path.
- Instruction issue checks A/C read-write, C/A write-read, and C/C write-write region hazards against intervening MAA instructions.  Software must also observe the completion token before CPU access to C.

Without the A/C disjointness proof (or a future all-A-reads-before-C-writes mode), early direct writes are illegal: they can corrupt a later gather source read.  Native gather-to-SPD followed by stream-store does not have that problem because C writes begin only after the gather tile is complete.

## Timing and backpressure model

The indirect unit forms batches no larger than `num_ALU_lanes`.  It must atomically claim the same ALU idle scoreboard used by normal ALU instructions.  A batch charges `ceil(words / lanes) * ALU_lane_latency` to the existing per-ALU compute counter and becomes readable only at the scheduled ALU event.  FP64 multiplication occurs at that event; there is no functional callback, host post-wait loop, or zero-time CPU operation.

Ready results remain owned by the ALU until the existing combine-bank and combiner-capacity checks accept each word.  This deliberately holds the shared ALU busy under cache/memory backpressure.  The existing combiner then emits full or masked line writes subject to its finite occupancy and 64-write credit limit.  The completion-only destination token is not released until the response pool, ALU result batch, combiner, and outstanding write-ACK count are all empty.

For the fused XRAGE run, the modeled ALU counters report 2 fused instructions, 5,019 timed ALU batches/cycles, 20,000 lane words, 1,651 result/ALU wait cycles, and a summed per-instruction result high-water of 8 words.  Direct retirement issued and acknowledged 3,620 writes.  The native arm reports 2 ordinary scalar ALU instructions and 14,339 scalar-ALU cycles.

## Storage accounting

The only new bounded data storage is one result batch per MAA ALU.  With 16 lanes, the implementation reserves 16 entries of 8-byte data plus a 4-byte destination-iteration tag: 192 bytes.  Its owner pointer, cursor, scalar, two state bits, and vector control add about 56 bytes in the C++ model (about 248 bytes total per ALU, allocator bookkeeping excluded).  No result scratchpad tile is allocated.

The path reuses, rather than duplicates, the already modeled direct-store storage in this configuration: 128 response descriptors, a collectively capped 480-word packed response pool (3,840 data bytes), 384 line-combiner entries (24,576 data bytes plus address/mask/valid metadata), a 4,096-word occupancy ceiling, and 64 outstanding write credits.  The fused opcode adds no unbounded queue.

## Matched evidence

All arms use the same production `gem5.opt`, XRAGE guest binary, fixed `xrage_gather0_20k.json`, 16K logical/physical tile geometry, cache/memory configuration, and exact post-ROI verifier.  `simTicks` is the first statistics block.  Final stats contain two blocks and every accepted run ends via `m5_exit`.

| Arm | Work | Replica | ROI simTicks | Output hash | Terminal/correct |
|---|---|---:|---:|---:|---|
| native16x3 | gather + modeled scalar ALU + stream-store | 1 | 22,548,520 | 16942094529479519491 | yes |
| native16x3 | gather + modeled scalar ALU + stream-store | 2 | 22,548,520 | 16942094529479519491 | yes |
| fuseddirect16x3 | gather + modeled shared ALU + direct store | 1 | 20,988,841 | 16942094529479519491 | yes |
| fuseddirect16x3 | gather + modeled shared ALU + direct store | 2 | 20,988,841 | 16942094529479519491 | yes |
| compact16 control | untransformed direct gather/store, scale 1 | 1 | 21,139,081 | 10990373302566333699 | yes |

Only the two scale-3 rows are used for the speedup.  The scale-1 control deliberately performs different work and is not a speedup denominator.  The older `compact16x3` arm remains a legacy CPU post-wait multiply and is explicitly excluded.

Cycles are removed by eliminating the intermediate result-tile writes/reads and the native stream-store instruction/path.  Cycles are added by waiting for the full index tile, occupying the shared ALU for 5,019 timed batches, retaining blocked ALU results, combining lines, and waiting for 3,620 direct-write ACKs.  The measured net is the 1,559,679-tick reduction above.

## Validation and preserved integration failure

- Six focused source-contract tests pass.
- The FP64 API test and the XRAGE configuration translation unit compile; the complete runtime XRAGE guest binary links.
- A full production `scons build/X86/gem5.opt -j16` compiles all changed MAA translation units and links.
- The 4,097-element runtime contract reports `errors=0`, hash `5894740462575425604`, then the 257-element A/C-alias case fails at the intended non-alias panic with exit 134.
- The first contract-run attempt was preserved.  It stopped before issuing MAA work because the new benchmark printed `FUSED_DIRECT_TRANSFORM_LAYOUT`, while the established validator requires `VIRTUAL_GATHER(64)?_LAYOUT`: `verifier memory map does not match gem5 --mem-size=2GB; layout markers: <none>`.  Renaming only that provenance marker made the same runtime contract pass.
- The first production build attempt was also preserved: the isolated worktree lacked Ramulator2's nested headers and failed at `ext/ramulator2/ramulator2/src/base/base.h:11:10: fatal error: spdlog/spdlog.h: No such file or directory`.  Hydrating the exact gitlink revisions and matching prebuilt `libramulator.so` from the exact-base sibling allowed the full build to complete without tracked-source changes.

Sanitizer execution is not applicable to this SE-mode guest path: the guest contains MAA MMIO/m5 instructions and cannot execute natively, while a sanitized production gem5 would require a separate full build outside this timebox.  Compile warnings were pre-existing (`volatile` return qualifier in the API and gem5's large-constructor debug-info fallback).
