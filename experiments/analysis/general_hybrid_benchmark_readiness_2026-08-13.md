# General hybrid benchmark readiness — 2026-08-13

This change prepares benchmark evidence for a token-bound virtual page load,
not an ALU/RMW/store fusion.  The ABI is an ordinary `STREAM_LD` with the live
virtual-gather completion tile in `tsrc1`.  Each page passes
`backing + page_offset` as its base and local scalar bounds `0..page_size`.
The downstream ALU, RMW, and store instructions are unchanged.

## Ready contracts

| workload | native16 | native4 | 16K logical / 4K physical controls | exact marker |
|---|---|---|---|---|
| API micro | yes | yes | full-wait stream, page-gated stream, one-page token `STREAM_LD`, optional two-page ping-pong | `VIRTUAL_TILE_CONSUMER_RESULT ... errors=0`; identical hash |
| NAS CG | yes | yes | full-wait, page-gated, one-page token `STREAM_LD`; ping-pong rejected (no second free tile) | `CG_FINGERPRINT ... nonfinite_x=0 nonfinite_z=0 result=PASS`; identical `x_q5` |
| UME GZP/GZZ | yes | yes | direct-index 16K virtual producer, ordinary 4K condition/map/ALU/RMW consumers; ping-pong rejected | one `UME_OUTPUT_FP ... nonfinite=0` plus one zero-error `UME_REFERENCE_PASS`; identical hash |
| GAPBS PR/BFS | yes | new native4 controls | **not wired** | `PR_FP` normalized-q5/nonfinite certificate; full `BFS_FP` depth certificate |
| XRAGE | existing | existing bounded control | use existing non-fused direct-index runner; no fused instruction added | `MAA_GATHER_VERIFY_PASS length=... hash=...` |

GAPBS remains deliberately unclaimed for the general hybrid.  PR and BFS have
tile-sized frontier/range intermediates in addition to the gather reload; only
replacing `wait_virtual_page + stream_load(backing)` would still issue unsafe
16K ordinary operations on a 4K physical SPD.  The Makefile additions are
exact native4 controls, not hybrid targets.

## Deterministic matched API matrix

Build both logical native sizes and the 16K hybrid binary:

```sh
experiments/scripts/build_virtual_tile_consumer.sh /tmp/dx100-vm-build
```

Preview the frozen arm order and selector payloads:

```sh
python3 experiments/scripts/run_general_hybrid_benchmark_matrix.py \
  --workload api --out /data1/nier/dx100-runs/2026-08-13-general-hybrid-api \
  --gem5 /ABS/gem5.opt --ramulator-library /ABS/libramulator.so \
  --native16 /tmp/dx100-vm-build/test_virtual_tile_consumer_T16384 \
  --native4 /tmp/dx100-vm-build/test_virtual_tile_consumer_T4096 \
  --hybrid /tmp/dx100-vm-build/test_virtual_tile_consumer_T16384 \
  --native16-options 'native 16384' --native4-options 'native 4096' \
  --hybrid-options 'deferred {selector}' --pingpong
```

Add `--execute` to run it.  The arm order is fixed: `native16`, `native4`,
`hybrid_stream_control` (`paged 4096`), `hybrid_page_gated`
(`paged_overlap 4096`), `hybrid_token_stream_ld`, and
`hybrid_token_stream_ld_pingpong`.  All four hybrid arms restore the same
checkpoint and absolute selector path serially; the checkpoint tree is hashed
again after every restore.  `--future-arm NAME=SELECTOR` adds an explicitly
named future treatment without aliasing it to the token-stream control.

The analyzer refuses speedups until checkpoint/restore exits, one terminal
`m5_exit`, fatal-text scan, first ROI statistics, per-workload certificate, and
cross-arm exact key all pass.  It reports `numInst_STRWR`, raw `cycles_STRWR`,
and total per-stream request/SPD access cycles.  At this base revision,
`StreamAccess` charges all stream completions to `cycles_STRRD`; therefore
`cycles_STRWR` is preserved but never presented as a valid store-only latency.
The token arms are checked against the mechanism's trace and scalar stats, not
just against benchmark output.  Each API token arm must have exactly four
`page_materialization_submit` events, four
`page_materialization_page_ready` events, one exact-closure summary, and one
retirement.  Its single context must submit and ready pages 0, 1, 2, and 3
once, carry activation counts 1 through 4, and account every committed line as
either forwarded producer payload or an ACK-gated cache read.  Both admission
and dispatch fallback events/stats, plus the legacy direct-retirement fallback
stat, must be zero.  Integrated token workloads apply the same four-page
closure per context and require submits equal pages-ready, at least one
retirement, no open contexts, and zero materializer fallback.  The report
includes forwarded and cache-read line counts.  The ordinary and page-gated
controls must show no materializer lifecycle events or nonzero materializer
stats.

The runner freezes the complete gem5 `configs` tree, not only `se.py`, so its
relative `common` and `ruby` imports are part of the hashed campaign input.

## Ordinary workload builds

After generating `util/m5/build/x86` (or using the source `m5op.S` directly),
the explicit targets are:

```sh
make -C benchmarks/NAS/cg GEM5_BUILD=1 cg_maa_4K_fp cg_maa_16K_fp cg_maa_16K_general_fp
make -C benchmarks/UME GEM5_BUILD=1 gradzatp_maa_4K_fixed_fp gradzatp_maa_16K_fixed_fp gradzatp_maa_16K_general_fp
make -C benchmarks/UME GEM5_BUILD=1 gradzatz_maa_4K_fixed_fp gradzatz_maa_16K_fixed_fp gradzatz_maa_16K_general_fp
make -C benchmarks/gapbs GEM5_BUILD=1 pr_maa_2G_fp pr_maa_2G_4K_fp bfs_maa_2G_fp bfs_maa_2G_4K_fp
```

For XRAGE, retain the existing non-fused entry point:

```sh
experiments/scripts/run_xrage_virtual_case.sh OUT GEM5 XRAGE_VERIFY INPUT_JSON \
  RAMULATOR_LIB RAMULATOR_PROVENANCE SIMULATOR_PROVENANCE CHECKPOINT_RUN
```

The integrated CG and UME binaries use the same runner and exact deferred
selector checkpoint boundary:

```sh
python3 experiments/scripts/run_general_hybrid_benchmark_matrix.py \
  --workload cg --out OUT/CG --gem5 GEM5 --ramulator-library RAMULATOR \
  --native16 benchmarks/NAS/cg/cg_maa_16K_fp \
  --native4 benchmarks/NAS/cg/cg_maa_4K_fp \
  --hybrid benchmarks/NAS/cg/cg_maa_16K_general_fp \
  --native16-options MAA --native4-options MAA \
  --hybrid-options 'MAA_DEFERRED {selector}' --execute

python3 experiments/scripts/run_general_hybrid_benchmark_matrix.py \
  --workload ume-gzp --out OUT/GZP --gem5 GEM5 \
  --ramulator-library RAMULATOR \
  --native16 benchmarks/UME/gradzatp_maa_16K_fixed_fp \
  --native4 benchmarks/UME/gradzatp_maa_4K_fixed_fp \
  --hybrid benchmarks/UME/gradzatp_maa_16K_general_fp \
  --native16-options 1000000 --native4-options 1000000 \
  --hybrid-options '1000000 {selector}' --execute

python3 experiments/scripts/run_general_hybrid_benchmark_matrix.py \
  --workload ume-gzz --out OUT/GZZ --gem5 GEM5 \
  --ramulator-library RAMULATOR \
  --native16 benchmarks/UME/gradzatz_maa_16K_fixed_fp \
  --native4 benchmarks/UME/gradzatz_maa_4K_fixed_fp \
  --hybrid benchmarks/UME/gradzatz_maa_16K_general_fp \
  --native16-options 1000000 --native4-options 1000000 \
  --hybrid-options '1000000 {selector}' --execute
```

Replace `OUT`, `GEM5`, and `RAMULATOR` with absolute paths.  GAPBS can use
this runner only as a native16/native4 comparison (omit `--hybrid`); an attempt
to request a GAPBS hybrid matrix fails closed.

No file under `src/mem/MAA` is changed by this benchmark preparation.
