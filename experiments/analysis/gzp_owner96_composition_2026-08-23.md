# GZP owner96 composition result - 2026-08-23

## Decision

Retain 64 active value owners. Reject 96 and 128 owners as performance
optimizations for the selected full-GZP masked-index/pre-A configuration.

## Evidence

Raw result root:
`/data1/nier/dx100-runs/2026-08-23-gzp-owner96-composition-a68a2e21-r1`

All eight restores exit zero. Every arm passes the exact output hash
`11225737641199706160`, the 1,180,000-element UME reference, 61 terminal
windows, index hash `15605778284598092602`, and closed A/value/write ledgers.
The 96- and 128-owner endpoints have two deterministic replicas each. The
32- and 64-owner values reproduce earlier exact campaigns.

| arm | `simTicks` | adjacent result |
|---|---:|---:|
| owner32, pre-A off | 7,033,542,566 | baseline |
| owner32, pre-A on | 6,855,742,603 | 2.528% lower latency |
| owner64, pre-A on | **6,816,306,794** | another 0.575% lower |
| owner96, pre-A on | 6,839,368,008 | 0.338% regression |
| owner128, pre-A on | 6,835,912,488 | 0.051% below owner96, still 0.288% above owner64 |

## Interpretation

Additional owners continue reducing physical value reads: 822,961 at owner64,
784,053 at owner96, and 756,119 at owner128. That traffic reduction is not on
the critical path. The larger active prefixes alter request timing and increase
end-to-end latency, so owner count is not the remaining GZP bottleneck.

Keep the already provisioned 128-entry array for sensitivity experiments, but
activate only the first 64 entries in the selected design. The active prefix
adds no hardware relative to the existing provision; this result does not
justify provisioning beyond 128 entries.
