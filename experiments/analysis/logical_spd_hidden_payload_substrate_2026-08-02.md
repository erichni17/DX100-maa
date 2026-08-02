# Logical SPD hidden-payload substrate

Date: 2026-08-02

This patch implements only the private physical payload allocation required by
the accepted logical-SPD-cache design. It does not connect the payload to the
logical controller.

## Fixed geometry and accounting

Each MAA receives two private FP64 page slots. Each slot is represented by two
adjacent 32-bit lanes, and every lane contains exactly 4,096 elements:

```text
visibleTileCount   = the existing architectural tile count
allocatedTileCount = visibleTileCount + numMAAs * 4
hiddenLaneID       = visibleTileCount + maaID * 4
                     + logicalSlot * 2 + fp64Lane
```

The valid coordinate bounds are `maaID in [0,numMAAs)`,
`logicalSlot in [0,2)`, and `fp64Lane in [0,2)`. Invalid coordinates and count
overflow fail before publishing an allocated ID. The fixed private payload is
65,536 bytes per MAA (`2 * 4096 * 8`) and 262,144 bytes for four MAAs. This
payload must be charged separately from controller state and simulator-only
metadata.

Visible tiles retain their configured physical capacity. The hidden tail uses
its fixed 4,096-word lane stride even when a legacy configuration gives visible
tiles a different capacity. Construction zeros the visible allocation as
before, zeros the complete hidden tail, and initializes tile metadata and
element-state bookkeeping for all allocated lanes.

## Isolation boundary

`MAA::num_tiles` remains the visible count. It is still the count passed to the
instruction file, invalidator, virtual-ready bookkeeping, and every legacy
admission path. `SPD::check_tile_id` also remains the common boundary for data,
size, ready, status, dirty, element-finished, latency, and waiter operations;
it now names the visible count explicitly. An FP64 operation at the last
visible lane is rejected before it could cross into the appended allocation.

The hidden coordinate mapper and slot-base helper are private. Only `SPD`,
`MAA` (for later controller-generated micro-ops), and the narrowly named unit
test peer can call the layout arithmetic. There is no public hidden-ID getter,
no MMIO aperture extension, and no benchmark/API alias.

## Deliberate non-features

- No logical scheduling is wired.
- No response handling is wired.
- No benchmark wiring or public API is added.
- No timing, bandwidth, or port behavior is changed.
- No performance claim is made.
- No area/power claim or total-storage-reduction claim is made.

The host gate checks exact mapping, adjacency, bounds, per-MAA isolation,
fixed-stride offsets, guarded zero initialization, overflow handling, and exact
payload bytes in optimized and ASan/UBSan builds. A separate source contract
checks that legacy instruction, MMIO, coherence/invalidation, wait, size, and
data paths continue to use only the visible count.
