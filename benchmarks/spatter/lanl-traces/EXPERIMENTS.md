# LANL Trace Experiment Matrix

## Purpose

FLAG extends the DX100 evaluation with a second LANL application trace. It is
not evidence for tile virtualization until the mechanism and hardware capacity
are isolated from instruction fusion.

## Gather Arms

Use the same FLAG configuration, simulator commit, CPU/memory configuration,
and correctness hash for every arm:

| Arm | Physical tile | Logical window | Gather path | Question |
|---|---:|---:|---|---|
| CPU | none | none | core gather | Application-level reference |
| N16 | 16K | 16K | indirect load + stream store | Original DX100 reference |
| F16 | 16K | 16K | fused gather-to-dense store | Fusion benefit only |
| F4 | 4K | 4K | same fused path | Cheap physical reference |
| V4/16 | 4K | 16K | fused, LLC-backed virtual tile | Virtualization tradeoff |

`V4/16` is the required target design point, not a label for the current C0
control. Do not use that label until the simulator is configured with 4K of
physical tile capacity while counters demonstrate a 16K logical reorder
window.

The central comparisons are:

- `F16` versus `N16`: instruction-fusion contribution.
- `V4/16` versus `F16`: virtualization overhead at the same logical window.
- `V4/16` versus `F4`: reordering opportunity recovered by virtualization.

An equal-logical-window virtual design is expected to take at least as many
cycles as equal-capacity native hardware because it adds backing-store traffic,
metadata, and dependencies. If `V4/16` beats `F16`, do not call that a
virtualization gain without showing a concrete mechanism absent from `F16`,
such as additional transfer overlap, and confirming it with event counters and
a matched control.

## Scatter Arms

The current transparent virtual mechanism implements gather only. FLAG's six
scatter configurations are immediately useful for native DX100 correctness,
tile-size sensitivity, and row-reordering studies, but they are not virtual
scatter results. Add virtual scatter only after defining ordering for duplicate
destinations and a deterministic write-retirement model.

The recovered FLAG scatter traces happen to have unique destinations. The
verification binary nevertheless implements last-program-writer semantics so
future inputs with duplicates fail if stores retire incorrectly.

## Promotion Gates

1. Verify source archive, derived input, binary, and simulator hashes; use the
   same `SPATTER_DATA_SEED` for every arm.
2. Run the host parser smoke and functional MAA output smoke.
3. Run one reduced gem5 correctness binary and require exactly one
   `MAA_GATHER_VERIFY_PASS` or `MAA_SCATTER_VERIFY_PASS` marker.
4. Require terminal gem5 completion, one ROI, final nonempty stats, and no
   panic, fatal, assertion, or abort marker.
5. Compare `simTicks`, never host wall time.
6. Confirm all non-treatment parameters match across an A/B pair.
7. Check mechanism counters: backing reads/writes, virtual page fills and
   evictions, bytes moved, outstanding-transfer occupancy, overlap cycles,
   row-buffer hit rate, and MAA cycles.
8. Repeat any surprising result from a fresh output directory.

## Workload Expansion

- Use FLAG first: its 31,923/63,846-element patterns are directly compatible
  with DX100's `count=1` Spatter path.
- Treat the bundled AMG, LULESH, Nekbone, and PENNANT files as trace kernels,
  not full applications. Their large `count` values need an exact repeated-
  pattern implementation before MAA use.
- Extract one serial ROI from AMG2023 or Branson only after freezing an input
  and application-level correctness oracle.
- Use SPARTA later for nested indirection. It is a mechanism stress test, not
  a drop-in Spatter workload.
