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
on GZP. This is not explained by lost 16K gather reordering: the hybrid retains
the full logical Row/Offset window. The remaining application schedule still
executes the two indirect RMWs and their consumers as 4K operations.

An exact same-checkpoint retention campaign tested 0, 2,048, and 4,096 retained
lines. The 4,096-line arm eliminates all 62,464 backing fallbacks but is
0.0552% slower than retention off. Every arm passes exact output hash and
application-reference checks. Therefore:

- gather-result backing reads are overlapped or noncritical on GZP;
- increasing gather retention is not the next optimization target; and
- the page-local downstream instruction schedule must be widened if the
  hybrid is to approach native16 application performance.

The measured hybrid/native16 MAA-cycle gap is 4,870,516 cycles. The indirect
RMW-cycle delta is 4,028,146 cycles, or 82.7047% of that gap. GZP issues 490
hybrid RMWs versus 124 native16 RMWs because it invokes two RMWs for every 4K
page instead of twice per logical 16K window. Substituting native16's measured
RMW-cycle total into the hybrid gives an optimistic ceiling of 6,090,411,905
ticks, 4.525% slower than native16. This is not a measured speedup and assumes
free staging, but it identifies logical-16K RMW execution as the next target.

## Current implementation target

The target remains the existing hybrid, not the fully bounded design:

- 16K logical tile and 16K Row/Offset reorder metadata;
- 4K physical SPD/result storage;
- one logical 16K RMW rather than four page-local RMWs; and
- finite just-in-time operand and write scoreboards sized by memory
  concurrency, not by the 16K logical window.

The preferred RMW input is structure-of-arrays backing. GZP can reuse
`c_to_p_map` and `corner_volume` directly because both already exist as
sequential arrays. It needs timed backing only for the shared predicate and
the computed `csurf * zone_field` values. Row/Offset entries retain logical
`i`; after an A cache line is selected, the consumer fetches the corresponding
value through the normal cache path, applies the alias chain in ordinary
insertion order, and waits for an authenticated A-line `WriteResp` before
completion. This preserves the full 16K reorder scope without a hidden
logical-window-sized value array.

A separate 4K-Row/Offset result may still be reported as a fully bounded
diagnostic, but it is not the primary hybrid optimization arm.

The backed-RMW API is also an instruction-path change because it can consume
arrays without first staging them in SPD. Performance attribution therefore
requires four controls: ordinary native16, ordinary native4, backed RMW with
16K metadata/16K physical SPD, and the identical backed RMW with 16K
metadata/4K physical SPD. Only the last pair isolates virtualization overhead.
Ordinary-to-backed comparisons measure the separate API/staging change.

### Accepted pre-A scheduling improvement

The default-off row-directed pre-A treatment now has a complete full GZP
volume-only pair at
`/data1/nier/dx100-runs/2026-08-14-gzp-pre-a-pair-f2865321-r2`.
Both arms use the same binary, guest, selector, checkpoint, and resolved
configuration except for `soa_jit_pre_a_value_lookahead`; both exit zero with
exact output hash `11225737641199706160`, the 1,180,000-element reference,
61/61 SoA/JIT terminals, and closed traffic ledgers.

The control is 7,293,533,199 ticks and the treatment is 7,115,533,855 ticks:
2.4405% fewer ticks, or 1.025015599x speedup. The native16 gap drops from
25.1694% to 22.1147%. This validates overlapping exact value requests with an
outstanding A read, at effectively no payload-storage cost, but leaves a large
application gap. It should be combined with traffic elimination rather than
treated as the final hybrid optimization.

## Promotion status

The API experiment establishes optimized gather feasibility. GZP is exact but
does not yet establish useful application performance; CG is still
outstanding. The next promotion gate is a correctness-first logical-16K RMW
mechanism, followed by replacement of any logical-window-sized operand arrays
with bounded just-in-time replay state. Then run fresh same-checkpoint API,
GZP, and CG comparisons. Until then, report the single-digit API result, the
negative gather-retention result, and the analytic RMW ceiling separately.
