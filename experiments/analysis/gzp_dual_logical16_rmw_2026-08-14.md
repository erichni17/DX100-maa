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

## Measured evidence

The default-off implementation was frozen in `ec49389ff49d7a61a719deb0f206fdc8289680ad`.
Trace-ledger support was frozen in `55a02fd80e91708dfb43efb6bae5a600d7931318`,
the composition/replica gate in
`9ef78930e661bda38594e7f16bfd1e032d900edd`, and the full-size concurrent
gate in `0e8c6d9f` (a user-authorized one-time `--no-verify` commit after direct
`py_compile`, focused-contract, and `git diff --check` validation).  The gem5
binary SHA-256 is
`ae0afdcb2e5780bedf75130ef405e9fb8f21d013c760b1060cb265e4ec0de73e`.

The inherited eight-context mechanism run is frozen at
`/data1/nier/dx100-runs/2026-08-14-gzp-dual-logical16-one-window-55a02fd-r2`.
It closed exact output/reference and all terminal/WriteResp ledgers.  The
volume-only arm took 437,481,352 ticks and the dual arm 318,956,390 ticks
(1.371602x).  This is mechanism evidence only because it used eight active
contexts and 32 active value owners.

The decision-bearing one-window composition run is frozen at
`/data1/nier/dx100-runs/2026-08-14-gzp-dual-logical16-c32-v64-two-rep-9ef7893-r1`.
Both replicas are bit-for-bit/statistically identical at 32 active contexts,
64 active value owners, masked indices, and pre-A enabled:

- volume-only: 405,168,484 ticks, five RMWs, 298,994 RMW cycles;
- dual logical16: 254,113,119 ticks, two RMWs, 331,707 RMW cycles;
- direction: 151,055,365 fewer ticks, or 1.594441x baseline/candidate;
- exact FP32 hash `12472729817211538253`, zero volume/gradient errors, and
  196,384 checked elements in every restore;
- candidate publisher: 1,024 issues, 1,024 accepts, 1,024 unique WriteResps,
  four terminals, zero retries, 992 credit-stall observations, credit HWM 8,
  and zero measured non-stream overlap issues in each replica.

Publisher serialization therefore did not consume the page-RMW reduction in
the exact composed one-window gate, even though candidate aggregate RMW cycles
rose by 32,713.  The one-window decision is **ACCEPT**, conditional on full-GZP
closure.

## Full-GZP handoff and review caveat

The first transient full attempt is preserved as incomplete infrastructure
evidence at
`/data1/nier/dx100-runs/2026-08-14-gzp-dual-logical16-full-c32-v64-two-rep-0e8c6d9-r1`
with sentinel `campaign.exit=8`; its checkpoint child disappeared without a
gem5 fatal marker.

The lead owns the durable successor unit
`dx100-gzp-dual-logical16-full-0e8c6d9-r2.service` (last observed main PID
2305169) and evidence root
`/data1/nier/dx100-runs/2026-08-14-gzp-dual-logical16-full-c32-v64-two-rep-0e8c6d9-r2`.
It uses one fixed `n=1,000,000` guest/binary/checkpoint, the same 32-context,
64-owner, pre-A/masked configuration, and four concurrent control/treatment
restores (two replicas per arm) with no timeout.  At handoff it was active with
`campaign.exit=8`; no full result is claimed here.

Promotion remains pending both fail-closed full-GZP artifact analysis and an
independent review.  In particular, an independent reviewer has not yet
validated the concurrent per-process read-only selector bind, the completed
full-run provenance, or the final issue/accept/WriteResp and RMW-cycle ledgers.
