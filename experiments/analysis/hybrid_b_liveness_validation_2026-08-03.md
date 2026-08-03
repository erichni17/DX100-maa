# Hybrid B-Index Liveness and Performance Validation

Date: August 3, 2026

## Verdict

Within one direct-index gather, `B[i]` is live only until the selected
iteration's RowTable/OffsetTable descriptor insertion succeeds.  The
descriptor thereafter contains the physical `A` cache-line address plus the
logical destination iteration and response word position needed to issue
`A[B[i]]` and place its response.  A failed insertion does not cross this
boundary and must retain the private feeder word for retry.

This is not the lifetime of architectural `B`.  Direct ingestion copies words
from the architectural memory stream into `direct_index_words`; later program
instructions can reread that memory independently.  The validation poisons
only a selected word in this private map after successful insertion and then
erases it.  Predicate- and partition-rejected words have no descriptor and are
discarded, without poison, after their terminal no-use decision.  No SPD tile
or architectural source memory is modified.

## Source proof

The direct path has the following exact dataflow:

1. `fillDirectIndexWindow()`/`receiveDirectIndex()` fetch architectural `B`
   cache lines and populate the private `direct_index_words[itr] = {value,
   line_addr}` map.  `checkElementReady()` calls `ensureDirectIndex(my_i)`, so
   the current private word exists before `fillRowTable()` examines it.
2. For a taken predicate, `fillRowTable()` reads the private value, computes
   `vaddr = my_base_addr + my_word_size * idx`, translates and cache-line
   aligns it, and computes `wid = (vaddr - block_vaddr) / my_word_size`.
3. `RowTableSlice::insert(grow_addr, block_paddr, my_i, wid, ...)` stores
   `block_paddr` in `RowTableEntry::Entry::addr`.  Its `first_itr`/`last_itr`
   point into an OffsetTable chain whose entries retain `itr`, `wid`, and
   `next_itr`.  Thus `addr` is sufficient to issue the `A` line request, while
   `itr` and `wid` are sufficient to select the returned word and place it at
   logical destination `C[itr]`.
4. Offset-epoch-full and RowTable-insert-failure branches both `break` before
   `discardDirectIndex()`.  The private `B[i]` therefore survives and the same
   iteration retries.  Only the successful `insert` branch sets
   `direct_index_descriptor_inserted`.
5. `discardDirectIndex()` fail-closes unless the private entry still exists
   with the expected value.  Exactly one terminal decision must hold:
   descriptor inserted, predicate rejected, or partition rejected.  Only the
   first case writes poison `0xd15ca4d` to the private map entry, reads it back
   fail-closed, traces that observed value, and then erases it.  The function
   contains no SPD write and has no architectural-memory pointer.

The ordinary tile-fed gather is separate: direct mode marks the index source
ready and uses the private stream; non-direct mode still reads `my_idx_tile`
from SPD.  Consequently this validation neither poisons the architectural B
tile nor says anything about the lifetime of a later instruction that reuses
that tile.

## Existing matched performance evidence

The raw August 2 matrix was independently requalified from each run's
`manifest.tsv`, `result.tsv`, first-ROI `stats.txt`, and trace.  The fair pair
uses the same direct-index producer, gem5 SHA-256
`978e746a074cd9f661bf355f4a043d3a86b43034c6a4a6cf3bdff369b488c0f2`,
workload SHA-256
`6c2e7ab2690e9ffda0f582b4f6c6ac42a65c91ffb66be5803160e88932046533`,
and exact output hash `7228541527853630339`.  Both checkpoint and restore
exited zero and each run has exactly one `m5_exit` marker.

| Case | simTicks | Delta from native direct 16K |
|---|---:|---:|
| Native direct 16K | 40,874,044 | reference |
| Transparent 16K reorder / 4K payload | 46,708,677 | +5,834,633 (+14.274665%) |
| Explicit overlapped 4K paging control | 46,897,416 | +6,023,372 (+14.736423%) |

The transparent controller is 188,739 ticks (0.402451%) faster than explicit
paging.  The large gap is therefore the producer/consumer transfer mechanism,
not transparent controller scheduling.

### Concrete attribution

The evidence rejects two tempting causes.  Both direct cases ingest exactly
16,384 B words in 1,025 cache-line reads with a feeder high-water mark of 53
and no index-filter cycles.  The hybrid also issues fewer A/source cache-line
requests (9,634 versus 9,858), builds fewer row descriptors (1,399 versus
1,458), and records fewer A memory accesses (10,659 versus 10,883) with
177,099 fewer summed A-memory latency ticks.  The 16K Row/Offset reorder is
preserved; neither B ingestion nor degraded A locality explains the slowdown.

The positive mechanism signature is:

- The hybrid retires 16K results through 5,303 backing writes: 301 full-line
  writes and 5,002 partial writes.  The partial writes cause 5,002 L3
  `ReadExReq_9` accesses, including 1,747 misses/RFOs; the four page reloads
  add 2,032 MAA `ReadReq_9` hits.  MAA memory read traffic rises by 97,472
  bytes (827,712 to 925,184).
- DRAM activity rises from 25,464 to 26,971 reads (+1,507), 4,752 to 5,562
  activates (+810), and 3,633 to 4,461 precharges (+828).  These are concrete
  transfer/locality costs of coherent backing plus reload.
- The indirect request interval rises from 107,657 to 114,198 cycles (+6,541).
  The hybrid interval classifies exactly as 113,814 source-flight cycles,
  383 final-drain cycles, and 1 other runnable cycle.  Relative to native,
  that is +6,157 source-flight, +383 final-drain, and +1 runnable cycle.  Its
  pipeline view is 106,608 overlapping source/write cycles, 7,206 source-only,
  383 write-only, and 1 idle; the overlap categories must not be added again
  to the request-interval delta.
- The four-page consumer executes seven additional MAA instructions (four
  loads and three stores), moves 16,384 additional stream words over 2,051
  additional stream cache lines, and adds 2,048 SPD-write cycles and 4,070
  stream request-table cycles.  The four ALU page actions total only 1,024
  compute cycles, so ALU arithmetic is not the dominant category.
- The controller trace spans 15,020,870 ticks from first page-fill issue to
  final retirement: 1,292,690 fill-action ticks, 641,024 compute-action ticks,
  7,610,282 store-action ticks, and a 5,476,874-tick gap between page 0 store
  completion and page 1 readiness.  Two pages become ready before source
  drain.  These intervals expose overlap and are not an additive decomposition
  of the end-to-end +5,834,633 simTick delta.

The supported conclusion is that the +14.27% is primarily the coherent
backing transfer plus four-page reload/consumer chain, expressed as partial
write RFO/DRAM work, extra stream movement, longer source-flight backpressure,
and a small final drain.  It is not host time, loss of 16K reorder, or private
B-feeder pressure.

## Validation commands and raw evidence

Static fail-closed checks:

```text
python3 -m unittest experiments.tests.test_direct_index_liveness_contract
bash -n experiments/scripts/run_virtual_tile_consumer_case.sh
git diff --check
```

The matched performance evidence is rooted at:

```text
/data1/nier/dx100-runs/2026-08-02-transparent-spd-premeeting
  native_direct_16k_matched/
  transparent_4k_retry1/
  paged_overlap_4k_matched/
  matched_matrix_summary.json
```

Fresh poison-validation runs use the task-local ignored build/evidence tree:

```text
build/hybrid-b-liveness-evidence-20260803/
  native_direct_16k_observed/
  transparent_4k_observed/
```

They use the same locally built gem5 SHA-256
`618ee2a13e08e5a96aecc16f2c7bef106b52433da78454ff7a7bb8d880f07de8`
and workload SHA-256
`221c6be8dee9869c014295b7e57e3562b060c0e6bf9951062e01b94d3fae770c`.
Both checkpoint and restore exit codes are zero, each restore has exactly one
`m5_exit`, both exact outputs hash to `7228541527853630339`, and neither log
contains a panic or fatal error.

| Fresh case | simTicks | inserted poison/discards | predicate rejects | partition rejects |
|---|---:|---:|---:|---:|
| Native direct 16K | 40,826,468 | 16,384 | 0 | 0 |
| Transparent 16K reorder / 4K payload | 46,932,159 | 16,384 | 0 | 0 |

The current-tree pair is +6,105,691 ticks, or +14.955227%.  It independently
confirms exact output with poison enabled and reproduces the same performance
regime, but does not replace the August 2 pair when attributing the measured
+14.274665% because the source revisions and binary hashes differ.  Both fresh
cases use one index partition; 4K refers to physical payload page capacity,
not four rescans of B.  Every selected B word therefore crosses the successful
descriptor-insertion boundary once, and there are no partition rejects.

The post-run exact trace audit requires
`poisoned=1 poison=0xd15ca4d reason=descriptor_inserted
private=direct_index_words` exactly 16,384 times in each trace, with zero
predicate/partition rejects.  Result/trace SHA-256 values are:

```text
native result  a8706941fd6e79be442610348ec2f7a045908fa67ab9ce7c333d3bab97b970fe
hybrid result  8b8926b09860cd353ca78ef5ff3210a2552c7bb0768393ae254aeb0522bbc3ba
native trace   273026b6cbe25536cd4bd60c692527da4168229b024da65fa034a048c2a6ff1e
hybrid trace   2d572802a785049d3cee805c810faf1432ef208d995909185e590d44b34aceca
pair JSON      6694d487954aa3c211df85d6356c2f05705a65a2cf685706166bc29c8432145e
pair Markdown  388c4ad6f6fb0d5518f46dfb017a5046f2783cac7b8deeaba180696705c721ae
```
