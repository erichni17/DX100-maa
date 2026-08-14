# 16K Row/Offset + bounded SoA/JIT RMW applicability audit

Date: 2026-08-14
Audit baseline: `8801c9da`
Scope: static source audit only; no product source was changed and no gem5
simulation was launched.

## Decision

The mechanism is a general indirect RMW optimization, not a GZP-only fusion.
It applies whenever a benchmark can publish, for one bounded logical window,
three ordinary registered arrays—`index[]`, `value[]`, and optional
`predicate[]`—and does not require the RMW's old value as a returned tile.  The
16K Row/Offset chain preserves repeated-index update order; the bounded
no-result-payload implementation then performs the selected ADD/MIN/MAX on the
target array and completes only after the A-line writeback response.

The audit finds 64 benchmark implementation call expressions (API definitions
excluded): 59 vector-tile value sources and 5 scalar/broadcast sources; 37
predicate-bearing calls; and 7 calls requesting an old-value/result tile.  Of
those, 54 are direct vector candidates after operand staging, 3 are direct
no-result RMWs but need scalar-broadcast support, and 7 cannot use the no-result form
without a separate old-value protocol.  The counts are static source counts,
not dynamic instruction counts; loops, OpenMP workers, and compile-time arms
can multiply execution counts.

## Contract used for classification

The existing API makes the relevant distinction explicit:
`MAA_gem5.hpp:646-663` encodes vector RMW as `(data, idx_tile, src_tile,
op, cond_tile, dst_tile)`, and `MAA_gem5.hpp:665-682` provides the scalar form.
The functional reference performs `dst[idx] = data[indices[idx]]` before the
update when `dst_tile` is present (`MAA_functional.hpp:755-798`).  Therefore a
bounded SoA/JIT operation with no result payload can preserve the update, but
cannot silently replace a call whose caller consumes `dst_tile`.

The proposed direct-memory input is not an instruction-sized fusion of
`STREAM_LD`, `INDIRECT_LD`, ALU, or predicate generation.  It needs a compiler
or library lowering that materializes the exact logical sequence into SoA
arrays and publishes those arrays before issuing the RMW.  Predicate values
must use the target's true/nonzero convention; an inactive sentinel such as
`-1` must not be passed as a raw unsigned predicate.

## Compatibility table

“Compatible” means semantically compatible with the no-result SoA/JIT RMW
after the smallest integration listed below.  “Conditional” means the
existing call has an old-result or ordering dependency that the stated
mechanism does not provide.  “No” means the benchmark has no indirect RMW
callsite to optimize.

| Workload / static callsites | Value source; predicate | Old/result use and downstream reuse | Alias/order requirement | Direct SoA/JIT result | Smallest integration |
|---|---|---|---|---|---|
| GZP / `UME/gradzatp.cpp:363-365,400-402,422,455` (4) | Vector: `corner_volume` and `csurf * zone_field`; `tileCond` is `corner_type >= 1` | No result tile; `point_volume` and `point_gradient` are consumed after waits/barrier (`:432-436` and normalization) | Duplicate point indices require original corner order; two destination arrays are separate RMW generations | Compatible | Add per-core 16K `index/value/predicate` staging and ACKed publication; issue volume then gradient. Keep corner-map, gather, multiply, and predicate generation outside the generic RMW. This is the GZP integration described by `gzp_soa_jit_integration_2026-08-14.md`, not a fused opcode. |
| GradZatZ / `UME/gradzatz.cpp:267,368-370,429` (3) | Vector: corner volume, then computed gradient contribution; `tileCond` from corner type | No result tile; zone volume is loaded to compute the later gradient, with explicit waits (`:268`, `:432`) | Repeated zone indices; preserve corner order within each logical window; do not overlap the zone-volume producer with its dependent gradient generation | Compatible | Stage `c_to_z`, value, and predicate arrays; complete zone-volume RMW before publishing values for zone-gradient RMW. |
| GradZatZ-invert / `UME/gradzatz_invert.cpp:279` (1) | Vector corner volume; no predicate argument | No result tile; waits for zone-volume accumulation (`:280-283`) | Repeated zone indices, integer/FP ADD order as currently issued | Compatible | Stage generated range indices and corner-volume values; one no-predicate ADD call. `gradzatp_invert.cpp` has no RMW callsite. |
| GAPBS BC / `gapbs/src/bc.cc:293` (1) | Vector `path_counts` contribution; `tile1` predicate | No result tile; path counts are consumed in later graph phases | Duplicate vertices can be targeted by multiple edges; retain atomic RMW and logical edge order | Compatible | Materialize `tile4` indices, `tile2` values, and `tile1` predicates; add completion token before the next level/phase. |
| GAPBS BC full / `gapbs/src/bc_maa_full.cc:141-142` (2) | Vector `tile3` delta; `tile_cond` predicate | No result tile; same immutable contribution is applied to `deltas` and `scores` | Same source contribution must feed both destinations; preserve inter-phase barriers | Compatible | Reuse one published value/predicate SoA for two sequential destination calls; add two completion tokens or an aggregate fence. |
| GAPBS BC new / `gapbs/src/bc_new.cc:458-459,907` (3) | Vector computed delta, and path-count source; predicates `tile_cond` | No result tile; later iterations read updated arrays | Duplicate graph destinations; preserve atomic update semantics and phase order | Compatible | Add a generic SoA submit wrapper; stage the two delta/scores calls and the path-count call independently. |
| GAPBS PageRank / `gapbs/src/pr.cc:357` (1) | Vector `curr_contrib` gathered by `tile3`; no predicate | No result tile; `incoming_total` is read after `wait_ready(tile5)` (`:359-365`) | Repeated destination nodes and FP ADD order must match the existing logical sequence | Compatible | Stage `tilei` and `tile5` as index/value arrays; completion before the score update. |
| GAPBS SSSP / `gapbs/src/sssp.cc:330` (1) | Vector `dist[u] + weight`; no predicate | **Old result required:** `dst_tile=tilei` is compared with the post-RMW load to decide frontier/bin insertion (`:330-347`) | MIN update and old/new comparison are order-sensitive | Conditional / not direct | Either add an old-value/result SoA output with authenticated ordering, or retain a separate pre-RMW gather that is guaranteed to observe the same serialized update. The bounded no-result form alone is insufficient. |
| NAS CG / `NAS/cg/cg.cpp:1028,1072,1097,1145,1355,1396,1421,1469` (8) | Vector products `a[k]*p[colidx[k]]` or `a[k]*z[colidx[k]]`; no predicate | No result tile; `q`/`r` are consumed after the RMW waits and barriers | Each row has repeated `q[j]`/`r[j]`; preserve sparse-matrix traversal order for FP ADD | Compatible | Lower the existing range-loop + gather + multiply sequence to an index/value SoA producer, then submit one bounded RMW per logical row window. No GZP predicate fusion is implied. |
| NAS IS / `NAS/is/is.cpp:848` (1) | Scalar constant `1`; no predicate | No result tile; prefix scan starts after `wait_ready(stream_tile)` (`:850-856`) | Integer histogram increments are atomic; duplicate keys are expected | Compatible with scalar broadcast | Add scalar-constant/broadcast mode (or materialize an all-ones value array), stage `stream_tile` as indices, and retain the completion fence before prefix accumulation. |
| Hashjoin histograms / `hashjoin/src/parallel_radix_join.cpp:453,759` (2) | Scalar constant `1`; no predicate | No result tile; histogram is prefix-scanned after `wait_ready(tile2)` (`:455-466`, `:761-768`) | Duplicate bucket indices are expected; integer ADD is order-insensitive | Compatible with scalar broadcast | Same scalar-broadcast API as NAS IS; stage hashed bucket indices. |
| Hashjoin partition destinations / `hashjoin/src/parallel_radix_join.cpp:483,810` (2) | Scalar constant `1`; no predicate | **Old result required:** `tile3`/`tile2` is the post-increment destination used by `maa_indirect_store_vector` (`:483-490`, `:810-818`) | `dst[idx]++` allocation order must match scatter order | Conditional / not direct | Requires an old-value result stream or a separate ordered fetch-add service; no-result SoA/JIT cannot produce the scatter offsets. |
| API RMW family / `API/test.cpp:292,400,454,517,609,794,890,988` (8) | Vector; five predicate-bearing (`:517,609,794,890,988`) | `:454` requests `c_tile` and stores it (`:455`); other seven do not request results | Range-loop variants generate repeated indices; preserve generated order | 7 compatible; `:454` conditional | Add one SoA wrapper and compiler/lowering hooks. For the seven no-result cases, publish arrays; for `gather_rmw_dst`, retain legacy RMW or add result output. |
| API functional family / `API/test_functional.cpp:153,228,266,313,378,452,532,614,698` (9) | Vector; predicate on `:313,378,452,532,614,698` | `:266` and `:698` request `c_tile`; the functional model explicitly copies old data (`MAA_functional.hpp:755-798`) | Same duplicate-index order as the tested API variants | 7 compatible; 2 conditional | Same wrapper; add an explicit result-capable variant before converting the two `dst_tile` tests. |
| API strided / `API/test_strided.cpp:299,450,539,671,810,956,1109` (7) | Vector; five predicate-bearing | No result tile | Strided/range-generated indices and predicate compaction must retain lane order | Compatible | Compiler lowering must materialize the strided index/value/predicate arrays; no architectural fusion is needed. |
| API tested-parallel / `API/test_tested_parallel.cpp:243,348,409,490,579,674,771` (7) | Vector; five predicate-bearing | No result tile | Cross-thread duplicate indices retain atomic ordering; tests are the parallel control | Compatible | Same SoA wrapper plus per-owner generation/completion token; preserve existing barriers. |
| API compiler bridge / `API/MAA_compiler_api.cpp:107,112` (2) | Vector values supplied by compiler bridge; one predicate form | No result tile | Compiler owns lane order and operation selection | Compatible | Add a direct-memory ABI entry point beside the current tile-based bridge and make the compiler pass materialize arrays. |
| Deprecated prefetch API / `API/prefetch_depricated.cpp:194` (1) | Vector; `condj_res_tiles` predicate | No result tile | Prefetch pipeline may overlap multiple tiles; retain generation lifetime until completion | Compatible, but legacy | Add the same direct-memory call only if this deprecated benchmark remains in scope; otherwise use it as a regression/control case. |
| Virtual/native FIFO control / `API/test_virtual_native_rmw.cpp:136-139` (1) | Vector increment; native predicate | **Old result required:** `rmw_old_tile` is checked at `:161-181`; call also tests FIFO interaction with a virtual owner | Must preserve virtual-owner queueing and exact old-value observation | Conditional / not direct | Keep as a FIFO/result regression. Conversion needs both result output and a mixed virtual/native ordering contract; it is not evidence for generic SoA applicability. |
| Spatter MAA targets and other gather/scatter-only code | No indirect RMW call expression; only loads/stores/fused gather paths (e.g. `spatter/src/Spatter/Configuration.cc:620-649,743-749`) | No RMW result to optimize | N/A | No | Do not force an RMW API into Spatter; its potential target is gather/scatter or a separate scatter-accumulate transformation. |

## What is general versus GZP-specific

The common optimization is the RMW back end: 16K logical Row/Offset metadata,
bounded A-line state, direct-memory index/value/predicate spans, alias-safe
ordered application, and no result payload when the caller only needs the
updated destination. The same shape covers integer ADD, FP ADD, and MIN where
the source arrays and ordering contract are supplied by the caller.

GZP-specific work is the producer side: `corner_type >= 1`, `c_to_p_map`,
`corner_volume`, `csurf * zone_field`, virtual gather publication, and the
normalization barrier. Those computations may be fused or staged, but they are
not properties of a general RMW instruction. In particular, replacing GZP's
page-local RMW sequences with one logical 16K RMW is valid only after publishing
the full 16K index/value/predicate arrays and preserving the corner-order
alias chain; it does not make the predicate or value generation universal.

## Required API/compiler surface

The smallest reusable interface is conceptually:

```text
submit_indirect_rmw_soa(dst, index_base, value_base,
                        predicate_base_or_null, count, op,
                        completion_token)
```

The implementation must validate registered, disjoint role spans; accept
16K logical metadata with bounded physical SPD/result storage; keep fixed
state for the A-line and Row/Offset chain; apply repeated-index aliases in
logical insertion order; and close only after the authenticated A-line
`WriteResp`. It must not expose a result tile unless an explicit result-capable
variant is added. A scalar mode may either broadcast one registered scalar or
use a caller-materialized value array; the latter is sufficient for the first
vector-only integration.

Compiler/library integration is therefore small but workload-specific:

1. Add the ABI/helper above and a direct-memory lowering in the compiler bridge
   (`MAA_compiler_api.cpp`), with explicit count, element type, op, predicate
   convention, and completion token.
2. Add SoA staging/publication for producer values and predicates in UME/GAPBS
   and for generated sparse products in NAS CG. Staging must be charged and
   ACKed; ordinary SPD stream-store visibility is not an implicit publication.
3. Add scalar broadcast for NAS IS and hashjoin histograms.
4. Keep SSSP and hashjoin partition destinations on the existing path until an
   old-value/result protocol exists. Do the same for the API result tests and
   the virtual/native FIFO control.
5. Validate duplicate indices, false predicates, FP32 order-sensitive aliases,
   cross-owner atomicity, retry/response closure, and two generations before
   using any performance comparison.

## Static evidence and limits

The inventory was produced with repository `rg` searches for
`maa_indirect_rmw`, `maa_indirect_rmw_vector`, and
`maa_indirect_rmw_scalar` under `benchmarks/`, followed by source-context
inspection. API definitions in `MAA_gem5.hpp`, `MAA_functional.hpp`, and
`MAA_gem5_magic.hpp` are not counted as benchmark calls. Commented reference
loops and declarations are not counted. Dynamic counts cannot be inferred
without inputs, OpenMP scheduling, and compile-time macro selection.

No claim is made here about speedup, area, or gem5 correctness. This report is
an applicability and integration gate; each compatible workload still needs a
matched correctness experiment with exact output fingerprints and explicit
RMW issue/response accounting.
