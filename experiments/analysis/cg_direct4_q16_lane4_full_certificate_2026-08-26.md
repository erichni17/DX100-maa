# Full direct4/q16 cache-on lane-4 certificate (2026-08-26)

## Decision

**ACCEPT the lane-4 full candidate through its read-only successor
certificate.** The original service exit is not itself a passing wrapper: the
guest and restore completed correctly, but an overstrict post-restore check
required every sparse operation to reach four-lane occupancy. The successor
replays the complete raw evidence with the corrected bounded condition.

Raw root:

`/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-lane4-full-r1`

Certificate root:

`/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-lane4-full-certificate-r1`

The certificate and explicit `--validate` both report
`PASS_NUMERICAL_MECHANISM_CORRECT`. Its manifest/certificate/input hashes are
`432de99c...ca4d`, `49c827e6...901c`, and `fe3d5109...8bde`. It launches zero
gem5 runs and leaves the historical raw root unchanged.

## Result

| Arm | First-ROI `simTicks` |
|---|---:|
| Accepted cache-on lane 1 | 123,968,991,971 |
| Cache-on lane 4 | 111,116,739,967 |

Lane 4 is 1.115664409x faster, saving 12,852,252,004 ticks or 10.3673% of
lane-1 latency. Both values are one deterministic full observation.

The candidate passes the full numerical tolerance and closes exactly 10,960
SoA/JIT instructions, 179,568,640 selected aliases/value deliveries, 57,491 A
reads/writes, 11,223,040 product publisher WriteResps, 43,840 page admissions,
10,960 q16 terminals, and zero drains/fallbacks/host payload/q-index backing.
It retains `p16=false`, `q16=true`, 524,288 B physical SPD, and 262,144 B
product backing.

Configured active lanes close at `43,840 = 4 * 10,960`. Aggregate observed
high-water is 43,242, satisfying
`3 * instructions < high_water <= 4 * instructions`. This proves real
four-lane use while allowing sparse rows that cannot occupy all four lanes.
All 16 lane owners and the 576-byte compiled lane-pool state are already fixed
in both arms; the lane-4 selection adds zero payload bytes, control bytes, or
ports relative to lane 1.

## Boundary

For orientation, historical native4/native16 full endpoints are
77,075,327,902 / 58,928,150,676 ticks. Lane 4 remains 1.4417x / 1.8856x
slower. It is 57.2375% lower latency than the older 259,846,205,097-tick fully
bounded4 design. These are historical end-to-end observations, not native
reruns, native-speedup claims, variability estimates, or iso-area results.
