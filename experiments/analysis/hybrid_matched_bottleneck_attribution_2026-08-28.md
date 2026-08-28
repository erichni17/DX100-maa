# Matched-feeder hybrid bottleneck attribution (2026-08-28)

## Result

The retained-16K hybrid preserves the intended global reorder work.  Its
remaining gap to native16 is destination-backing traffic, not extra A-source
DRAM requests.

This comparison uses the accepted same-binary, same-checkpoint equal-work API
micro at feeder depth 64:

| Metric | Native16 | Logical16/physical4 hybrid | Delta |
|---|---:|---:|---:|
| `simTicks` | 48,487,143 | 57,330,645 | +18.239% |
| Direct memory read packets | 10,548 | 10,548 | 0 |
| MAA cache write packets | 2,048 | 10,746 | +8,698 |
| MAA cache read packets | 2,049 | 4,097 | +2,048 |
| L3 MAA demand misses | 2,049 | 4,097 | +2,048 |
| Ramulator read requests, both channels | 24,817 | 26,876 | +2,059 |
| MAA busy cycles | 153,087 | 181,741 | +28,654 |

The hybrid issues exactly the same 10,548 direct memory reads as native16.
Its 16K Row/Offset window is therefore not degrading into four native 4K
source passes in this experiment.

## Causal interpretation

The hybrid emits 8,698 masked backing writes for 2,048 destination cache
lines.  Its 16-line destination combiner produces zero complete cache lines.
The L3 records exactly 2,048 new misses in the backing address region, plus
7,427 hits.  The miss count is one per backing line, and Ramulator observes
2,059 extra reads after cache/coherence effects.

The evidence is consistent with the first partial write to each backing line
performing a read-for-ownership allocation.  Later masked fragments to that
line hit or merge.  The consumer then reloads the coherent backing before its
four physical-page ALU/store actions.  Thus "copying to LLC" is not merely a
small on-chip transfer in the measured design: fragmented partial writes make
the cache fetch every old line once and process 8,698 write transactions.

This explains why the hybrid remains slower than native16 despite preserving
16K source reordering.  It also explains why the hybrid still beats matched
native4x4: the saved A-service work is larger than the virtualization traffic,
but not large enough to erase it completely.

## Experiments in flight

1. Sweep 16/32/64/128 bounded combiner lines at fixed feeder, checkpoint, and
   semantic work.  This tests whether modest extra assembly capacity reduces
   masked transactions or the 2,048 backing misses.
2. Compare cold backing with ideal free preallocation and a charged sequential
   preallocation pass.  This brackets a dense-overwrite/no-read-allocate hint
   without claiming that warm cache state is free hardware.

If neither treatment materially reduces end-to-end ticks, increasing the
combiner or adding an initializer is rejected.  A larger result buffer can
eventually assemble complete lines, but recreating a full 16K result store
would defeat the storage objective.

## Evidence

- Native16 stats SHA-256:
  `80524c56006aacddbce5111833333df66de1a8f303b35283647b9b11011b7d45`.
- Hybrid64 stats SHA-256:
  `59ae9f430d56279f7f743775ad8c7e0e8e468a12b5feb3c86346ca48ddb2c911`.
- Accepted controls and provenance:
  `hybrid_feeder_matched_native_controls_2026-08-28.md`.

These are one-observation microbenchmark results.  They establish a measured
bottleneck and an experiment priority, not suite-wide performance or
synthesized cache/combiner cost.
