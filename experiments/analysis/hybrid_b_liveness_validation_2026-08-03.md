# Hybrid B-Index Liveness Validation

Date: August 3, 2026

## Accepted conclusion

Within one direct-index gather, `B[i]` is live only until the selected
iteration's RowTable/OffsetTable descriptor insertion succeeds. The descriptor
thereafter contains the physical `A` cache-line address plus the logical
destination iteration and response word position needed to issue `A[B[i]]` and
place its response. A failed insertion does not cross this boundary and must
retain the private feeder word for retry.

This is not the lifetime of architectural `B`. Direct ingestion copies words
from the architectural memory stream into `direct_index_words`; later program
instructions can reread that memory independently. The validation poisons only
a selected word in this private map after successful insertion and then erases
it. Predicate- and partition-rejected words have no descriptor and are
discarded, without poison, after their terminal no-use decision. No SPD tile or
architectural source memory is modified.

This source-liveness conclusion passed independent review. It is the only
evidence conclusion retained from this note.

## Source proof

The direct path has the following exact dataflow:

1. `fillDirectIndexWindow()`/`receiveDirectIndex()` fetch architectural `B`
   cache lines and populate the private `direct_index_words[itr] = {value,
   line_addr, word_paddr}` map. `checkElementReady()` calls
   `ensureDirectIndex(my_i)`, so
   the current private word exists before `fillRowTable()` examines it.
2. For a taken predicate, `fillRowTable()` reads the private value, computes
   `vaddr = my_base_addr + my_word_size * idx`, translates and cache-line
   aligns it, and computes `wid = (vaddr - block_vaddr) / my_word_size`.
3. `RowTableSlice::insert(grow_addr, block_paddr, my_i, wid, ...)` stores
   `block_paddr` in `RowTableEntry::Entry::addr`. Its `first_itr`/`last_itr`
   point into an OffsetTable chain whose entries retain `itr`, `wid`, and
   `next_itr`. Thus `addr` is sufficient to issue the `A` line request, while
   `itr` and `wid` are sufficient to select the returned word and place it at
   logical destination `C[itr]`.
4. Offset-epoch-full and RowTable-insert-failure branches both `break` before
   `discardDirectIndex()`. The private `B[i]` therefore survives and the same
   iteration retries. Only the successful `insert` branch sets
   `direct_index_descriptor_inserted`.
5. `discardDirectIndex()` fail-closes unless the private entry still exists
   with the expected value. Exactly one terminal decision must hold:
   descriptor inserted, predicate rejected, or partition rejected. Only the
   first case writes poison `0xd15ca4d` to the private map entry, reads it back
   fail-closed, traces that observed value, and then erases it. The function
   contains no SPD write and has no architectural-memory pointer.

The ordinary tile-fed gather is separate: direct mode marks the index source
ready and uses the private stream; non-direct mode still reads `my_idx_tile`
from SPD. Consequently this validation neither poisons the architectural B
tile nor says anything about the lifetime of a later instruction that reuses
that tile.

## Superseded performance material

The earlier performance sections in this file compared arms restored from
different checkpoints. They are retained only in Git history and must not be
used as matched-pair evidence. The later poison-enabled pair had the same
defect and is not promoted here, even though its exact output and completion
checks passed.

The superseding evidence is
`experiments/analysis/hybrid_overhead_attribution_2026-08-03.md` with the
machine-readable companion
`experiments/analysis/hybrid_overhead_attribution_2026-08-03.json`. That audit
requires one byte-identified shared checkpoint for both restores, one frozen
binary/config/input, exact output, strict versioned trace schemas, complete
event/counter reconciliation, and raw artifact hashes.

Unit discipline in the superseding audit is explicit:

- end-to-end simulator time and traced stage intervals are `simTicks`;
- `IND_Cycles*`, `STR_Cycles*`, `ALU_Cycles*`, and mutually exclusive MAA
  request categories are cycles;
- host wall time is never used as architecture performance;
- cycle views and simTick views are not added together.

## Source-validation commands

```text
python3 -m unittest experiments.tests.test_direct_index_liveness_contract
bash -n experiments/scripts/run_virtual_tile_consumer_case.sh
git diff --check
```
