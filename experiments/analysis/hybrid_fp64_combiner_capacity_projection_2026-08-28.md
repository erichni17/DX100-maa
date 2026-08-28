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

In the uncapped offline model, 512 lines cover the trace's live
destination-line working set and every line completes before retirement.
Capacities 1,024 and 2,048 produce the same 2,048 writes.  That 512-line model
can retain up to 4,096 FP64 words, however, before also charging the eight-line
response pool; it is an optimistic transaction ceiling rather than a legal
physical4K live configuration.

## Hardware interpretation

Line tags and payload are separate.  The implementation supports many line
tags over one shared bounded word pool.  For live physical4K experiments, the
response pool plus combiner payload is capped at 4,096 words: 3,968 combiner
words for FP32 CG and 4,032 for the FP64 API micro.  A 512-line table therefore
does not receive 512 full payload lines; global payload pressure can evict a
tag before its line completes.

Using the current four-unit FP32 CG storage geometry, the corrected packed
ledger gives:

| Lines | Combiner payload / unit | Hybrid comparable lower bound | Reduction vs. native comparable |
|---:|---:|---:|---:|
| 16 | 1 KiB | 1,596,712 B | 49.733% |
| 32 | 2 KiB | 1,604,152 B | 49.499% |
| 64 | 4 KiB | 1,619,536 B | 49.014% |
| 128 | 8 KiB | 1,651,328 B | 48.013% |
| 256 | 15.5 KiB, capped | 1,714,080 B | 46.038% |
| 512 | 15.5 KiB, capped | 1,749,088 B | 44.936% |

The storage report previously multiplied all combiner words by eight bytes
even under `--word-bytes 4`.  Commit `91b3ab9e` corrects that overcharge and
adds an FP32 regression test.  FP64 replay payload counts were already eight
bytes per word and do not change.

## Decision boundary

This is an exact transaction projection only while line capacity is the
binding resource.  The live capacity sweep must reproduce the 16-line arm,
preserve exact output and work, enforce the separate 4K payload bound, and
measure cache misses, Ramulator reads, and `simTicks`.  The first uncapped
256/512 attempts correctly failed closed because response plus derived
combiner payload exceeded 4K.  The capped live arms are the relevant evidence.

For capacities below 512, the first partial update to each of the 2,048 lines
can still incur read-for-ownership even when total transaction count falls.
The separate dense write-allocation candidate targets that first-write cost
with only an initialized-line bitmap.

The live bounded CG NA256 sweep now rejects capacity growth: 256 and 512 line
tables reduce writes 31.392% and 50.131% but regress `simTicks` 0.177795% and
0.767058%.  See `hybrid_cg_combiner_capacity_live_2026-08-28.md`.  The FP64
API sensitivity remains useful only as a cross-workload check, not as a reason
to promote the larger structure.

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
