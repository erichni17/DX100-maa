# Bounded old-result write coalescing (2026-08-24)

## Decision

Promote the replicated composition for the tested small SSSP input only:

- old-result pressure policy `densest`
- four partial pressure writes allowed in flight
- existing SoA/JIT value cache enabled with 64 active owners
- existing pre-A value lookahead enabled
- active contexts unchanged at eight

The coalescing mechanism and the scheduling composition are separate deltas.
Dense/four alone is a sparse-smoke Pareto point and a small-SSSP bandwidth win,
but it is not a small-SSSP speedup.  Adding the existing cache/owner and pre-A
controls produces the replicated small-SSSP promotion result.

This is not a full-graph GAPBS claim.  No native baseline was rerun.

## Source and binary

- Source commit: `cf3d9fc00705bd260788dc16b2b1d5a1e41c5d85`
- gem5 SHA-256:
  `1e079112469892681d661925db09ccfbc845d1a2ce45c79e1d9a4902c19a9863`
- Read-only binary:
  `/data1/nier/dx100-binaries/gem5-1e079112469892681d661925db09ccfbc845d1a2ce45c79e1d9a4902c19a9863.opt`
- Frozen Ramulator target:
  `/data1/nier/dx100-runs/2026-08-12-hybrid-line-handoff-8a5c7712/input/libramulator.so`
- Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`

The final binary's `ldd` resolves the isolated absolute symlink to that frozen
Ramulator target.

## Mechanism and bounded cost

The accepted publisher still has eight retained 64-byte cache-line slots.
Pressure concurrency and victim ordering are orthogonal runtime controls:

- `original_oldest` with limit eight is the default and exactly reproduces the
  accepted pressure issue order and timing.
- `densest` scans only the fixed eight filling slots, selects the greatest
  valid-word population, and breaks ties by age.
- limits are restricted to 1, 2, 4, or 8 partial writes awaiting response.
  Full-line writes remain concurrent and do not consume this limit.

The fixed per-indirect-unit old-result object remains exactly 1,128 bytes:
512 payload bytes and 616 metadata bytes.  The optimization adds no payload or
line slots.  Conservative control accounting is three bits per unit (two limit
bits and one policy bit), plus a bounded eight-slot popcount/compare scan used
only in dense mode.

The scheduling composition adds no payload.  The value-cache owner records
were already provisioned, its 64-owner selection reuses them, the two enable
bits already exist, and contexts remain eight.

Logical ordinals, old-value bits, duplicate-index order, Row/Offset reorder
behavior, A/result WriteResp terminal accounting, and zero host SPD payload
access are unchanged.

## Exact tests

The focused C++ test covers both policies at limits 1/2/4/8.  It checks exact
partial-response high water, original oldest order, dense descending-popcount
order with unequal masks, full-write concurrency, fail-closed invalid controls,
and the exact 1,128-byte object size.  Optimized and ASan/UBSan binaries pass.
The Python integration test checks CLI/SimObject/config plumbing, counters,
fixed capacity, and distinct pressure/terminal modes.

## Sparse matched sweep

Raw root:
`/data1/nier/dx100-runs/2026-08-24-old-result-partial-sweep-cf3d9fc0-r1`

All five trace-free restores used the accepted checkpoint and guest with
16K logical elements, 4K physical elements, two memory channels, four indirect
units, and 32 row-table slices.  All wrapper return codes, exact outputs,
A/result ledgers, and terminal markers pass.  The checkpoint tree SHA-256 is
unchanged before and after:
`a8670a14070b2cfd140c6d480270016a98375a785542fcebee1d554eb83b1d98`.

| Arm | First simTicks | Speedup | Writes | Words/write | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| oldest/8 | 687,827,203 | 1.000000000x | 11,399 | 2.225458 | baseline |
| dense/1 | 733,637,257 | 0.937557623x | 10,165 | 2.495622 | reject: slower |
| dense/2 | 701,817,990 | 0.980064935x | 9,653 | 2.627991 | reject: slower |
| dense/4 | 686,432,788 | 1.002031393x | 9,491 | 2.672848 | select |
| dense/8 | 688,221,270 | 0.999427412x | 11,332 | 2.238616 | reject: slower |

Dense/four is the unique non-regressing Pareto point: 0.202728% lower ticks,
16.7383% fewer writes, and 20.1033% higher useful-word packing than the exact
oldest/eight reproduction.

The earlier one-partial diagnostic is frozen as rejected at
`/data1/nier/dx100-runs/2026-08-24-old-result-coalescing-sparse-21e1a7ac-r1`:
it reduced writes 10.8255% but regressed ticks 6.66011%.  Its missing lead
wrapper-status file is recorded; exact terminal evidence nevertheless passes.

## Dense/four small SSSP

Accepted root:
`/data1/nier/dx100-runs/2026-08-24-sssp-old-result-small-23e924da-r3`

Dense/four root:
`/data1/nier/dx100-runs/2026-08-24-sssp-old-result-dense4-cf3d9fc0-r1`

The exact certificate reaches 69,633/69,633 vertices with distance sum 135,168,
maximum distance two, expected hashes, zero triangle violations, and zero
missing predecessors.  Four routed windows, 65,536 captures, four terminals,
and all A/result responses close exactly.  Host SPD reads remain zero.

| Metric | Accepted | Dense/four | Change |
| --- | ---: | ---: | ---: |
| first simTicks | 10,002,435,519 | 10,007,120,503 | +0.046838% |
| result writes | 37,098 | 24,705 | -33.4061% |
| useful words/write | 1.766564 | 2.652742 | +50.1639% |

Dense/four alone is therefore a bandwidth win with no speedup claim.

## Separate scheduling composition

Strict trace-free matrix root:
`/data1/nier/dx100-runs/2026-08-24-sssp-dense4-scheduling-matrix-cf3d9fc0-r2`

All three new cells have gem5 and fail-closed wrapper return code zero, exact
certificates, exact ROI terminal markers, clean `m5_exit`, four instructions,
four terminals, 65,536 captures, and balanced A/result responses.  The accepted
checkpoint tree hash is unchanged:
`4094f51bf2108814d2a561e40e4e3ccf5014e10cfb712161d3666db73e3212ff`.

| pre-A | cache + 64 owners | First simTicks | Writes | Words/write | Versus dense/four |
| --- | --- | ---: | ---: | ---: | ---: |
| off | off | 10,007,120,503 | 24,705 | 2.652742 | control |
| on | off | 10,005,018,082 | 25,402 | 2.579954 | 1.000210137x |
| off | on | 9,981,237,594 | 17,332 | 3.781214 | 1.002593156x |
| on | on | 9,976,182,331 | 17,805 | 3.680764 | 1.003101204x |

Cache-only is traffic-minimal.  Combined pre-A plus cache/64-owner scheduling is
latency-minimal and is promoted for this tested small SSSP input.  Relative to
the accepted candidate it is 1.002631587x (0.262468% lower ticks), with 52.0055%
fewer result writes.  Relative to dense/four alone it is 1.003101204x with
27.9296% fewer writes.

Replica2 root:
`/data1/nier/dx100-runs/2026-08-24-sssp-dense4-combined-replica2-cf3d9fc0-r1`

Replica2 is bit-identical to the matrix combined cell: first/final ticks
9,976,182,331/11,365,847,556; 17,805 writes; pre-A
4,100/4,005/4,100; 57,344 value hits; four terminals; and every first-window
SoA/JIT counter identical.  Its service exited successfully, exact hashes
verify, and checkpoint before/after hash lists are identical.

## Excluded artifacts and limits

- The all-true pressure smoke at
  `/data1/nier/dx100-runs/2026-08-24-old-result-coalescing-21e1a7ac-smoke-r1`
  is correctness-only because its guest differs from the accepted sparse
  smoke.  It achieved 16 useful words/write but is not timing evidence.
- Interrupted combined and preliminary matrix roots are marked superseded and
  are not evidence.
- The promotion covers one deterministic small SSSP graph, not full GAPBS
  graph coverage or a native speedup pair.
- Defaults remain attribution-safe `original_oldest` plus eight credits.  The
  promoted tested composition requires explicit runtime controls.
