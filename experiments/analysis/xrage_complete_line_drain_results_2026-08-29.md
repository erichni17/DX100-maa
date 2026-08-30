# XRAGE complete-line drain-width result (2026-08-29)

## Decision

Accept one complete-line write per MAA cycle as sufficient for the selected
XRAGE gather0 design. Width 1 closes exact output and all 8,192 producer
writes/WriteResps at 37,252,008 `simTicks`, 0.044% below unlimited width and
11.959% below native16.

Do not call the sub-percent finite-width differences speedups. Widths 1, 2,
and 4 change scheduling order and are 0.044%, 0.077%, and 0.158% lower latency
than unlimited; width 8 is tick-identical. The result is that drain injection
bandwidth is not the selected design's bottleneck, not that throttling improves
the architecture.

## Fixed comparison

All five arms use:

- source `ac8c3a45d23d58edf046c132a4f911b1d96a23d8`;
- gem5 SHA-256
  `4e6f54296c8369d352da24d333c4097392a966aa3845865e5c00706df2890cc8`;
- one frozen XRAGE gather0 64K input and exact verifier;
- logical16K/physical4K, 1,536 tags x 16 ways, 2,560 combiner words,
  1,024 response words, 64 acknowledged write credits, and four response-word
  insertion attempts per MAA cycle;
- line-level direct consumer handoff with four active contexts; and
- no timeout.

Only `virtual_complete_line_drain_lines_per_cycle` changes.

| Width (lines/cycle) | `simTicks` | vs unlimited | vs native16 | Budget-stall cycles | Peak sum across 4 operations |
|---:|---:|---:|---:|---:|---:|
| unlimited | 37,268,284 | 0.000% | -11.921% | 0 | 32 |
| 1 | 37,252,008 | -0.044% | -11.959% | 4,571 | 4 |
| 2 | 37,239,488 | -0.077% | -11.989% | 1,317 | 8 |
| 4 | 37,209,440 | -0.158% | -12.060% | 63 | 16 |
| 8 | 37,268,284 | 0.000% | -11.921% | 11 | 24 |

The peak statistic is the sum of each instruction's peak. The finite-width
contract is therefore `peak_sum <= width * 4`, not `peak_sum <= width`.

## Attribution

Width 1 records 4,571 cycles in which at least one complete line was ready but
the per-cycle issue token was exhausted. Those stalls do not increase overall
ROI latency because line publication overlaps the dominant producer/consumer
memory work and the downstream coherent cache/network already serializes much
of the traffic. Width 0 exactly reproduces the pre-drain 37,268,284-tick result,
so changing the old word/bank budgets from raw ticks to MAA-cycle identity is
also timing-neutral for this workload.

This closes only issue count at the drain boundary. It does not time the
16-way tag lookup, ready-line scan, eight payload/reference reads and frees,
scoreboard lookup, or reset. Those remain the next hardware-performance gates.

## Correctness

Every arm has:

- terminal checkpoint/restore and `m5_exit`;
- exact output hash `5576400619275092867`;
- 8,192 complete producer lines, zero partials, and 8,192 exact WriteResps;
- 8,192 direct-consumer reads, ALUs, writes, and responses;
- four descriptors/contexts, no page fallback, and no early-line overflow;
- a configured-width peak bound; and
- one frozen binary, library, input, config, runner, and source manifest.

Evidence root:
`/data1/nier/dx100-runs/2026-08-29-xrage-complete-line-drain-sweep-r2`.

Artifact ledger:
`xrage_complete_line_drain_artifacts_2026-08-29.sha256`.

The first sweep root (`...-r1`) contains the same terminal simulations but is
rejected because its postprocessor compared the four-operation peak sum to one
operation's width.

## Next gate

Use the exact insertion trace to replay 16/8/4-way organizations at fixed tag
and payload capacity. If lower associativity causes any partial eviction,
reject it before timing. If it closes, then add explicit lookup latency/banks
and a bounded ready queue before another full application run.
