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

## GZP application gate

Root:
`/data1/nier/dx100-runs/2026-08-14-general-hybrid-gzp-tailfix-lean-r1152p2304-c512w16-b8-wpc4-901daab8-r1`

All five arms terminated with exact output hash `11225737641199706160`, zero
non-finite values, and `UME_REFERENCE_PASS` for 1,180,000 elements. The
analyzer initially rejected the campaign because it required every materializer
to create its context at first submit. GZP legally pre-registers the exact
context, so its submits report `new_context=0`. Commit `5263d838` accepts either
created or reused application contexts while retaining the exact close checks.

| arm | `simTicks` | gap vs native16 | speedup vs native4 |
|---|---:|---:|---:|
| native16 | 5,826,750,095 | baseline | 1.311x |
| native4 | 7,636,382,131 | +31.05% | baseline |
| hybrid token materializer | 7,351,221,603 | +26.16% | 1.039x |

The current hybrid recovers only 15.76% of the native4-to-native16 opportunity
on GZP. This is not explained by lost 16K reordering: the hybrid retains the
full logical Row/Offset window. It is dominated by materializing result pages
from coherent backing storage.

The exact trace contains 61 completed materializer lifetimes and 62,464
backing cache-line reads, exactly 1,024 lines per lifetime. Producer traffic
contains 378,002 write responses: 377,966 are partial-line writes and only 36
are full-line writes. All 61 completed lifetimes eventually produce 1,024
complete lines with no overlapping word masks or writes after a line first
becomes complete. Therefore:

- retaining only already-complete inactive lines can help API but has almost
  no opportunity on GZP;
- a general GZP mechanism must accumulate bounded masked fragments before page
  activation and release only a sealed, exact line; and
- 62,464 avoided backing reads is an ideal unbounded mechanism ceiling, not a
  prediction for a finite direct-index implementation.

## Promotion status

The API experiment establishes optimized feasibility. GZP is exact but does
not yet establish useful application performance; CG is still outstanding.
The next promotion gate is a reviewed bounded inactive-fragment mechanism,
followed by fresh same-checkpoint API, GZP, and CG comparisons. Until then,
report the single-digit API result and the negative GZP result separately.
