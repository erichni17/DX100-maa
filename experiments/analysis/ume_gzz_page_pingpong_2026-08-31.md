# UME GZZ page ping-pong result

Scope: deterministic reduced-input GZZ (`n=16,384`, 196,384 padded outputs),
not the separate 1M-element full-scale campaign.

## Decision

Accept the default-off eighth-tile ping-pong candidate as a small GZZ
optimization. It is exact and 0.811% lower latency than the sealed seven-tile
strict control.

| Arm | `simTicks` | Partial writes | Transport bytes |
|---|---:|---:|---:|
| seven-tile strict control | 25,470,375 | 26 | 66,368 |
| eight-tile ping-pong | 25,263,795 | 4 | 65,664 |

- Speedup over strict control: 1.00818x
- Latency reduction versus native4: 15.095% (1.17779x speedup)
- Slowdown versus native16: 22.957%
- Native4-to-native16 latency gap recovered: 48.776%
- Pressure-spill writes reduced 84.615%
- Exact output hash: `7602200327591349891`
- Evidence: `/data1/nier/dx100-runs/2026-08-31-ume-gzz-page-pingpong-r1`
- Simulator SHA-256:
  `cd36ea5acd0ee660ae66ba384cdef0acad265d48acc73e62bd2b13a2f161b8d0`

The current-source seven-tile control replay at
`/data1/nier/dx100-runs/2026-08-31-ume-gzz-bitmap-diag-r2` uses the same
simulator hash and exactly reproduces 25,470,375 ticks and all r6 counters.
Thus the ping-pong comparison is same-simulator-binary despite using a fresh
guest/checkpoint for the default-off guest macro.

The control and candidate both configure eight physical tiles per core. The
control uses seven; the candidate uses the eighth as an alternate virtual-page
destination. This changes utilization but not configured tile capacity.

## Attribution

The measured win is not the originally hypothesized reduction in stream/SPD
write time:

| Counter (MAA cycles) | Control | Ping-pong | Delta |
|---|---:|---:|---:|
| total | 81,375 | 80,715 | -660 |
| busy | 73,331 | 72,757 | -574 |
| idle | 8,044 | 7,958 | -86 |
| stream SPD-write access | 21,865 | 21,965 | +100 |
| strict A-issue interval | 7,154 | 6,503 | -651 |
| strict backing interval | 6,817 | 6,130 | -687 |
| strict consumer interval | 22,952 | 22,708 | -244 |

Alternating page destinations changes scheduling and greatly reduces output
fragmentation: only two lines require pressure spill and completion fragments,
versus 13 in the control. The A/backing intervals shrink, while the dominant
stream SPD-write counter does not. The next optimization still needs to target
materialization/stream serialization directly rather than assuming this
ping-pong implementation already overlaps it.
