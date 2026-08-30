# Complete-line hybrid campaign checkpoint (2026-08-30)

## Selected design

- logical/reorder scope: 16K Row/Offset;
- physical result payload: 4,096 FP64 words;
- FLAG geometry: 2,048 tags, 8-way XOR7, 3,072 combiner words, 1,024
  response words;
- XRAGE knee: 1,536 tags, 8-way XOR7, 2,560 combiner words, 1,024
  response words;
- response insertion: four words per MAA cycle;
- combiner insertion/update ports: four banks;
- lookup: four starts/four completions, three-cycle tested latency;
- retirement: one complete line per MAA cycle plus exact final tail;
- selection: bounded 16-page ready queues; and
- coherence: exact WriteResp ownership plus rejection of overlapping live MAA
  producer spans.

See `selected_complete_line_hybrid_2026-08-30.md` for the simple mechanism.

## Accepted evidence

| Gate | Result | Record |
|---|---|---|
| XRAGE complete-line application | 37,268,284 ticks versus 42,312,279 native16 | `xrage_complete_line_hybrid_results_2026-08-29.md` |
| 14 FLAG gathers | -7.463% latency vs fused16; -0.026% vs compact16 | `flag_xor8_results_2026-08-29.md` |
| finite drain | width 1 exact; 37,252,008 XRAGE ticks | `xrage_complete_line_drain_results_2026-08-29.md` |
| lower associativity | 8-way XOR7 exact on XRAGE and all 14 FLAG; timing-equivalent to 16-way | `xrage_combiner_xor_results_2026-08-29.md`, `flag_xor8_results_2026-08-29.md` |
| lookup latency | XRAGE +0.134%, FLAG +0.155% at 3 cycles | `xrage_lookup_latency_results_2026-08-29.md`, `flag_lookup_latency_results_2026-08-30.md` |
| bounded ready selection | XRAGE exact, FLAG all-14 exact; timing-neutral | `xrage_page_ready_drain_results_2026-08-30.md`, `flag_page_ready_drain_results_2026-08-30.md` |
| live MAA producer ownership | selected XRAGE exactly reproduces 37,291,759 ticks | `xrage_backing_ownership_results_2026-08-30.md` |
| finite combiner banks | four banks add 0.310%; one bank rejected at +20.253% | `xrage_combiner_bank_results_2026-08-30.md` |

## Important attribution

The improvement comes from retaining scattered returned words privately until
a full destination cache line is available. The small bounded direct4 control
publishes thousands of partial fragments; the selected design publishes one
full line per eight FP64 words plus only the final tail.

The fail-closed complete-line flag itself is timing/work-identical when disabled
at the same capacity. The result is not a fused direct sink and does not remove
the CPU for arithmetic consumers.

## Bugs and rejected paths

- Unsafe zero-placeholder dense publication was rejected by independent
  review because coherent readers could observe invalid zeros.
- 1,536 tags do not cover all FLAG inputs; FLAG retains the 2,048-tag geometry.
- 8-way low-bit indexing causes a partial XRAGE victim; XOR7/10 remove it.
- 4-way remains illegal for every tested XOR shift 1-20.
- A final-drain bug misclassified a credit-blocked full `0xff` line as an
  illegal partial; `3d28c649` retains and retries it after ACK.
- The first drain sweep rejected valid finite arms because peak statistics are
  summed across four operations; the corrected gate uses `width * operations`.
- Two FLAG launch attempts were rejected for missing Ramulator library and
  live-runner mutation; later campaigns freeze runner/library artifacts.
- The final XOR8 FLAG wrapper exited after all 14 rows; recovery is explicitly
  marked and every per-case artifact was checked before acceptance.

## Hardware boundary

Genuinely bounded in source: useful payload, tag count/ways, response credits,
lookup metadata/starts/completions/latency, write credits, drain width, ready
selection, exact ACK identity, complete-line/tail legality, and live MAA
producer overlap.

Still not closed:

1. physical tag, payload, and reference RAM banking/ports and same-set hazards;
2. CPU or virtual-alias writes to privately retained destination fragments;
3. reset/epoch implementation and generation wrap;
4. synthesized area, energy, and Fmax; and
5. general row-table virtualization for logical64K/physical16K.

## Execution record

This autonomous block began at 2026-08-29 17:53 EDT. The prior comparable
block took 2h40m. By this checkpoint the current block has run for more than
seven hours, including repeated full gem5 rebuilds and 14/28/56-point
application matrices. Every accepted milestone is committed locally on
`codex/virtualization-selected-integration-cont-20260826`; no push was made.
