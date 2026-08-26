# CG page-fed reduction-order diagnosis (2026-08-25)

## Scope and status

This is the bounded successor to static audit `e03ecc1d` at base
`e6373c9f`. It adds a diagnostic build mode and a matched shared-checkpoint
runner; it does not alter the ordinary CG build. No gem5 process, native arm,
or full-CG input was launched while preparing or validating this change. The
already active `CG_NA=4096` schedule worker was not touched.

## Deterministic guest mode

`CG_DETERMINISTIC_REDUCTIONS` is restricted at compile time and runtime to four
OpenMP threads. In `conj_grad_maa`, each initial `rho`, per-CG-iteration `d`,
per-CG-iteration `rho`, and final `sum` contributor writes one FP32 partial by
thread ID. The reduction-contributing tail loop uses `schedule(static) nowait`
only in this mode, then all threads meet a barrier and thread 0 accumulates
partials in exact `0,1,2,3` order. Every pre-existing post-reduction barrier
remains between the destination write and its first consumer. The existing
critical section remains only around MAA tile/register allocation.

The outer inverse-power `x·z` and `z·z` FP64 pair uses the same contract:
static element ownership, one pair of partials per thread, a pre-combine
barrier, ordered combination by thread 0, and a post-combine barrier before
normalization or zeta consumption. Ordinary builds retain their original
dynamic tails, critical additions, and OpenMP pair reduction.

`CG_REDUCTION_EVIDENCE` is separately opt-in and requires deterministic mode.
It emits no memory-access trace. One CG call emits ten compact FP32 records
(partial/result bits plus alpha or beta bits when applicable) and one FP64
record (both partial vectors, both results, normalization-scale bits, and zeta
bits).

## Matched runner contract

`experiments/scripts/run_cg_page_fed_reduction_order_diagnosis.py` requires an
explicit `--cg-na` in `1..32768`; the bound prohibits the full `CG_NA=150000`
case. It builds one generic deterministic/evidence guest with 16K logical and
Offset geometry, 4K physical pages, and ten guest tiles. It creates one
checkpoint before the deferred selector is read, then rewrites only that same
selector pathname for serial restores of:

1. `physical_page_product_soa_jit`
2. `page_fed_product_soa_jit`

Both arms use the frozen page-fed gem5
`606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427`
and Ramulator
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`,
two memory channels, 32 initial RowTable slices, 16K Offset tables, four
indirect units, and no debug trace flags or wall timeout.

Each arm must have a zero wrapper return code, one ROI terminal, one exact
`m5_exit`, one passing treatment terminal, one passing fingerprint, the exact
eleven-record reduction-evidence shape, nonempty final stats, resolved config
closure, publication/A-line closure, and zero fallback/epoch drains. Value
traffic must have positive matched issue/response/fill totals, cached responses
no greater than fills, and issue + ready-cache hit + merged-waiter totals equal
selected values. Logical value delivery and aliases must each equal selected
values; a physical value-read issue is never treated as one logical delivery.
This permits legal cache/coalescer reuse while rejecting missing delivery.
Checkpoint, immutable-artifact, source-status, and source-commit ledgers must
match before/after. The immutable ledger covers the compiled guest, its direct
API/ABI inputs, and the runner/config modules that construct the treatment.
Only after those checks does the runner seal `result.json`, `raw_root.sha256`,
and `gate.complete`. A cross-arm bit difference is archived as `outcome=DIFFER`,
not discarded as a process failure.

## Interpretation

- Equal FP32/FP64 partial records and equal final fingerprints at a selected
  `CG_NA` support the timing-dependent reduction-order explanation for the
  prior mismatch at that size. This diagnostic configuration is not
  performance evidence.
- A first partial-bit difference means the treatments diverged before the
  ordered combine. Static tail ownership rules out timing-dependent remainder
  assignment, localizing the next audit to upstream q/r/z values.
- Equal partials but unequal result/downstream bits would violate the ordered
  reduction contract and is treated as an implementation failure.
- Equal reduction evidence but unequal final fingerprints falsifies reduction
  order as a sufficient explanation and redirects the investigation to later
  treatment-specific semantics.

## Static validation

The focused contract suite contains 20 tests. It adversarially checks the four
FP32 tail guards, both ordered reducers, post-combine barriers before every
consumer, preservation of ordinary reductions, compact evidence shapes, the
single-guest/single-checkpoint runner, frozen geometry, non-full/no-native/no-
timeout constraints, exact terminal/stat closure, legal cache/coalescer reuse,
missing delivery, and before/after artifact sealing. C++ syntax checks pass
with ordinary flags, deterministic-only flags,
and deterministic-plus-evidence flags; deterministic-plus-evidence also
compiles to an object. The focused and adjacent static CG suite passes 76
tests. No simulator validation has been run; launch remains explicitly
deferred.
