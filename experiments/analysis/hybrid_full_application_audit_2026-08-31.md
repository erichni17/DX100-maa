# Hybrid full-application audit checkpoint

The read-only completion auditor was rerun after the full SSSP S22 process
terminated.  It launched no simulations and returned `INCOMPLETE` with exactly
one pending application.

| Application | Audit status | Authority |
|---|---|---|
| NAS CG | passed | tolerant full certificate plus direct4/q16 full certificate |
| NAS IS | passed | official full verification certificate |
| HashJoin PRO | passed | hardened exact 2M full result |
| HashJoin PRH | passed | hardened exact 2M full result |
| GAPBS SSSP S22 | pending | completed candidate lacks `gate.complete` |

SSSP reached one `m5_exit` and reproduced the exact frozen 4,194,304-vertex
fingerprint. Its terminal mechanism record also closes 31,492 coherent
fallback pages, 94,476 publication issues/responses, zero host-SPD reads, and
zero illegal aperture accesses. It is nevertheless rejected as hybrid-route
evidence because all 7,226 eligible logical windows used fallback and
`routed_windows=0`. The first-ROI candidate time is 10,819,081,747,253 ticks
versus the frozen native16 758,524,789,379 ticks; no performance promotion is
made.

Audit root:
`/data1/nier/dx100-runs/2026-08-31-hybrid-goal-audit-terminal-sssp-r1`

- `audit.json` SHA-256:
  `644194dea7c838a72834314768d5dc45528b8ac352c14eee40c1be3f7e054680`
- `input_sha256.txt` SHA-256:
  `ccf7edf941a4541c82c9fba3f0856936528cd7e4fa4ab3a63a3a9eca0eba6710`

The remaining full-application goal is therefore narrow: diagnose and repair
SSSP's zero routed-window coverage with micro/small gates before considering
another full S22 run. Valid native baselines and the four passed application
certificates must not be rerun.

## 2026-09-01 successor checkpoint

The hardened auditor was rerun at
`/data1/nier/dx100-runs/2026-09-01-hybrid-goal-audit-preconflict-r1`.
CG, IS, HashJoin PRO, and HashJoin PRH still pass; SSSP remains the only pending
application, and no terminal goal gate was written. `audit.json` SHA-256 is
`47436ed98a1b3856e9ae340bd17f36d17999292bce7c4e9d5ac0c302f975dcbb`.

Small per-chunk SSSP gates now pass exact 4/4 safe, 3/4 active-source, and 2/4
cross-owner routing. They do not justify a full run: the independently
validated host predictor reproduces those cases but predicts 0/7,232 routed
windows on frozen S22 because every eligible chunk carries both data-hazard
reasons. The current decision is therefore **NO LAUNCH** pending a
conflict-tolerant mechanism; the six-day routed-zero run must not be repeated.
