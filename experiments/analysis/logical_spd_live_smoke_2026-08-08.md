# Logical SPD live functional evidence

Date: 2026-08-08

## Result

The independently accepted live cache-authority repair was integrated and both
bounded payload organizations completed in the production gem5 binary.

| Mode | Geometry | Exact output | Completion |
|---|---|---|---|
| Serial4K | one 4K-element private slot, four pages | hash `7303085050985348899`, zero errors | PASS |
| PingPong2K | two 2K-element private slots, eight pages | hash `7303085050985348899`, zero errors | PASS |

Both modes use 32,768 private payload bytes. Ordinary visible SPD remains
allocated in addition to that payload.

## Provenance

- Source commit used by both runs: `17f8c6796f0f8cf2973abe0996bf2e833cbaeea0`
- Production `gem5.opt` SHA-256:
  `1957c723abc4cb43586100b336f693dd53352c73201a63f0df5bdd261f7d56a8`
- Raw root:
  `/data1/nier/dx100-runs/2026-08-08-spd-live-integration-17f8c679`
- Serial binary SHA-256:
  `9a5dd2dc8ab89552ef904e52004426b34d241fa150072a88810806e7e986b4ec`
- Ping-pong binary SHA-256:
  `82b4e710868751bc087892d90a94cf89b7934241a692492733028a9e56d23bbb`

Each raw directory preserves the command manifest, source status and diff,
artifact hashes, checkpoint hashes, wrapper return codes, restore log, final
stats, resolved configuration, and logical-SPD trace.

## Validation

- The controller, hidden-payload, bridge/lifecycle, and ABI runners passed in
  optimized and sanitizer configurations, together with 54 Python contracts.
- `scons build/X86/gem5.opt -j8` completed successfully.
- Checkpoint and restore return codes were zero for both modes.
- Both restore logs contain one exact output marker and one `m5_exit` marker,
  with no fatal/error marker.
- Both resolved one MAA, 16K logical elements, 4K visible physical elements,
  16 initial RowTable slices, and the requested logical mode.
- Both traces contain `event=logical_spd_complete`.

## Claim boundary

This evidence validates uninterrupted functional fill, scalar transform, and
writeback for one logical execution, one MAA, four cache ports, and 64-byte
lines. It does not validate reorder survival, multiple MAAs, concurrent logical
executions, checkpointing live controller state, graceful live drain, total
area, iso-area equivalence, speedup, throughput, or performance. The transform
and cache-control timing are not yet architecture-faithful, so the recorded
`simTicks` are deliberately not compared.
