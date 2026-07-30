# Bounded-Window Follow-up Experiments

These experiments profile the remaining gap after both Row and Offset state
were bounded to a 4K epoch. They use exact output checks and simulated ROI
ticks; host time is not used as an architecture metric.

## B feeder depth

On FLAG `static_2d/001.fp_00_gather`, the selected 128-line feeder took
37,737,471 ticks. Increasing depth did not produce a useful tradeoff:

| B lines | Payload | ROI ticks | Delta vs. 128 |
|---:|---:|---:|---:|
| 64 | 4 KiB | 38,326,224 | +1.560% |
| 96 | 6 KiB | 37,928,088 | +0.505% |
| 128 | 8 KiB | 37,737,471 | +0.000% |
| 160 | 10 KiB | 37,811,026 | +0.195% |
| 192 | 12 KiB | 37,698,033 | -0.105% |
| 224 | 14 KiB | 37,772,214 | +0.092% |
| 256 | 16 KiB | 37,920,889 | +0.486% |

The 256-line arm reduced idle cycles but increased source-only service time.
The 192-line arm's 0.105% improvement costs another 4 KiB and is too small to
justify promotion from one deterministic observation. The 128-line point
remains the capacity/performance knee.

## Descriptor epoch size

The same FLAG case was run with matched 2K Offset capacity and epoch. It took
39,060,209 ticks, 3.505% more than the fully bounded 4K point. A 2K-capacity
arm and a 4K-capacity control at the same 2K epoch were bit-for-bit equivalent,
again showing that storage above the live epoch is unused. The smaller epoch
nearly eliminated refill-idle time but increased source-flight cycles from
67,228 to 78,236, so a simple 2K ping-pong schedule does not hide the critical
path.

An 8K Row/Offset window took 37,870,496 ticks, 0.353% more than 4K while
roughly doubling descriptor storage. It is dominated for this case.

## Remaining gap

For the representative FLAG case, `compact16` took 35,124,860 ticks, the first
full-descriptor direct4 design took 36,662,629 ticks, and the fully bounded 4K
design took 37,737,471 ticks. The current design is therefore 7.44% slower than
`compact16` and 2.93% slower than full-descriptor direct4 on this case.

Trace accounting identifies B ingestion and source service, rather than empty
refill bubbles, as the dominant remaining difference. A refill spans roughly
1,100 MAA cycles. The current run spent 67,228 cycles with source requests in
flight, 5,316 runnable/idle cycles, 7,496 source-only cycles, and 59,732 cycles
with source work overlapping other work. Making epochs smaller reduced idle
time but lengthened source flight, which is why it regressed.

These results reject simple capacity growth and simple epoch shrinkage. A
future treatment needs to change source service or recover cross-epoch A
locality, and must compare against the same 4K storage budget.

## Evidence

- Feeder sweep:
  `/data1/nier/dx100-runs/2026-07-29-flag-bounded-feeder-sweep-3b50cdb`
- 256-line feeder:
  `/data1/nier/dx100-runs/2026-07-29-flag-bounded-feeder256-3b50cdb`
- 2K capacity/epoch:
  `/data1/nier/dx100-runs/2026-07-29-flag-offset-epoch2k-attribution-3b50cdb`
- 8K descriptors:
  `/data1/nier/dx100-runs/2026-07-29-flag-descriptor8k-3b50cdb`
- Refill trace:
  `/data1/nier/dx100-runs/2026-07-29-flag-offset-epoch-trace-3b50cdb`
