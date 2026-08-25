# Full CG hybrid overhead profile (2026-08-25)

## Decision

The current physical-page-product hybrid is correct against native16 but not a
performance candidate. First-window latency is `818,687,246,165 simTicks`
versus frozen native16 `58,928,150,676`, a `13.8929736768x` slowdown.

This profile compares existing immutable stats only. No native or candidate
simulation was rerun.

## Measured expansion

| Metric | Hybrid | Native16 | Ratio |
|---|---:|---:|---:|
| MAA total cycles | 2,615,614,205 | 188,268,852 | 13.893x |
| MAA busy cycles | 2,587,782,729 | 162,466,810 | 15.928x |
| indirect-read cycles | 1,171,023,551 | 121,160,218 | 9.665x |
| indirect-RMW cycles | 2,541,215,422 | 27,703,543 | 91.729x |
| indirect fill cycles | 1,106,051,935 | 53,408,582 | 20.709x |
| indirect request cycles | 2,606,187,038 | 95,455,179 | 27.303x |
| cache read packets | 299,101,442 | 107,940,377 | 2.771x |
| cache write packets | 202,072,565 | 21,599 | 9,356x |
| stream instructions | 176,420 | 22,190 | 7.950x |

The hybrid executes 10,960 SoA/JIT operations and applies 179,568,640 aliases.
Its index/product publisher issues, accepts, and completes 22,446,080 cache-line
writes, with 21,744,631 publish-credit stalls. Native16 has no equivalent
publication path.

The accepted bitwise microprobe at
`2026-08-25-cg-product-handoff-55c9ab71-r1` rules out corrupted product
publication and same-destination update order. This overhead is real data
movement and replay, not a hidden correctness failure.

## Redundant work

For every full 16K window, the current guest:

1. creates four physical 4K index/product pages;
2. response-publishes four index and four product pages to coherent backing;
3. starts a separate 16K SoA/JIT instruction;
4. rereads the published index array to rebuild Row/Offset metadata;
5. rereads product values while applying the RMW.

The destination indices are dead after RowTable/Offset insertion. Publishing
and rereading them recreates information the producer already held. Across the
full run that accounts for half of the 22,446,080 publisher line writes plus a
second descriptor-fill pass.

## Selected next architecture

Add a bounded page-fed SoA/JIT admission mode:

- open one logical 16K RMW context with the existing 16K Row/Offset capacity;
- for each physical 4K page, feed its completed index tile directly into that
  context and assign logical ordinals `page*4096 + lane`;
- retain the resulting destination line/word and source ordinal only in the
  existing Row/Offset structures; discard the index tile immediately;
- response-publish only the 4K product page into coherent value backing;
- after four pages, close admission and execute the single useful 16K
  RowTable schedule, fetching product values by retained logical ordinal;
- overlap each product publication with admission/production of the next page
  where existing ports permit it.

This does not add a 16K payload tile or hidden descriptor array. Persistent
new state should be one bounded operation/page cursor, generation, admitted
count, and closure bits; exact bytes must be reported by the implementation.
The immediate expected effect is removal of four index publications/window,
11,223,040 full-run line writes, and the coherent index reread/build pass.

This is not yet a performance claim. A focused vertical slice must first prove
exact 16K admission, source ordinals, Row/Offset closure, zero index backing,
four product page responses, exact output, and bounded state. Only then should
small CG and eventually full CG be rerun.
