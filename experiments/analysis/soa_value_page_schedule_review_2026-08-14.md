# SoA/JIT value-page schedule review (2026-08-14)

## Decision

Do **not** replace the present global A-row schedule with a value-page replay
as the next performance implementation. A four-page replay is legal with a
small, explicit state extension, and it has a compelling value-transaction
bound. But the frozen trace shows that it gives up a substantial part of the
current one-read/one-write A-line locality. First measure it with a bounded
functional/trace microcase and the counters below; promote it to a GZP run
only if that microcase demonstrates the predicted transaction exchange and
bit-exact duplicate order. The more conservative next hardware experiment is
a bounded sequential internal value prefetch while retaining global A-row
order, although existing evidence makes that a diagnostic rather than a
favoured optimization.

This is a source-and-frozen-artifact review only. No simulator was launched,
no trace was copied, and no core source was changed.

## Evidence and scope

The reviewed lead is `a4fcc5f0ba7e3a843749b8a17f463aa2ff6ac44f`. The exact
root is
`/data1/nier/dx100-runs/2026-08-14-gzp-soa-jit-optimized-prepublisher-fbec9dbe-r1`.
Both restored arms have wrapper success, `m5_exit`, the same exact FP output
hash (`11225737641199706160`), zero non-finite values, and zero reference
errors over 1,180,000 elements. Thus the comparison below is a valid
same-checkpoint, correct performance observation, not host-time evidence.

| Arm | `simTicks` | `cycles_TOTAL` | Relevant RMW instructions |
|---|---:|---:|---:|
| current hybrid (`legacy_4k`) | 7,520,117,655 | 24,025,935 | 490 |
| volume-only SoA/JIT | 9,557,940,150 | 30,536,550 | 307 |

The volume-only arm is 27.098% slower despite removing 183 ordinary RMWs. It
closes 61 SoA/JIT generations with exactly 949,411 selected aliases, 875,918
value-read issue/response pairs, and 509,830 A-read/A-write issue/response
pairs. Its 32-owner value pool records 51,312 hits, 22,181 merged waiters,
873,966 evictions, zero owner stalls, and 10,244,167 context-scoreboard stalls.
Those counters identify repeated timed value access and A-context turnover, not
an unclosed protocol, as the opportunity.

The active configuration is bounded: logical Row/Offset capacity 16,384,
physical SPD capacity 4,096, eight A contexts, eight ordered lookahead slots,
16 predicate credits, and 32 active value owners. `IndirectAccess.cc` builds
all Row/Offset entries before claiming A lines, services each claimed A line
through its Offset chain in `nextOffset` order, and retains the modified 64-B A
line through its authenticated `WriteResp`. `SoaJitContext` is a fixed
at-most-512-B state object; it is deliberately not a hidden 16K value payload.

## What a legal value-page replay would be

For one 16K logical window, keep the completed Row/Offset metadata and
partition the immutable FP32 `value[i]` backing array by logical `i`:

1. Fill physical SPD with logical values `[0,4096)`, sequentially (256 cache
   lines).
2. Traverse A rows. For each row, traverse its existing Offset chain but apply
   only entries whose `logical_i >> 12` is page 0, in increasing Offset order.
   Read and write the A line only if that filtered chain is nonempty.
3. Drain every A `WriteResp`; repeat for pages 1, 2, and 3. The final token
   still waits for all pages and all A writes.

The `logical_i` field already exists in an Offset entry, so the page test is
bounded control, not another 16K payload. A one-bit/page-presence summary per
Row/Offset chain is optional (four bits per chain); it may accelerate an empty
pass but is not required for legality. The physical value payload is one
16-KiB FP32 page plus tags/validity. A 4K ping-pong variant needs two such pages
(32 KiB) to overlap fill and apply. No design may retain the complete 16K value
array (64 KiB/window), materialize the 16K logical SPD payload, or relax the
registered range/WriteResp protocol.

### FP order is non-negotiable

For a particular A word, the current Offset chain is increasing logical
insertion order. Processing pages in ascending page number and entries in their
original chain order preserves that exact order, including a non-associative
FP32 sequence such as `16777216, 1, -16777216, 1`. Rows may interleave only
when their A addresses differ and existing region permits allow it. It is
illegal to sort aliases by value address, process pages in a different order,
write an A line before the last selected chain occurrence for the current page,
or use a cache hit to hide an A/value overlap.

This is a different ordering from the present strict global A-row traversal:
the latter reads each A line once and accepts random value accesses; replay
visits rows once per nonempty logical-value page. Holding all dirty A lines
until the fourth page to avoid revisits would require a payload proportional to
the number of selected rows (up to 509,830 * 64 B in this run), so it is
explicitly outside the bounded design.

## Quantified transaction and locality exchange

For the 61 full volume windows, a sequential four-page fill has
61 * 1,024 = 62,464 64-B value-line fills, or 3,997,696 B. The observed demand
path issued 875,918 64-B value reads, or 56,058,752 B. Ignoring
cache/coherence effects, that is a 14.023x issue reduction (92.87%). It is a
transaction bound, not a speedup prediction: fill traffic, SPD writes, cache
ports, and descriptor replay still cost time.

The corresponding A cost is adverse. I sampled the frozen trace without
exporting it by reconstructing each live context from its `head` Offset and
its `soa_jit_value_request logical_itr` events, then counting distinct
`logical_itr >> 12` pages before the matching A `WriteResp`. The first 15
terminal generations (125,449 A lines; 196,014 page-visits) show:

| Distinct 4K value pages in an A chain | A lines | Share |
|---:|---:|---:|
| 1 | 69,693 | 55.55% |
| 2 | 42,254 | 33.68% |
| 3 | 12,195 | 9.72% |
| 4 | 1,307 | 1.04% |

That trace sample requires 1.5624995 A page-visits per currently single A
line: 70,565 extra visits, or 56.25% more A read/write pairs. If the sample
were representative (not an asserted full-run measurement), the 509,830
current A pairs would become about 796,609 pairs: about 18.35 MB more 64-B
reads and the same additional write payload. The legal bounds for the full run
remain 509,830 to 2,039,320 visits. A candidate must report the exact full-run
value rather than use this extrapolation.

The descriptor side also makes four complete row/Offset passes rather than one.
Its storage is reused, but its SRAM read ports, cursor/chain traversal, and
arbitration are not free. The 4K physical SPD saves payload capacity relative
to a 16K value tile; it does not make the four scans or repeated A transactions
disappear.

## Ping-pong assessment

A 4K+4K ping-pong is legal if—and only if—the two value pages have independent
fill/apply storage and ports: while page *p* is applied from one 16-KiB bank,
the sequential backing fill of page *p+1* may target the other. It adds 32 KiB
of FP32 payload per active indirect unit, plus two page tags, generation,
valid/ready state, a fill cursor, and port/arbitration accounting. It cannot
overlap an A update with another update to that A address, and it may not
overwrite a page before every consumer context has released it.

A 2K+2K split uses only 16 KiB of payload (two 8-KiB halves, 128 lines each)
and could likewise overlap fill with apply only with independently banked
halves/ports. It doubles page passes from four to eight while the sequential
value traffic remains 1,024 lines/window. Since 44.45% of sampled A chains
already span multiple *4K* pages, finer pages can only preserve or increase A
revisits. It is therefore a second-tier experiment, not a capacity win. A
single-port SPD or shared fill/apply bank invalidates the claimed overlap;
measure overlap cycles and port conflicts rather than assuming it.

## Simpler alternatives

1. **Internal sequential LLC prefetch, retain global A order — diagnostic.**
   This preserves exactly one A read/write per row and needs only a bounded
   prefetch-credit queue (the existing coalescer already has eight payload-free
   credits), not a value page. It cannot reduce the number of value aliases; it
   can only move their latency. The earlier CPU value-warm diagnostic found
   27,641/29,689 (93.1%) MAA LLC hits and was slower after moving the first
   2,048 fills to CPU warm loads. That rejects CPU warming as a hardware proxy
   and makes an internal prefetch a falsifiable small experiment, not an
   expected win.
2. **Cross-generation value retention — reject for GZP.** Consecutive full
   windows use disjoint `corner_volume + c` ranges, so a 32-line owner cache
   has no inherent next-generation reuse to capture. Within a generation it
   already evicts 873,966 lines for only 51,312 hits. Increasing it toward a
   16K payload would hide the very storage forbidden by this review; retaining
   normal LLC state is already part of the measured system and should not be
   rebranded as a new accelerator store.
3. **Global A-row order with the current bounded demand cache — retain as the
   baseline.** It has poor random value transaction count but preserves the
   one-pass A locality and the simplest duplicate-order proof.

## Ranked next experiments and reject gates

1. **First: four-page replay functional/trace microcase, no full GZP.** Use
   deliberately cross-page duplicate FP32 chains and independent A lines.
   Require bitwise output equivalence; each chain's applied logical-i sequence
   must equal the baseline; value-page fills must be exactly 1,024 per full
   window (or an explicitly documented selected-line subset); and
   `value_demand_read_issues` must fall to zero for replayed aliases. Add
   `page_row_passes`, `a_page_visits`, per-page A read/write issue/response,
   empty-pass skips, value-page fill issue/response, page-buffer high-water,
   fill/apply overlap cycles, and fill/apply port-conflict cycles. Reject if
   any terminal ledger, range permit, generation, retry, or duplicate-order
   check fails, or if it stores 16K values.
2. **Second: matched bounded internal-prefetch microcase under unchanged A
   order.** Sweep 0/1/2/4/8 credits only; preserve the 32-owner cap. Accept
   only if exact output and all existing ledgers close and the measured
   prefetch-hit/late/duplicate counters explain a lower `simTicks` without
   adding logical-window payload. Reject CPU warm loops and pre-checkpoint
   population as substitutes.
3. **Third, only after item 1: 4K+4K banked ping-pong microcase.** Require
   nonzero measured overlap, zero same-bank conflicts, page lifetime closure,
   and the same A/page-visit count as non-overlapped 4K replay. Test 2K+2K
   only if its measured additional page visits are paid back by demonstrated
   independent-port overlap; otherwise reject it.

No candidate should launch or claim a full GZP speedup until those checks pass,
the exact hardware storage/port/control ledger is recorded, and a fresh
same-commit/same-checkpoint repeated matrix verifies the prescribed mechanism
signature. In particular, a result that improves ticks by hiding a 16K value
payload, eliding A `WriteResp`, reordering FP duplicates, or changing the
pre-checkpoint cache state is rejected even if its output hash happens to match
this input.
