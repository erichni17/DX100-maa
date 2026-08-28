# Strict full-CG feeder pair (2026-08-28)

## Purpose

Run one trace-free, same-binary, same-checkpoint full-CG pair with feeder depth
1 versus 64. The accepted 250-GB one-line certificate remains the per-window
mechanism authority; this pair isolates full-workload feeder performance and
does not rerun native or direct4.

## Gate

- Reuse the immutable accepted full guest and treatment-neutral checkpoint.
- Preserve strict two-phase ordering, masked line retirement, value cache,
  four apply lanes, 16K logical Row/Offset, and 4K physical SPD.
- Require exact full terminal counts, frozen numerical tolerances, and all
  conserved work statistics from the accepted full certificate.
- Require resolved feeder depth in each `config.ini`.
- Forbid debug traces so the pair does not duplicate 500 GB of mechanism
  evidence already established by the accepted one-line certificate.
- Compute only feeder1/feeder64 performance; make no native claim.

## Terminal result

**Accept feeder64 as a faster candidate-only full-CG observation.**

| Arm | First-ROI `simTicks` | Relative result |
|---|---:|---:|
| Feeder1 | 160,746,544,242 | control |
| Feeder64 | 141,810,448,012 | 11.7801% lower, 1.13353x |

Both restores exit zero, close one ROI and one `m5_exit`, satisfy the frozen
full numerical tolerances, preserve 10,960 windows and all semantic work, and
resolve the intended feeder depths. No native, direct4, or trace run was
launched.

Feeder64 reduces strict B-fetch cycles 70.1653% and overlapping Row/Offset
cycles 68.6384%. The other phase counters move in the opposite direction:
A issue +1.0186%, backing +0.8613%, page +5.1186%, and consumer +0.5584%.
These counters overlap and must not be added to reconstruct `simTicks`.

Transport is schedule-dependent rather than semantic work. Feeder64 emits
1,022 additional masked P transactions out of about 147.6M (0.000693%); every
issue has a matching ACK, q writes remain exactly 57,491, and total strict
backing transactions reconcile as P plus q.

Evidence root:
`/data1/nier/dx100-runs/2026-08-28-cg-strict-feeder-full-pair-r1`.

- result SHA-256:
  `81810e1da449c87f3bda48c8417c9fea8af59c4c72cd58cd8ebc9813511ad114`;
- artifact ledger SHA-256:
  `b1b3fb799b6d7bc26961056c21f9a3a373b264c1cd362cece7c6562049b56d22`;
- gate SHA-256:
  `bc7972bf64903eee394c2977c289d2708d72d6d091d40738b19755b07a591c40`.

This validates full-CG feeder scaling for one deterministic pair. It does not
establish native4 performance, suite-wide benefit, variability, or synthesized
hardware cost.
