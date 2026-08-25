# CG single-pass destination-order audit (2026-08-25)

## Decision

**Do not add ordering hardware.** The current RowTable/OffsetTable plus
SoA/JIT path already preserves insertion/source order for every update to the
same FP32 destination word.  Its concurrency may schedule different
destination cache lines differently; that is irrelevant to a per-word FP32
sum.  The useful 16K selected set and the existing 4K physical SPD geometry
remain unchanged.

This is a source and focused-model audit, not a gem5 experiment.  The rejected
full CG run remains rejected; this result neither repairs it nor claims any
performance result.

## Proof obligation and result

For a selected logical source iteration `i`, let `D(i)` be its destination
word.  The required invariant is: if `i < j` and `D(i) == D(j)`, then the
FP32 ADD for `i` is performed before the FP32 ADD for `j`.  No global order is
required between different destination words.

The invariant holds at every relevant boundary:

| Boundary | Source evidence | Same-destination consequence |
|---|---|---|
| RowTable insertion | `Tables.cc`, `RowTableEntry::insert` appends with `offset_table->insert(itr, wid, entries[i].last_itr, pass)` and replaces `last_itr`. | Each destination **cache-line** chain retains source insertion order, including repeated words. |
| RowTable claim | `IndirectAccess.cc`, `serviceSoaJitBuild` calls `claim_entry_send`, initializes `nextOffset=head`, and panics if a live context already owns the same `aPaddr`. | Exactly one active context owns a destination cache line, hence also every word in it. |
| Lookahead/value responses | `serviceSoaJitLookahead` may fill/deliver aliases early, but selects a ready slot only when `slot.offset == context.nextOffset`. | Cache hits, misses, value-owner merges, and lookahead cannot apply a later same-line alias first. |
| Apply | The same routine calls `applySoaJitValue`, then `offset_table->consume_entry(context.nextOffset)` and verifies its offset, iteration, and word identity. | A chain advances only at its head; the FP32 destination is updated once per source entry in chain order. |
| Multiple contexts/cache lines | The apply-lane pool can rotate contexts, but the duplicate-`aPaddr` guard forbids two contexts for one line. | Different lines can interleave; one destination word cannot. |
| Row/Offset pressure epochs | `rememberSoaJitPressureRetry` ties the uninserted item to `my_i` and `soa_jit_next_source_ordinal`; `resetSoaJitEpochTables` requires empty contexts and Offset state; Request drains contexts before reset/refill. | All admitted aliases finish before the retried source ordinal is reconsidered. An epoch cannot move a later same-word update ahead of the pending one. |
| Completion/writeback | A context issues its A write only after `remaining == 0`, and its exact `WriteResp` frees it. | The cache-line update is not released/reused halfway through its chain. |

The focused test `experiments/tests/test_soa_jit_destination_order.py` models
order-sensitive FP32 collisions (`16777216, 1, -16777216, 1`) on two cache
lines plus an unrelated word.  It deliberately reverses context/line service
and forces three-entry Row/Offset epochs.  The only permitted model rule is
the source rule above—apply the chain head—and it produces the serial,
per-destination FP32 result and commits source ordinals exactly once.

## Hardware accounting and rejected diagnostic

No new ordering hardware is added: **0 B incremental state**, 0 new ports,
and 0 new timing paths.  The retained configuration is one 16,384-element
logical selected set with `MAA_CONSUMER_TILE_SIZE=4096`; the CG physical-page
producer keeps four 4K FP32 pages (4 × 4096 × 4 B = 65,536 B of produced
payload) and the existing eight guest tiles/core configuration.  This audit
does not reinterpret coherent producer backing as hidden MAA storage.

Four masked 4K passes are **not a valid successor**.  They serialize the page
sets rather than enforcing only colliding-destination order, leave only 4K
useful admissions per pass, add three scans' worth of rejected predicates, and
discard the single useful 16K Row/Offset selection that this audit preserves.

## Next falsifiable mismatch boundary

Because the same-destination ordering proof closes, the next plausible source
is the physical product handoff—not Row/Offset scheduling.  In
`benchmarks/NAS/cg/cg.cpp`, each 4K page computes `t7` with
`maa_alu_vector<float>(t4, t5, t7, MUL_OP)`, waits for it, then
`cg_publish_index_product_page` response-publishes `t7` into coherent
`cg_soa_products`.  The later one-pass SoA/JIT descriptor rereads
`cg_soa_products`.

The full-run ledger proves the four page publications complete and its counts
close, but it does not prove a per-element **bitwise** identity between the
legacy page-local `t7` operand entering ordinary RMW and the published
`cg_soa_products` word entering SoA/JIT.  That is an unproven boundary, not a
claim of corruption.  The smallest next diagnostic is a focused, no-full-gem5
microcase that records/compares the four page-local product words and the
coherent published words by `(window, page, logical offset)` before issuing
the RMW; it must also compare the final per-destination FP32 bits against the
ordinary four-page reference.  It should retain one 16K selected descriptor,
the four 4K physical producer pages, exact source identity, and no masked
passes.

If that bridge is bit-identical, the next audit target is the native ordinary
RMW versus SoA/JIT FP32 execution/visibility contract (including when the
legacy page-local `wait_ready(t7)` makes each page's update visible), again
with a small exact bitwise model before any full CG retry.
