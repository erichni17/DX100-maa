# GZP dual-logical16 RMW gate — 2026-08-14

## Scope and audit

This default-off arm extends the performance-promotable masked-index GZP
volume treatment; it does not change native DX100.  The existing guarded
response-bearing `STREAM_ST` is type-generic for four- or eight-byte words, so
one invocation can carry a completed physical-4K FP32 gradient-product page
without carrying a predicate page.  It copies one 64-byte source line into one
of eight existing publisher credits and retains the exact packet payload until
its authenticated `WriteResp`.

The publisher deliberately keeps the source SPD tile referenced until the
last unique page `WriteResp`.  Reusing the gradient producer before the
completion token is ready is therefore illegal.  The guest waits on that token
before `tile2` is overwritten.  Accepted publisher lines can still overlap an
already active indirect/gather/ALU unit, which is measured by
`STR_PublishOverlapIssues`; the single stream unit means publisher service can
also serialize following page streams.  The paired gate records both effects
instead of assuming overlap is free.

## Implemented arm

For every full 16K logical window:

1. `point_volume` uses one masked-index SoA/JIT ADD with `c_to_p_map` and
   `corner_volume` directly.
2. The existing physical-4K gather and FP32 multiply order is unchanged.
   Exactly four gradient-product pages are published to coherent per-core
   backing.  No predicate page is published or registered.
3. `point_gradient` uses a second masked-index SoA/JIT ADD with the same
   `c_to_p_map` and the published gradient backing.
4. The partial tail stays on the existing page-local path, and normalization
   remains behind the existing OpenMP barrier.

Masked `UINT32_MAX` classification is therefore shared by both RMWs.  Active
indices, insertion order, and FP32 operands are unchanged.  The treatment is
selected only by `token_stream_ld dual_logical16`; legacy and volume-only
defaults are unchanged.

The exact storage/traffic ledger is:

- existing producer SPD page: 4,096 FP32 words = 16,384 bytes;
- existing publisher credits: 8 x 64 bytes = 512 bytes;
- coherent gradient backing allocation: 4 x 16,384 FP32 words = 262,144
  guest-memory bytes;
- one-window publisher traffic: 16,384 FP32 words = 65,536 bytes, 1,024
  response-bearing cache lines, four terminal publications;
- incremental hidden logical-16K payload: 0 bytes;
- CPU untimed copy: 0 bytes; separate predicate publication: 0 bytes.

## Exact one-window gate

Runner: `experiments/scripts/run_gzp_dual_logical16_one_window.py`.
It builds one guest, creates one immutable `n=16,384` checkpoint, and restores
the volume-only and dual-logical16 selectors against that exact checkpoint.
Both arms enable the already accepted row-directed pre-A lookahead and freeze
all other hybrid knobs.

Acceptance requires the exact output hash and scalar reference, unique
`m5_exit`, masked-index terminal generations, closed value/A-line issue and
response counts, closed publisher stats and trace `WriteResp` counts, and
strictly lower candidate `simTicks`.  Host time is not an authorized metric.

Measured evidence and the final accept/reject decision will be appended after
the clean-source paired run.
