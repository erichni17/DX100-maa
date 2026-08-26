# CG direct4 product / page-fed q16 full candidate — 2026-08-26

## Scope and current disposition

This milestone defines one candidate-only, trace-free, no-timeout full
`CG_NA=150000` observation for `direct4_product_page_fed_q16`. It does not
rerun native16, the physical-product predecessor, or the accepted full
page-fed control. The source base is `19223ae6`; the only comparison permitted
after a complete PASS is the accepted page-fed control's
`715,387,684,015 simTicks`.

The run is not official NAS verification and cannot support a native or
iso-area speedup claim. Its architectural statement is deliberately narrow:
q16 reorder is retained, while p16 reorder is lost in exchange for four direct
physical p gathers and final-product publication.

## Pinned correctness authority

The tolerant successor certificate root is
`/data1/nier/dx100-runs/2026-08-26-cg-page-fed-full-reclassification-r1`.
The runner verifies its manifest (`42ef48c…927ee6`), certificate
(`cd78f8f…90649a`), and gate (`8382a8b…1c47aaf`) byte-for-byte before and
after execution. That certificate pins the native16 log and stats and defines
the only accepted numerical criterion: a project-local PASS, finite x/z
vectors, and relative deltas no larger than `1e-8` for x/z sum and norm,
`1e-3` for rnorm, and `1e-10` for zeta. Raw and quantized equality are neither
required nor claimed.

The precomputed header is copied from the accepted full root and must remain
exactly 992,830,458 bytes with SHA-256 `f2b18716…dbe131`. The archived page-fed
gem5 and frozen Ramulator identities are `606eb920…f0427` and
`76ea3a9c…a15753`.

## Exact mechanism contract

The sole guest uses ordinary production reductions, `USE_DATA_FROM_FILE`, four
cores, eight tiles per core, a 16,384-element logical tile, and 4,096-element
physical pages. It selects only `direct4_product_page_fed_q16` after one
deferred checkpoint restore.

A passing terminal must report exactly 10,960 full windows: 8,768 q and 2,192
residual. It must close zero virtual-p gathers/backing, 43,840 physical-p
gather pages, 43,840 product publisher terminals, 43,840 q-index admissions,
10,960 q16 closes, 262,144 B external backing, and 524,288 B physical SPD
payload across eight tiles. Coherent q-index backing, host payload access,
fallbacks, drains, and open contexts must all be zero.

The first-ROI stats gate independently closes 11,223,040 product publisher
issues, accepts, and WriteResp lines. Value issues/responses/fills must be
positive and equal one another; deliveries must equal selected words through
the exact `issues + hits + merged_waiters` identity. The gate never equates
issues with selected words. SoA instruction/terminal, alias, page-fed, A-line,
and publisher closure must all hold before `result.json`, `gate.complete`, or
performance arithmetic can be emitted.

## Evidence state

Pre-launch implementation milestone only. No full candidate observation is
reported in this revision. The terminal classification, first-ROI simTicks,
and accepted-control-over-candidate ratio will be added only if the durable
candidate produces a complete numerical-and-mechanism PASS. Exactly one full
configuration observation will be reported.
