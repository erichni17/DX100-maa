# CG direct4 product / page-fed q16 tradeoff — 2026-08-26

## Disposition

**This treatment preserves 16K reordering only for the q Row/Offset stage. It
intentionally loses p-side 16K reordering and must not be described as full
16K reorder preservation for both stages.**

The static implementation is accepted at the end of this report, but the one
permitted `CG_NA=1024` matched live pair is **rejected for architecture or
performance evidence**. The serial page-fed control passed every correctness
and mechanism gate. The first treatment restore aborted during q-index page 1
admission before a fingerprint or terminal record. Consequently, no treatment
correctness claim, no paired `simTicks`, no speedup, and no promotion claim are
reported.

The live failure identified one missing dependency wait in the new post-product
q16 admission loop. The current source adds `wait_ready(t0)` between each
`maa_range_loop` and page admission in both SpMV sites. Focused tests and the
exact eight-tile syntax build pass after that correction. Per the single-pair
launch limit, the corrected source was not silently rerun.

## Architectural slice

The selectable treatment is `direct4_product_page_fed_q16`; the matched serial
control remains `page_fed_product_soa_jit` in the same guest and checkpoint.

For each complete logical 16K sparse window, the treatment uses only `t0..t7`:

1. For each of four physical 4K pages, stream `colidx` to a physical index
   tile, issue ordinary physical `maa_indirect_load` for `p[colidx]`, stream
   sequential `a[k]`, multiply, and response-publish only the final product
   page to `cg_soa_products`.
2. Wait the exact product publisher completion before page-lane reuse. No
   16K `p[colidx]` intermediate and no
   `maa_indirect_load_virtual_index` occur on the treatment path.
3. After all four product pages are coherent, open one page-fed q RMW. Generate
   the four q-destination index pages with the existing range-loop cursor in
   page/lane ordinal order, wait each generated index tile ready, admit it,
   close, and execute one 16K Row/Offset schedule over q.

The treatment has no coherent q-index backing, no registered virtual-p backing,
no host SPD reads, no hidden payload, and no extra completion tile. Both arms
use four cores, four indirect units, 4K physical pages, one 16K q operation,
`NUM_TILES_PER_CORE=8`, and exactly 524,288 B of physical SPD payload. The
treatment's only external producer backing is the 262,144 B coherent product
array; the control additionally retains 262,144 B of virtual-p backing.

## Candidate-only runner

`experiments/scripts/run_cg_direct4_product_page_fed_q16.py` hard-codes the
following fail-closed experiment:

- one `CG_NA=1024`, four-thread deterministic-reduction guest;
- one shared checkpoint created before the deferred selector is read;
- frozen page-fed gem5 SHA-256
  `606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427`;
- frozen Ramulator SHA-256
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`;
- serial page-fed control followed by the direct4/q16 treatment;
- no native, medium, or full run, no timeout, and no per-access trace;
- exact raw and quantized fingerprint equality, all 11 deterministic FP
  reduction records, terminal/mechanism closure, and immutable source,
  checkpoint, and artifact ledgers before any `simTicks` result is emitted.

The inherited hardened coalescer closure is retained: value deliveries must
equal the logical selected-word count, but value issues are independently
closed against responses/fills and issues + hits + merged waiters. The runner
does not assume value issues equal deliveries.

The runner overrides the inherited 10-tile parser before calling the hardened
arm parser. Each resolved `config.ini` must contain exactly one
`num_tiles_per_core=8`; 10 is rejected. Both terminals must independently state
`physical_spd_payload_bytes=524288`.

## Live evidence: rejected pair

Raw root:
`/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-na1024-r1`

The durable service was
`dx100-cg-direct4-q16-na1024-20260826.service`. It used source commit
`bfc379261498a81ae0cce1d45260f04bece067d1`, whose parent chain is based
directly on diagnostic commit `51ec728d`; no concurrent product-overlap MAA
changes were merged.

Identity ledger:

- guest SHA-256:
  `2cc443c8d843ec63fbf76e6fc69697c256875e5afa270b70d3234109b2b9c4ff`;
- checkpoint-files ledger SHA-256:
  `b1aef227fd306067049de2dd0f6495655e6990b97cdd8e01195160f0fd828cc2`;
- control restore-log SHA-256:
  `55a642434f59ea3261233e9bd2af87d2d465171ba35975487f2e55951bafdd96`;
- treatment restore-log SHA-256:
  `c6ac960fc4a71fd0b9720ea7acd292664bae3b2394af7d130cd0e5dba16548dd`;
- wrapper exits: control `0`, treatment `-6`.

Both resolved configs contained exactly one `num_tiles_per_core=8`, page-fed
enabled, one MAA, four indirect units, 16K logical elements, and 4096 physical
elements. The treatment selection record additionally closed the intended
static ledger before execution:
`p_gather_mode=physical_4k_direct`, `virtual_p_backing_bytes=0`,
`p16_reorder_preserved=0`, `q16_reorder_preserved=1`,
`coherent_index_backing_bytes=0`, `host_payload_access=0`, and
`physical_spd_payload_bytes=524288`.

### Control acceptance

The control produced exactly 11 deterministic reduction records in required
order and a passing exact/quantized fingerprint. Its terminal closed:

- 65 full windows: 52 q SpMV and 13 residual SpMV;
- 65 virtual-p gathers and zero physical-p gather pages;
- 260 physical product ALUs, 260 product publisher terminals, 260 q-index
  admits, and 65 page-fed closes;
- 1,064,960 admitted index words and product words;
- zero coherent q-index backing and zero host payload access;
- 524,288 B physical SPD payload.

Fingerprint:
`x_raw=8513a33e8cad9f9e z_raw=59417f9f91294e19
x_q5=6438e193ca03f10a x_q6=9a5b269688cb4313
z_q5=38c02e8ec15b7aa8 z_q6=1caf0b6809305531`.

The runner parsed this arm successfully with the hardened instruction,
completion, selection/alias, value-delivery/coalescer, A-read/write, publisher,
page-fed, and no-fallback gates.

### Treatment rejection and correction

The treatment aborted at tick `661811791085`:

```text
I[0] page-fed page 1 tile 24 is not one unowned, completed physical-4K index page
```

Page 0 admission succeeded, but page 1 reached the doorbell while the newly
generated `t0` range-loop destination still had an instruction-file reference.
This is a dependency-order failure, not correctness or performance evidence.
The fail-closed runner stopped immediately; it did not write `result.json` or a
completion gate and did not inspect/report paired `simTicks`.

The post-rejection correction waits `t0` ready after each range loop and before
each q-index admit in both q and residual paths. It adds no tile, backing, or
payload and preserves exact page/lane ordinal order.

## Validation

Post-correction validation:

- focused direct4/q16 contract: 7/7 pass;
- inherited page-fed SoA contract: 9/9 pass;
- inherited deterministic-reduction runner contract: 21/21 pass;
- inherited small page-fed application contract: 5/5 pass;
- exact candidate compile with `CG_NA=1024`, four cores,
  `NUM_TILES_PER_CORE=8`, logical 16K / physical 4K, deterministic reductions,
  physical-product-only and page-fed-only macros: pass with `-Werror` and
  `-fsyntax-only`;
- `git diff --check`: pass.

## Conclusion

The narrow direct4-product/q16 architecture is implemented and statically
closed at eight tiles. Its intended tradeoff is explicit: p uses four serial
physical gathers and loses p-side 16K reorder, while q retains one 16K
Row/Offset reorder. The only live treatment observation was rejected before
correctness, the identified readiness defect is corrected, and no performance
claim is authorized without a future explicitly approved matched rerun of the
corrected source.
