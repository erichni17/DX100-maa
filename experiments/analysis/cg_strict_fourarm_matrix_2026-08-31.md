# CG strict four-arm matrix (2026-08-31)

## Contract

This report records the fresh `CG_NA=256` same-binary matrix.  The guest is
compiled once from the final CG source lineage and one checkpoint is captured
before the deferred selector is read.  Native16, equal-work native4x4,
original legacy 16K-logical/4K-physical hybrid, and strict page-fed two-pass
restores all use that guest, input, checkpoint, gem5, Ramulator, and matched
feeder depth.  Historical native4 evidence is not reused because its
provenance is mismatched.

The selector is supplied through per-restore FD 198; restores therefore run in
parallel without mutating a shared selector.  The treatment delta is explicit
in the machine-readable manifest.  The strict arm is judged on the partial-
mask P-retirement path: 32-byte/cycle payload staging, one active line,
32 payload banks, 16 bounded combiner slots, 8 response slots, exact
scheduled/read-word closure, and `B-close-before-A` timing.  This is not a
complete-line direct-gather claim.

## Result

The terminal table and exact fingerprints/reductions are populated only by
`run_cg_strict_fourarm_matrix.py`; its `matrix.complete`, immutable raw ledger,
and per-arm process identities are the acceptance gates.  No full CG run is
authorized by this report.

| Arm | Selector | Physical tile | Strict | `simTicks` | Decision |
|---|---|---:|---:|---:|---|
| native16 | `native_16k` | 16,384 | no | pending | pending run |
| native4x4 | `native_4kx4` | 4,096 | no | pending | pending run |
| original_hybrid | `legacy_4k` | 4,096 | no | pending | pending run |
| strict_two_pass | `page_fed_product_soa_jit` | 4,096 | yes | pending | pending run |

The committed manifest is the authoritative handoff once the small matrix
passes; raw logs and stats remain outside Git under the coordination evidence
root.
