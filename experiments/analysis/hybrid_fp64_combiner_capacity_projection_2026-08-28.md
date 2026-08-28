# FP64 hybrid combiner-capacity projection (2026-08-28)

## Result

A corrected eight-word-per-line replay exactly reproduces the accepted FP64
hybrid's 8,698 live backing writes at 16 fully associative combiner lines.
Increasing bounded line capacity predicts a meaningful transaction knee:

| Combiner lines | RR writes | Full-line writes | Eviction writes | Final partial writes | Reduction vs. 16 |
|---:|---:|---:|---:|---:|---:|
| 16 | 8,698 | 0 | 8,682 | 16 | 0% |
| 32 | 6,588 | 90 | 6,466 | 32 | 24.259% |
| 64 | 5,868 | 187 | 5,617 | 64 | 32.543% |
| 128 | 5,180 | 312 | 4,740 | 128 | 40.446% |
| 256 | 3,958 | 627 | 3,075 | 256 | 54.495% |
| 512 | 2,048 | 2,048 | 0 | 0 | 76.455% |

At 512 lines, only one quarter of the 2,048-line logical output is resident at
once, but that capacity covers the trace's live destination-line working set.
Every line completes before retirement.  Capacities 1,024 and 2,048 produce
the same 2,048 writes and add no transaction benefit.

## Hardware interpretation

Each line carries 64 B of payload regardless of FP32/FP64 word width.  A
512-line combiner therefore uses 32 KiB per indirect unit, or 128 KiB across
four units, before tags, masks, references, ports, and arbitration.  It is not
free, but it is still much smaller than restoring a full logical16 result SPD.

Using the current four-unit FP32 CG storage geometry, the corrected packed
ledger gives:

| Lines | Combiner payload / unit | Hybrid comparable lower bound | Reduction vs. native comparable |
|---:|---:|---:|---:|
| 16 | 1 KiB | 1,596,712 B | 49.733% |
| 32 | 2 KiB | 1,604,152 B | 49.499% |
| 64 | 4 KiB | 1,619,536 B | 49.014% |
| 128 | 8 KiB | 1,651,328 B | 48.013% |
| 256 | 16 KiB | 1,716,960 B | 45.947% |
| 512 | 32 KiB | 1,852,324 B | 41.686% |

The storage report previously multiplied all combiner words by eight bytes
even under `--word-bytes 4`.  Commit `91b3ab9e` corrects that overcharge and
adds an FP32 regression test.  FP64 replay payload counts were already eight
bytes per word and do not change.

## Decision boundary

This is an exact transaction projection, not a latency result.  The live
capacity sweep must reproduce the 16-line arm, preserve exact output and work,
and measure cache misses, Ramulator reads, and `simTicks`.  A 512-line point is
worth considering only if its end-to-end gain justifies reducing the current
storage saving from about 49.7% to about 41.7% in the four-unit CG geometry.

For capacities below 512, the first partial update to each of the 2,048 lines
can still incur read-for-ownership even when total transaction count falls.
The separate dense write-allocation candidate targets that first-write cost
with only an initialized-line bitmap.

## Evidence

- Accepted insertion trace SHA-256:
  `81124d0ae826847d0052f3ff67155457ad6dac7de307dd7ebf3e14f5efee3e06`.
- Trace source: sealed hybrid64 arm in
  `hybrid-equal-work-micro-r4`.
- Analyzer: `experiments/scripts/analyze_virtual_combiner_reuse.py`, invoked
  with `--words-per-line 8 --ways 0`.
- The 16-line replay is fail-closed against the observed 8,698 writes.

The result is one deterministic API-micro trace and does not establish a
suite-wide destination-line working set.
