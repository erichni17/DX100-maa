# Typed logical SPD live evidence (2026-08-23)

The bounded logical SPD cache now completes in production gem5 for FP32 and
FP64. This closes the live bridge gap left by the type-generic controller
change: backing spans, transport geometry, scalar capture, and trace fields are
derived from the decoded datatype.

| Type / organization | `simTicks` | Exact result | Lifecycle |
|---|---:|---|---|
| FP64 Serial4K | 424,572,606 | hash `7303085050985348899`, zero errors | one admit / one complete |
| FP32 Serial4K | 489,806,188 | hash `6880529560763119881`, zero errors | one admit / one complete |
| FP32 PingPong2K | 489,727,312 | hash `6880529560763119881`, zero errors | one admit / one complete |

Every checkpoint and restore exited zero, each restore has one clean `m5_exit`,
and each trace records the expected datatype, word size, page geometry, active
payload bytes, and terminal completion.

Raw roots:

- `/data1/nier/dx100-runs/2026-08-23-logical-spd-fp64-serial4k-bd5625d1-r4`
- `/data1/nier/dx100-runs/2026-08-23-logical-spd-fp32-serial4k-bd5625d1-r4`
- `/data1/nier/dx100-runs/2026-08-23-logical-spd-fp32-pingpong2k-bd5625d1-r4`

This is functionality evidence, not an architectural speedup result. The
logical cache still uses its existing private payload model, its compute stage
is not yet the final native-ALU page scheduler, and the three datatype/mode
timings perform different byte volumes. Native16/native4 controls were not
rerun.
