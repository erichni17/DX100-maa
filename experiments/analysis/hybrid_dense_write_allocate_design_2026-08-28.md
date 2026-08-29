# Dense virtual-result write-allocation candidate (2026-08-28)

## Purpose

The matched hybrid produces 2,048 first-write L3 misses, one for every dense
backing cache line.  The candidate below removes the old-data fetch without
reintroducing a logical 16K result buffer.

This is a conditional design, not an accepted optimization.  Implement it
only if the backing-residency bracket shows material end-to-end headroom.

## Mechanism

For an operation that guarantees every logical destination word will be
written before completion:

1. Keep the existing 16K Row/Offset reorder window, 4K physical SPD, bounded
   feeder, response pool, and destination combiner.
2. On the first partial retirement of a backing cache line, construct one
   aligned 64-byte packet containing the currently valid words and arbitrary
   placeholders in the not-yet-valid bytes.
3. Send that packet as an unmasked full-line write.  gem5 promotes it to
   `WriteLineReq`, which obtains writable ownership without fetching old line
   data.
4. Record only the real word mask in the retirement scoreboard and page
   completion ledger.  Placeholder bytes never count as produced words.
5. After the exact ACK, mark the backing line initialized.  Later fragments
   use the existing masked-write path and should hit the writable LLC line.
6. Expose a page only after all real words have acknowledged, exactly as in
   the current mechanism.

The packet mask and semantic-completion mask must therefore become separate
fields.  Reusing the current `valid_words=0` convention for both would falsely
mark placeholder words complete.

## Legality boundary

Enable the mechanism only for an explicit dense/full-overwrite contract.  An
unpredicated direct-index gather has one result for every logical ordinal and
is a candidate.  Predicated, sparse, conditional-RMW, old-result-preserving,
or partially updated backing is not: placeholder bytes would destroy values
that remain semantically live.

The backing/token API already prevents a correct consumer from using a page
before page readiness.  The architecture must retain that ownership rule;
the optimization is not legal if another agent may read incomplete backing
as ordinary coherent data.

## Bounded state

One initialized bit per logical backing line is sufficient.  A pending first
write is already represented by the exact-address retirement scoreboard, so a
second pending bitmap is unnecessary.  For 16K FP64 results this is 2,048
bits, or 256 B per indirect unit (1 KiB across four units).  Reset is legal
only after all retirement ACKs close.  A future logical64K FP64 aperture would
require 1 KiB per unit.

No A-result payload is added beyond the existing combiner and write packet.
This does not recreate the native 16K SPD.

## Required gates

- First partial line emits an unmasked aligned 64-byte request but credits
  only its real semantic mask.
- A second fragment to the same line cannot pass the exact pending owner.
- Wrong address/generation/transaction ACKs fail closed.
- After the first ACK, subsequent fragments remain masked and exact.
- Page readiness cannot be reached through placeholder bytes.
- Sparse/predicated operations retain the existing read-for-ownership path.
- Full-line-first arrivals remain one normal full-line retirement.
- Operation reset rejects any pending write or incomplete dense line.
- Exact output, B/descriptors/A work, ACK counts, and terminal state pass.

## Performance decision

The current cold micro has 8,698 backing writes, 2,048 backing-region L3
misses, and 2,059 additional Ramulator reads relative to native16.  The
candidate should leave write transaction count approximately unchanged while
removing those first-write data fetches.  It is useful only if the charged
preallocation bracket materially lowers `simTicks`; otherwise the added
contract, bitmap, and cache behavior are not justified.

This mechanism improves dense output retirement.  It does not reduce A-source
DRAM work, replace the 4K physical SPD, or solve row-table virtualization.

## CPU-prewarm result

The separate `hybrid_backing_rfo_bracket_2026-08-28.md` experiment tried to
make each backing line writable with a CPU volatile read/write-self walk.  It
did not remove any of the 2,048 MAA-region misses; free prewarm improved only
0.11%, while charging the pass was 5.05x slower.  Reject CPU prewarming.

That result does not exercise this proposal.  It never changes the MAA's first
masked request into an aligned unmasked full-line write, and Ramulator reads
do not decrease.  The go/no-go gate for this design therefore remains a
default-off MAA first-write implementation or an equivalent cache-level
experiment with exact semantic-mask accounting.
