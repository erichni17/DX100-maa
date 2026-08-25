# Full SSSP host-SPD fallback failure (2026-08-25)

## Decision

The first full S22 candidate with the bounded CPU aperture is **rejected**.
The aperture correctly distinguishes the offending request as non-droppable;
the guest still performs a real host load beyond physical SPD.

- Root: `/data1/nier/dx100-runs/2026-08-24-sssp-aperture-full-s22-r1`.
- Source commit: `7bfb5c63`; original pre-tail-replay `sssp.cc` SHA-256
  `71cef23d...f1`.
- gem5 SHA-256: `703c1e1d...f0f1a5`.
- Guest SHA-256: `b9225249...4c3c`.
- Graph SHA-256: `23eb25e3...4eebc`.
- Checkpoint exit: zero. Restore exit: 134. Wrapper exit: one.
- Failure tick: `238,504,751,130`.
- Request: address `0x801c4000`, tile 28, tile offset 16,384 bytes,
  64-byte line, physical bytes 16,384, logical bytes 65,536,
  `speculative=0`.

Each thread allocates eight tiles in order: `tilev`, `tileu`, upper/lower
bounds, `tilei`, `tile1`, `tile2`, condition. Thread 3 owns tiles 24--31, so
tile 28 is its `tilei`. The failure is therefore the legacy post-RMW host loop
reading the next line after a full 4K predicate tile. It is not the
task-tagged `ReadSharedReq` prefetch form handled by the aperture drop.

The L1D-only ablation also fails at the same boundary. No exact fingerprint,
final stats, or performance result exists for either full root.

## Required successor

The bounded fallback must publish `tilev`, `tile1`, and `tilei` through
response-bearing physical-page transfers into already charged coherent
backing, then let the CPU consume backing memory. It must add no tile or
dedicated payload, preserve duplicate winner order and logical-window routing,
measure zero host-SPD reads, and close publication responses. A deterministic
full-page fallback reproducer must pass before another full S22 launch.
