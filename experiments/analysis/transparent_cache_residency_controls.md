# Transparent Consumer Cache-Residency Controls

`transparent_displaced_4k` originally combined a producer-completion wait and
a 32 MiB CPU cache walk with the transparent controller.  It is therefore not
by itself a cache-residency measurement: its `simTicks` also include work that
the baseline did not perform.

The workload now supplies the following matched cases.  All use the direct
virtual producer, a 16K logical tile split into four physical 4K pages, and
the same exact-output oracle.

| Case | Consumer start | 32 MiB CPU walk in ROI | Consumer |
|---|---|---:|---|
| `transparent_4k` | controller gates each ready page | no | one transparent controller instruction |
| `transparent_ready_4k` | after producer completion | no | one transparent controller instruction |
| `transparent_displaced_4k` | after producer completion | yes | one transparent controller instruction |
| `paged_4k` | after producer completion | no | four application page consumers |
| `paged_displaced_4k` | after producer completion | yes | four application page consumers |
| `transparent_reload_warm_4k` | after producer completion and stats reset | no | one transparent controller instruction |
| `transparent_reload_cold_4k` | after pollution and stats reset | no, excluded before reset | one transparent controller instruction |
| `paged_reload_warm_4k` | after producer completion and stats reset | no | four application page consumers |
| `paged_reload_cold_4k` | after pollution and stats reset | no, excluded before reset | four application page consumers |

Use these exact comparisons:

1. `transparent_ready_4k` versus `transparent_4k` measures the loss of
   producer/consumer overlap without cache pollution.
2. `transparent_displaced_4k` versus `transparent_ready_4k` is the combined
   cache-residency-plus-CPU-walk change for the transparent path.
3. `paged_displaced_4k` versus `paged_4k` is the same combined change for the
   non-transparent control path.
4. Subtract comparison 3 from comparison 2 (a difference in differences) to
   attribute only the transparent-specific sensitivity.  The equal 32 MiB
   walk is charged in both arms, so this result must not be described as a
   transparent-only CPU-pollution cost.
5. Compare each reload-only cold/warm pair to measure backing-residency
   sensitivity without producer or CPU-walk ticks. Compare transparent and
   paged deltas separately; one is controller-owned and one is application
   owned.

The case runner fail-closes each new case on its mode/layout/result line,
exactly one or zero pollution marker as specified, and, for every transparent
case, one submit, twelve issue/complete micro-operations, one retirement, and
the fixed fill/compute/store order.  No simulator behavior is changed here.
