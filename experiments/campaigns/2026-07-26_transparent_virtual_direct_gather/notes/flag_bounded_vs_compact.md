# FLAG Bounded4 Generalization

The fully bounded 4K mechanism was compared directly against `compact16` on
all 14 imported FLAG gathers. Every pair used simulator source `3b50cdb`, gem5
SHA-256 `43cf815f...7097`, the same FLAG guest binary SHA-256
`0f6fb9e8...31ce`, and the same input. Each arm passed exact output, terminal
exit, artifact, resolved-configuration, and Ramulator artifact checks before
aggregation.

## Result

- Equal-weight latency geometric-mean delta: **+5.290%**.
- Per-case range: **-2.040% to +8.624%**.
- Outcomes: **2 faster, 0 tied, 12 slower**.
- Compact16 emitted 9,329 excess C-line writes over the dense minimum across
  the suite; bounded4 emitted 656.

This means the full-XRAGE speedup does not broadly hold. Most FLAG compact
arms already retire C near the cache-line minimum, so bounded4 pays direct B
ingestion and 4K-epoch overhead without enough write-coalescing benefit to
recover it. The two winning cases are the cases where compact16 emitted about
3,400 excess C writes and bounded4 removed nearly all of them.

The same explanation predicts XRAGE. Full XRAGE compact16 emitted 342,540 C
writes for a 262,144-line minimum, or 80,396 excess writes. Bounded4 emitted
262,903, only 759 excess writes. That much larger avoided-retirement term is
why bounded4 is 7.146% faster on XRAGE while being 5.290% slower geometrically
on FLAG.

## Interpretation

The bounded design is currently a cost/performance tradeoff:

- It reduces the configured comparable storage lower bound by 72.979% versus
  native16/full descriptors.
- It has modest average overhead on FLAG direct gathers.
- It can outperform compact16 when its smaller legal schedule fixes severe
  destination-line fragmentation.

The next optimization should target direct B ingestion or overlap between B
refill and A service without increasing the 4K Row/Offset budget. Merely
shrinking to 2K, growing to 8K, or deepening the feeder beyond 128 lines has
already been rejected on the representative FLAG case.

## Evidence

- Corrected campaign:
  `/data1/nier/dx100-runs/2026-07-29-flag-bounded-vs-compact-3b50cdb-v2`
- Aggregate report:
  `/data1/nier/dx100-runs/2026-07-29-flag-bounded-vs-compact-3b50cdb-v2/summary-v2`
- The earlier campaign without `-v2` is explicitly quarantined because its
  compact and bounded guest binaries did not match.
