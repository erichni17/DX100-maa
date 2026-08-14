# Hybrid optimization checkpoint - 2026-08-14

## Current result

The current lean point is a 16K logical Row/Offset reorder window with a 4K
physical SPD, 1,152 response tags, a 2,304-word packed response pool, 512
destination-line tags, a 4,096-word destination payload pool, four retirement
words per cycle, and eight explicit combiner banks.

Two independent API checkpoint instances passed exact correctness with key
`7228541527853630339`:

| checkpoint | native16 | native4 | hybrid | hybrid gap vs native16 | speedup vs native4 |
|---|---:|---:|---:|---:|---:|
| A | 18,332,410 | 29,325,909 | 18,890,489 | 3.044% | 1.552x |
| B | 18,420,050 | 29,315,267 | 19,786,921 | 7.421% | 1.482x |

The result is therefore single-digit behind native16 on both checkpoint
instances. The defensible API statement is a **3.04-7.42% latency gap**, not
the better endpoint alone. Restoring the current `7a4ac410` gem5 binary from
checkpoint A reproduced the older binary's `18,890,489` ticks and mechanism
counters exactly. This falsifies a timing regression from the sparse-payload
refactor and attributes the range to checkpoint-dependent execution state.
The cross-binary diagnostic and frozen provenance are at
`/data1/nier/dx100-runs/2026-08-14-general-hybrid-api-sparse-current-on-f04-checkpoint-7a4ac410-r1`.

## Controlled knob attribution

Root:
`/data1/nier/dx100-runs/2026-08-14-general-hybrid-api-matched-r1152p2304-c512-banks-sparse-7a4ac410-r1`

All treatment arms restored the same checkpoint and produced the same exact
output. The following changes all left `simTicks=19,786,921`:

- four to eight retirement words per cycle;
- unlimited combiner-bank abstraction to eight explicit banks;
- eight to sixteen explicit banks; and
- 512 to 1,024 destination-line tags with the payload fixed at 4,096 words.

The eight-bank arm records 16,384 accesses and 920 conflict cycles, versus 184
conflict cycles with sixteen banks, but neither count reaches the measured
critical path. The 512-tag high-water is only 325-326 lines. These results
reject WPC8, sixteen banks, and 1,024 tags as unjustified hardware additions.

The selected hybrid performs 2,048 full-line writes and zero partial writes.
Its pipeline records 35,344 source/write overlap cycles, 7,861 source-only
cycles, 622 write-only cycles, zero idle cycles, and zero response-word-pool
stalls. The current bottleneck is not a serialized final LLC copy.

## Storage boundary

The source-checked ledger is at:
`/data1/nier/dx100-runs/2026-08-14-general-hybrid-r1152-storage-accounting-7a4ac410`.

For the measured FP64 point it reports 512 KiB physical SPD, 18 KiB packed
response payload, 32 KiB destination payload, and 44.77 KiB incremental
virtual tags/control per indirect unit. Including the retained logical
Row/Offset descriptors and readiness, the configured comparable lower bound
is 873.28 KiB versus 2.30 MiB for native16, a 62.875% reduction.

This is a capacity lower bound, not synthesized area. It excludes ports,
arbitration, wiring, SRAM periphery, and host-container overhead. The retained
16K Row/Offset window is intentional: it preserves the native16 reorder scope;
only result storage is virtualized here.

## Promotion status

The API experiment establishes optimized feasibility, not application
generality. GZP and CG campaigns remain the promotion gates. Until both have
terminal exits, exact output checks, and comparable `simTicks`, report this as
a microbenchmark result and keep the application verdict open.
