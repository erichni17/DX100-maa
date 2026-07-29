# Fused Index-Prefetch Diagnostic

The `direct4fusedprefetch` instruction atomically creates a cache-only stream
prefetch for B and a direct-index gather. The gather may start when the stream is
in service rather than waiting for it to finish. Duplicate in-flight B reads can
coalesce in the MAA port.

FLAG `static_2d/001.fp/config_00_gather.json` used the same logical range,
physical capacity, Row-Table geometry, output verifier, and native A issue order
as the prior `direct4` control.

| Arm | ROI `simTicks` | Result |
|---|---:|---:|
| `direct4` | 36,662,629 | control |
| `direct4fusedprefetch` | 36,444,155 | 0.596% lower latency |
| `compact16` | 35,124,860 | fused prefetch is 3.756% slower |

The fused arm produced exact output hash `17529267342572166465`, two statistics
blocks, normal `m5_exit`, 3,995 issued/completed retirement writes, 31,923 B
words, and zero indirect SPD reads. It recorded 216 in-flight B-read merges and
zero outstanding-wait cycles. Its two A-request digests strictly matched the
`direct4` control across 12,297 source requests.

The mechanism is real but the gain is too small to promote. Existing FLAG depth
data also show identical ticks at 128 and 256 feeder lines, so simply increasing
B lookahead is already saturated. A shared producer/consumer feeder could remove
duplicate requests, but this result does not justify that implementation before
profiling the remaining direct-versus-compact gap.

Evidence root:
`/data1/nier/dx100-runs/2026-07-29-flag00-fused-index-prefetch-481eeac-2g`.
