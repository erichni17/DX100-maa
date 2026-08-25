# CG physical-product handoff probe (2026-08-25)

## Scope

This is the next falsifiable boundary identified by
`cg_single_pass_destination_order_2026-08-25.md`.  It is a dedicated gem5
microbenchmark, not a NAS CG run and not a performance experiment.

The guest has exactly four physical 4K FP32 producer pages and one logical
16K SoA/JIT selected descriptor.  For every local offset `d` in `[0,4096)`,
the index on each page is `d`, so every one of the 4096 destinations has a
four-page collision chain.  The exact MUL products in source/page order are:

| physical page | left | right | exact FP32 product |
| --- | ---: | ---: | ---: |
| 0 | 4096 | 4096 | `+2^24` |
| 1 | 1 | 1 | `+1` |
| 2 | -4096 | 4096 | `-2^24` |
| 3 | 1 | 1 | `+1` |

Starting from zero, serial FP32 ADD in this order leaves every destination at
the exact word `0x3f800000` (`1.0f`).  This is intentionally order-sensitive.

## Boundary and ABI result

The current ABI can expose the required pre-publication bits without a new
hardware register or a host SPD read.  Immediately after each completed
physical `maa_alu_vector<float>(..., MUL_OP)` result, the guest issues an
ordinary `maa_stream_store` to a separate coherent diagnostic page and waits
for that store's completion.  Only then does it issue the guarded
response-bearing publication of that unchanged physical product tile into the
coherent 16K product array.

Thus the comparison is between two coherent captures of the same physical MUL
tile, with the first capture completed before publication begins.  It does not
read an SPD tile from the CPU and does not claim direct observation of an
unlatched internal ALU wire.  That is the smallest ABI-valid instrumentation
for this boundary; a direct ALU-wire observation would require a new explicit
diagnostic ABI and is not inferred here.

Each page also response-publishes its physical index tile.  The benchmark then
performs four ordinary page-local `INDIR_RMW_VECTOR ADD`s and exactly one
`maa_indirect_rmw_vector_soa_jit<float>` ADD using the complete published
index/product arrays.  There are no masked descriptors and no four-pass
emulation.

## Exact checks

The guest fails unless all of the following close exactly:

- All 16,384 source indices equal their coherent published words.
- All 16,384 pre-publication product words equal their published product words
  bit-for-bit.
- All 4,096 ordinary and SoA/JIT destination words equal `0x3f800000`, and
  the two destination arrays match bit-for-bit.
- FNV-1a word hashes equal: index `14754458253095254915`; product
  `2849837644626199427`; destination `17263589712773219203`.
- Eight response-bearing page publications close exactly: 2,048 issues,
  accepts, and WriteResps, with eight terminals (four index plus four product
  pages).

The run script records the source commit, compiled guest hash, gem5 binary
hash, Ramulator configuration hash, checkpoint hashes, commands, final stats,
and trace hashes.  It refuses a dirty source tree and records
`performance_claim=0`.

## Execution status

The source contract passes 9 focused tests, `bash -n`, `git diff --check`, and
the GEM5 guest compilation.  No full application or native result was used.

At the execution check, no pre-existing gem5 process was found and the host
reported 285 GiB available memory, so a small probe would otherwise have been
feasible.  The live launch was intentionally not made because the runner
refuses a dirty source tree and the required `codex-coord checkpoint` could
not commit the validated files: the repository commit-message hook crashes
while loading an absent `MAINTAINERS.yaml`.  This is a repository hook/input
failure, not a benchmark result.  Do not infer pass, performance, or a
physical-to-published bitwise conclusion from the source validation.

A live result, if accepted after that commit blocker is repaired, must add the
checkpoint/restore terminal markers, exact guest line, final stats, frozen
artifact hashes, and all response-count closures here.
