# Paired page-fed CG schedule diagnosis (2026-08-25)

This successor investigates the rejected full-CG page-fed fingerprint without
rerunning full or native work.  `run_cg_page_fed_schedule_diagnosis.py` builds
one generic page-fed-capable guest for each `CG_NA`, checkpoints it before the
deferred selector is consumed, then restores the same checkpoint once with
`physical_page_product_soa_jit` and once with `page_fed_product_soa_jit`.

The sequence begins with control `CG_NA=1024`, followed by bounded medium
sizes only while needed for causal localization. The arms share guest, binary
hash, input layout, checkpoint digest, core/cache/RowTable geometry, frozen
page-fed gem5, and frozen Ramulator. The only intentional restore deltas are
the read-only selector and page-fed enable switch.

Instrumentation is compact: `MAAIssueDigest` records per-instruction source
order hashes and `MAAMacroEvent` records producer, RowTable-admission, and
A-line closure. `MAAReorderTrace` is intentionally excluded: the archived
binary aborts in its optional tracker on a page-fed response. Epoch/drain
counts are instead checked from final stats. No general virtual trace or
per-issue trace is enabled. The raw root retains both logs and final stats;
`diagnosis.json` is a stable projection covering source digests, RowTable
admission, alias/A-line closure, product publication/value delivery, epoch
drains, exact terminal outputs, and fingerprints.

The runner fails closed on timeout-free process failure, missing final stats,
non-PASS terminal/fingerprint, missing compact digest evidence, a changed
source tree, or artifact hash mismatch.  It is diagnostic only and does not
claim a performance result.

## Live result

The initial r1 raw root stopped before simulation because checkpoint gem5 did
not inherit the frozen Ramulator search path.  It is preserved, excluded, and
was corrected before r2.  r2 completed both processes but is also excluded:
the checkpoint stores the selector *pathname*, so passing a second pathname to
the restore command left both arms at `physical_page_product_soa_jit`.  The
successor rewrites only the treatment text at that one checkpointed pathname
between the serial arms.  This is the intentional treatment delta, not a
checkpoint/input mismatch.

r3 verifies that the same-path rewrite selects page-fed, but is excluded after
the archived binary aborts in the optional `MAAReorderTrace` bookkeeping
(`recordReorderSurvivalIssuedEntries`) before a terminal result. The successor
uses no `MAAReorderTrace`; this retains compact source and macro digests while
avoiding a debugger-only correctness failure.

Pending the corrected bounded pair. Record only the first divergent size and
its raw-root hash here; do not infer a stage cause from aggregate closure alone.

## NA=1024 corrected control (r4)

Raw root:
`/data1/nier/dx100-runs/2026-08-25-cg-page-fed-schedule-diagnosis-1024-r4`.
The exact shared checkpoint digest is
`0dbd6b6d7c2a12e88b7b8e440fd6b3ec1c95944f0a2a6d240d479e97cdda6178` and
the guest digest is
`becbd7a803cbbc4169c8fe8e423c71d43156478f16b98cfcec6b066f04e5601b`.

Both arms terminate PASS with exact identical q5/q6 x/z fingerprints. After
excluding instruction ticks, every source issue count/FNV/mix digest matches;
the timing of those same issues differs. RowTable admission projections,
destination alias counts (1,064,960), A-line issue/read/write closure (375),
and epoch drains (zero) match. Value issue/response delivery also matches.

The raw publisher totals deliberately differ: physical emits 133,120 lines
for 260 index plus 260 product pages, while page-fed emits 66,560 final-product
lines for 260 pages. Each arm's issue/accept/response counts close exactly to
its own required publication volume, so this treatment-specific eliminated
index publication is not a fingerprint-cause at NA=1024.

The NA=4096 successor consumes r4 as `--prior-control`, preserving the
accepted control without rerunning it.

## NA=4096 terminal matched diagnosis (r1)

Raw root:
`/data1/nier/dx100-runs/2026-08-25-cg-page-fed-schedule-diagnosis-4096-r1`.
Its raw-root ledger digest is
`fc068de27495e7fe830c1033e06d542b1a08995c130929a83608dbd49c5585c0`.
The shared checkpoint and guest digests are respectively
`b8028a25159cb4c20984c2ea7bd1a23c53a521a1cf893f77954f390b48d0a0f5` and
`c3b8a4a02bfe887f24112daec865a565ad17b209489186e8b0887d981ee3b568`.

This is terminal and sufficient: do not launch CG_NA=16384. Both arms pass
and have the same quantized fingerprints:
`x_q5=e03cba68dff80802`, `x_q6=8458d6396eee1e7c`,
`z_q5=91cfe451e93d2650`, and `z_q6=c99085e428243502`.

The raw fingerprints do differ. Physical reports
`x_raw=1d9819aeded94804 z_raw=1bc2927ed159875d`; page-fed reports
`x_raw=225873f272124c14 z_raw=36e3b0c8d5f3c391`. Thus this gate accepts the
predeclared quantized criterion, not raw-bit identity, and this result must
not be represented as raw FP32 equality.

Normalized source-order count/FNV/mix digests match after excluding issue
ticks; issue timing itself differs. RowTable-admission projections, value
issue/response delivery (4,751,289 each), destination-alias count
(4,751,360), A-line issue/read/write closure (1,555 each), and zero epoch
drains/fallbacks also match. Physical publishes 593,920 lines and 2,320
terminals (index plus product); page-fed publishes 296,960 lines and 1,160
terminals (product only). Each arm closes issue/accept/response exactly, so
page-fed halves this publication traffic without breaking the matched closure.

`simTicks` is 29,867,173,640 physical versus 25,058,955,593 page-fed:
physical/page-fed = 1.191876234792x. This is a diagnostic simulated-time observation
only, not a promoted performance claim: the raw FP32 bits differ.

The next causal intervention is deterministic reduction-order CG_NA=4096.
It should isolate the timing-sensitive FP32 reduction critical section while
retaining the established matched selector/checkpoint and mechanism closures.
