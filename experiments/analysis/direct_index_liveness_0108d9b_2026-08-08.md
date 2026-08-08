# Direct-index B-value liveness at 0108d9b

## Conclusion

For one admitted direct virtual-gather iteration, the private feeder copy of
`B[i]` is dead immediately after `RowTableSlice::insert(grow_addr,
block_paddr, i, wid, ...)` succeeds. The original architectural B array is not
dead: a later instruction or bounded-range pass may reread it. The destructive
experiment touches only `direct_index_words[i]`, the current ingestion copy.

An unsuccessful row insertion or an offset-epoch-full check exits the fill
loop before the discard, so the feeder value remains live for retry. A
predicate- or partition-rejected value produces no descriptor and is erased
only after that terminal no-use decision. Successful admission overwrites the
private value with `0xd15ca4d`, reads the poison back fail-closed, emits one
trace record, and erases the map entry.

## End-to-end dataflow and every later consumer

1. `receiveDirectIndex()` copies a B cache-line response into
   `direct_index_words[itr] = {value, line_addr, word_paddr}`.
   `fillRowTable()` is the only functional consumer of `value`. It computes
   the A word address, translates it, and decomposes it into a physical A
   cache-line address (`block_paddr`) and response word ID (`wid`).
2. `RowTableSlice::insert()` retains the DRAM grow/row scheduling identity and
   A cache-line address. `RowTableEntry::Entry` retains `addr`, `first_itr`,
   and `last_itr`. Its OffsetTable chain retains `itr`, `wid`, and `next_itr`.
   `next_itr` preserves per-line admission order; explicit `itr` preserves the
   architectural destination order independent of request/response reorder.
3. The Build-stage claim functions consume only the retained A line address
   plus OffsetTable head/count. `issueVirtualSource()` copies those fields into
   `VirtualSourceReservation` and issues `createReadPacket(source_addr, ...)`.
4. `recvData()` matches the returned A line by reservation address. It copies
   `dataptr + entry.wid * word_size` into the response slot and advances the
   retained OffsetTable chain. It does not consult B.
5. `drainVirtualResponses()` passes the returned word and retained `entry.itr`
   to `insertVirtualCombineWord()`, then consumes the OffsetTable entry.
6. `backingWordAddr(itr)` combines the retained logical iteration with the
   instruction-owned `my_backing_addr` and word size. The combiner and
   retirement writer use that address. Page counters retain completion state,
   not B or a substitute source index.

The admission-only `MAAPhysicalRecordTrace` reads `word_paddr` and the B value
to record provenance before discard; it is instrumentation, not a functional
consumer. Unique-address statistic sets likewise receive already-derived A
addresses. No downstream request, response, combiner, backing-write, or
transparent consumer path reads the admitted B value.

## Fresh exact-output evidence

The authoritative run is
`/tmp/b-index-liveness-20260808/transparent_4k_v3`. It restored a private copy
of the treatment-neutral checkpoint and passed the repository's fail-closed
`run_virtual_tile_consumer_case.sh` gate. The machine-readable companion to
this note preserves the full identities and hashes.

- Source: `0108d9b7a0c9f7818be75745aef3f8b72146c7d4`, clean at launch.
- gem5 SHA-256:
  `90858e29506525ea0ab4af88130c212ff8f4d3ae32842a7dfe322f8221ee9295`.
- Workload SHA-256:
  `f87d7206c91d5d48235da13a90ad08f2fb8c8f58ae25311758ebe4094ca6dfc5`.
- Checkpoint-files identity:
  `b2ce605f4f22273b9193a67dbc12370bc1f35080a084c13f395663131c92c4e3`.
- Resolved config SHA-256:
  `f8ee6be6b7cd24a2e12d5a09f15d60fab9d6f6e0100e0823b5784de420b4f226`.
- `simTicks`: `46889591`.
- Exact marker: `VIRTUAL_TILE_CONSUMER_RESULT mode=transparent
  page_elements=4096 hash=7228541527853630339 errors=0` (exactly once),
  followed by exactly one `ROI Ended` and the gem5 m5-exit marker.
- Destructive evidence: 16,384/16,384 descriptor-admitted words reported
  `poisoned=1 poison=0xd15ca4d`; predicate and partition discards were zero.
- Completion invariants: 5,019 retirement writes issued and completed, four
  pages ready, nonempty final stats, and `virtual_tile_consumer_case.pass`.

`simTicks` is reported as run identity, not as a speedup or performance claim.
The failed preparatory outputs `transparent_4k` (missing copied layout log) and
`transparent_4k_v2` (zero CLI defaults rejected by the explicit resolved
capacity check) are not evidence and are not referenced by the manifest.
