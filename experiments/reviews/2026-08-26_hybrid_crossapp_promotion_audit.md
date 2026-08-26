# Fail-closed selected-hybrid promotion audit — 2026-08-26

## Verdict

**Do not promote the selected 16K-logical/4K-physical hybrid as a
cross-workload performance result.** The sealed completion audit at
`/data1/nier/dx100-runs/2026-08-26-hybrid-goal-audit-r2/audit.json` is
`INCOMPLETE`: IS, HashJoin PRO, and HashJoin PRH pass their correctness
requirements, while the selected full CG-direct4 candidate and full SSSP run
are pending. Pending is not failure, but it supplies no correctness or timing
claim.

All accepted configurations below use 16,384 logical elements, 4,096 physical
elements, eight tiles/core, four cores, and therefore 524,288 B physical SPD
payload. This is a capacity accounting statement, not synthesized area or an
iso-area result. Coherent backing is external capacity and must not be counted
as SPD SRAM.

## Raw-evidence disposition

| Workload | Raw evidence rechecked | What passes | Allowed claim | Not allowed |
| --- | --- | --- | --- | --- |
| CG direct4/q16 | `.../2026-08-26-cg-direct4-product-page-fed-q16-na1024-r2` and `...-na4096-r1`; both gates say `correctness=EXACT_MATCH` and their raw SHA-256 ledgers are present | Exact, shared-checkpoint bounded pairs | At NA=1024, control/direct4 is 1.405585308x; at NA=4096 it is 1.388065785543x. The treatment removes 262,144 B virtual-p backing and uses four physical p pages while retaining q-side 16K reorder. | Full-CG, native-speedup, iso-area, or general-workload claim. It deliberately loses p-side 16K reorder. |
| CG page-fed predecessor | `.../2026-08-26-cg-page-fed-full-reclassification-r1`; gate/hash bindings recompute exactly | Numerical-tolerance plus mechanism closure, not bit/quantized equality and not official NAS verification | Separate predecessor observation: 715,387,684,015 versus 818,687,246,165 simTicks (1.144396618x predecessor/candidate); still 12.139998894x slower than native16. | Relabeling it as direct4/q16, exact, native speedup, or iso-area. One observation/arm is not a variability estimate. |
| NAS IS | Raw root `.../2026-08-24-is-scalar-soa-full-a44aaa60-r5` and successor certificate `.../2026-08-26-is-scalar-soa-full-certificate-r1` | `terminal.status=PASS`, NAS verification, one ROI end and m5 exit, 2,048 terminals, 33,554,432 selected/index words, balanced A traffic; certificate says `PASS_FULL_IS_CORRECTNESS` | Full candidate correctness under scalar-SoA treatment, 524,288 B physical payload and zero staging payload. `379,831,843,258` simTicks is provenance only. | Any performance comparison or speedup: the certificate records `performance_promoted=false` and no native rerun. |
| HashJoin PRO | `.../2026-08-24-hashjoin-pro-hardened-r1` | `gate.complete`, result ledger (`sha256sum -c`: all entries OK), cardinality 2,000,000, 240/240 first-pass routed windows, shifted pass not applicable, m5 exit | Full candidate correctness/mechanism coverage; 16K logical/4K physical geometry. | Performance promotion: candidate-only, no matched baseline. |
| HashJoin PRH | `.../2026-08-24-hashjoin-prh-hardened-r1` | `gate.complete`, result ledger (`sha256sum -c`: all entries OK), cardinality 2,000,000, 240/240 first-pass routed windows, m5 exit | Full candidate correctness; shifted pass is explicitly **tail-only** (0/0 routed windows and 1,024 bounded 4K tail actions). | Claiming routed shifted-pass coverage, or a performance comparison without a matched baseline. |
| GAPBS SSSP | `.../2026-08-25-sssp-coherent-full-s22-r2` | Checkpoint artifacts and active restore artifacts exist; configured 16K logical/4K physical, 8 tiles/core, 32 RowTable slices | None for full S22 yet. The smaller coherent-fallback and routed-path gates remain separate correctness evidence only. | Treating absence of `gate.complete`, terminal record, final fingerprint/closure, or final stats as failure or success; any full-S22 performance/correctness claim. |
| Selected full CG direct4/q16 | `.../2026-08-26-cg-direct4-product-page-fed-q16-full-r2` | Manifest fixes one candidate-only restore, 16K logical/4K physical geometry, 524,288 B physical payload, 262,144 B external coherent product backing, and the frozen full input | None until its terminal numerical, mechanism, provenance, and immutability gates pass. | Using the page-fed predecessor certificate as its result, or reporting its partial/absent stats as timing. |

## Configuration and storage boundary

The selected direct4/q16 manifest fixes four cores, four indirect units, 8
tiles/core, 16K logical Offset scope, 16K offset epochs, 32 initial RowTable
slices, and 4K physical tiles. Its 524,288 B SPD calculation is
`4 * 8 * 4096 * 4`. The treatment's 262,144 B coherent product backing is
external; the serial page-fed control additionally has 262,144 B virtual-p
backing (524,288 B external total). The selected treatment has no virtual-p
backing or coherent q-index backing. No source establishes physical timing,
synthesized area, reinvestment of saved backing, or an iso-area comparison.

For IS, the certificate records 524,288 B physical payload and zero staging.
The HashJoin manifests independently bind the same logical/physical geometry.
Those workload-specific accounting records do not prove that all unmeasured
applications have no external backing or no additional timing cost.

## Promotion blockers and required next evidence

1. The selected direct4/q16 full CG root must finish with its own terminal
   gate, exact provenance/ledger validation, numerical criterion, mechanism
   closure, and explicitly bounded interpretation. Its page-fed control is not
   a direct4 result.
2. The full SSSP root must finish its independent exact fingerprint,
   fallback-publication closure, artifact ledger, final stats, and wrapper
   gate. Until then it remains **pending**, not failed.
3. IS and both HashJoin roots need matched, comparable baseline arms (same
   input/checkpoint and non-treatment configuration) before any performance
   claim. PRH also needs a workload with a routed shifted-pass window before
   claiming that mode is covered.
4. Generality requires completed, comparable full-application evidence for at
   least two applications; the present portfolio has none for the selected
   treatment. Repetitions are additionally needed for a spread/noise claim.

The only currently reportable performance numbers are the two bounded CG
direct4/q16 shared-checkpoint ratios and the separately named one-observation
page-fed-predecessor ratio above. They must retain their treatment, baseline,
input-size, and non-native/non-iso-area qualifiers.
