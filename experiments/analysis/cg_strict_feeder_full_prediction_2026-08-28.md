# Pre-result prediction: strict full-CG feeder pair (2026-08-28)

Recorded while both full restores are active and before either has terminal
stats or a result file.

## Prediction

| Arm | Predicted first-ROI `simTicks` | Acceptance range |
|---|---:|---:|
| Feeder1 control | 160,746,544,242 | exact reproduction expected |
| Feeder64 candidate | approximately 90.7B | 85B to 110B |

The point estimate applies the measured NA1024 masked-retirement reduction
of 43.5698% to the accepted one-line full observation. The range allows the
full workload to have a different mix of B ingestion, A service, cache
interference, and consumer work. A result outside the range is not
automatically incorrect, but requires a new phase-level explanation.

## Predicted invariants

Both arms must preserve the accepted full certificate's exact non-timing work:

- 21,920 strict operations;
- 22,456,140 B cache-line fetches;
- 359,137,280 descriptor insertions;
- 84,740,503 A issues;
- 147,611,841 total strict backing issues;
- 87,680 strict pages ready;
- 10,960 SoA/JIT operations and full windows;
- 179,568,640 selected/applied/admitted Q words;
- 43,840 product-page terminals; and
- zero drains, fallbacks, fused-P work, and coherent index backing.

Both arms must also satisfy the frozen full numerical tolerances, exact
terminal structure, one ROI close, and one `m5_exit`. The resolved configs may
differ only in `virtual_index_buffer_lines=1` versus `64`; debug traces are
disabled in both.

## Expected explanation

If feeder64 wins, it should do so by overlapping the same sequential B-line
requests, not by changing A order, backing writes, numerical work, or logical
reorder scope. The full 16K Row/Offset barrier remains in both arms.

Main risks to the point estimate:

- full-CG B lines may have different cache residency or channel contention;
- faster B admission may move pressure into Row insertion or later phases;
- request-generation width is not yet timed explicitly, although the NA1024
  one-line/cycle upper-bound penalty is only 0.5407%; and
- the 64-line hardware structure is not synthesized or frequency-qualified.

No native4 timing or speedup is predicted by this document.
