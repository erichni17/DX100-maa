# SSSP exact unsafe-reason coverage closure

## Decision

**ACCEPT the counter and validation closure; retain the existing no-launch and
no-performance-promotion decisions.** This successor closes the residual gaps
identified by `sssp_chunk_promotion_review_2026-09-01.md` after milestones
`5fbaa33e`, `08f069cd`, and `13de5123`. It changes admission accounting and
fail-closed evidence validation, not the routed or coherent-fallback datapath.

## Closed contracts

- The guest counts every unsafe eligible window exactly once in
  `reason_covered_unsafe_windows` when any tracker reason bit is present. Its
  terminal `counts_close` requires exact equality with
  `unsafe_eligible_windows`.
- Bounds, active-source, and cross-owner counts remain independent bit tallies.
  They can overlap and are never summed to infer coverage.
- The small and full runners require the new terminal field. The full runner
  and hardened completion audit require exact equality and exact
  `fallback_pages == unsafe_eligible_windows * 4` conservation. Consequently,
  a legal all-routed execution with zero full-page fallback is accepted.
- When the aperture candidate gate is enabled, boundary-drop and out-of-range
  rejection statistics must exist. Missing statistics no longer become an
  optional zero. The small mechanism gate applies the same fail-closed rule.
- The full manifest and completion audit retain the accepted external-storage
  disclosure: 1,048,576 bytes of coherent backing, 37,748,736 bytes of dense
  admission metadata, a 1,024-byte maximum external tracker allocation, zero
  accelerator SRAM for admission metadata, and externally allocated bounded
  winner maps.

## Predictor disposition

The host predictor now emits schema 2. Every iteration row and the total emit
`reason_covered_unsafe_windows` plus `counts_close`; generation aborts if either
routed/unsafe closure or exact reason coverage fails.

The regenerated frozen S22 ledger remains a **NO LAUNCH** result:

| Count | Total |
| --- | ---: |
| Eligible windows | 7,232 |
| Routed windows | 0 |
| Unsafe eligible windows | 7,232 |
| Reason-covered unsafe windows | 7,232 |
| Active-source-rejected windows | 7,232 |
| Cross-owner-rejected windows | 7,232 |

The two reason-specific totals overlap on every unsafe window; their sum is
not a coverage count. The machine ledger has SHA-256
`2ae007490d74ff91768f6864e9eb7db97d7452d0c76ddb4b6bff59ffb33554a4`.

## Validation

- `tests/maa/run_sssp_chunk_admission_unit.sh`: optimized and ASan/UBSan PASS.
- `experiments.tests.test_predict_sssp_chunk_admission`: 6/6 PASS, including
  an active-source plus cross-owner overlap fixture.
- Small/full runner contracts: 26/26 PASS.
- Hardened goal-completion audit: 20/20 PASS, including uncovered-window
  rejection, overlapping reasons, all-routed/no-full-fallback acceptance, and
  external metadata disclosure rejection.
- Coherent fallback contracts: 9/9 PASS.
- The GEM5/MAA SSSP guest compiled with `-Werror` under the selected
  16K-logical/4K-physical configuration.
- Both runners pass `bash -n`; `git diff --check` passes.

No gem5 run was launched. The production change adds terminal counters and
stronger counter closure only, so the objective's conditional small-rerun
threshold was not met. Existing small gem5 correctness evidence is not
retroactively relabeled as containing the new field.
