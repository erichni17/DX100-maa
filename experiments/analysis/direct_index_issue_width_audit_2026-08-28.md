# Direct-index request-generation width audit (2026-08-28)

## Finding

The current feeder can enqueue multiple sequential B cache-line requests in
one `fillDirectIndexWindow()` call. At selected depth64, the first 64 lines of
every strict NA1024 window are created at one `curTick`. Cache/DRAM routing and
responses remain timed, but request address generation and feeder allocation
do not currently consume an explicit per-line cycle.

This is a real hardware-modeling gap, but an offline serialization bound shows
that it cannot explain the measured 43.5698% one-to-64 speedup by itself.

## Trace evidence

The exact ACK-identity selected trace contains 65 operations and 66,560 B-line
issues:

| Depth | Issue events | Unique enqueue ticks | Maximum same-tick burst |
|---:|---:|---:|---:|
| 1 | 66,560 | 66,560 | 1 |
| 8 | 66,560 | 64,343 | 8 |
| 64 | 66,560 | 44,945 | 64 |

All 65 depth64 operations begin with a 64-line burst. Across the whole trace,
most later refills are already spread over time; mean occupancy is 1.481 issue
events per enqueue tick.

## Conservative serialization bound

For each same-tick group, charge `ceil(group / width) - 1` additional issue
cycles and pessimistically place every added cycle on the critical path. At a
3.2-GHz MAA clock (312.5 gem5 ticks/cycle):

| Generation width | Added cycles | Added ticks | Share of 1,249,282,534 ticks |
|---:|---:|---:|---:|
| 1 line/cycle | 21,615 | 6,754,688 | 0.5407% |
| 2 lines/cycle | 9,097 | 2,842,812 | 0.2276% |
| 4 lines/cycle | 4,353 | 1,360,312 | 0.1089% |
| 8 lines/cycle | 2,020 | 631,250 | 0.0505% |

This is not a replacement for timing integration: delayed creation can alter
memory scheduling and refill timing. It establishes only that zero-cycle queue
creation has insufficient direct cycle mass to account for the observed gain.

## Required closure

The fixed feeder must expose a finite line-generation/allocation width and run
the selected depth64 micro at widths 1/2/4 or a defensible fixed width. Accept
64 as hardware-realistic only if exact work and correctness remain fixed and
the measured gain survives. No additional full workload is authorized for
this question.
