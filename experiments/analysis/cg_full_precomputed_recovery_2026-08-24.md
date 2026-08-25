# Full CG precomputed-input recovery (2026-08-24)

## Problem

The first full physical-page-product candidate spent more than 18 CPU-hours
inside NAS `makea` before checkpointing. It never entered ROI and ran no native
arm. That root is frozen as superseded progress evidence:
`/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-baf142f7-r1`.

## Recovery

Current `cg.cpp` already supports `USE_DATA_FROM_FILE`. Full mode now copies and
hashes the frozen 4-core NAS matrix header used by the prior full CG campaign:

- Path:
  `/data1/nier/dx100-runs/2026-08-11-cg-bounded-789cc703-full-v8/input/cg_data_4C.h`
- SHA-256: `f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131`
- Bytes: 992,830,458

Only full mode adds `USE_DATA_FROM_FILE`; small mode retains runtime generation.
The unused matrix-construction helpers are permitted only in that precomputed
build. The candidate selector is still read after checkpoint, so the
physical-page-product treatment remains outside checkpoint construction.
Ramulator is bound to frozen SHA-256 `76ea3a...a15753`.

## Gate outcome

Root:
`/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-precomputed-5d51743b-r2`.

The checkpoint log contains exactly `Using data from file!`, contains no
`makea started!` or `makea finished!`, and exits at tick `52,769,040,500`.
The checkpoint file ledger is frozen. Candidate-only O3 completed with 16K
logical reorder, 4K physical SPD, 32 RowTable slices, two memory channels,
eight guest tiles/core, no native arm, no trace, and no wall timeout.

The mechanism closes, but all four exact quantized fingerprints differ from
the frozen reference. The candidate is rejected under its predeclared
correctness gate; see
`experiments/analysis/cg_full_page_product_rejection_2026-08-25.md`.

The preceding precomputed `r1` launched no simulator; compilation failed only
because `-Werror` promoted expected unused generator helpers. Runner commit
`5d51743b` adds the narrow full-only warning exception.
