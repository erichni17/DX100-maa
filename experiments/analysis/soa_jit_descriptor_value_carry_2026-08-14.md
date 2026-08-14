# SoA/JIT descriptor value carry audit (2026-08-14)

## Decision

Do not promote descriptor value carry as an optimization. The zero-growth
descriptor repurposing and bounded hardware state are defensible, and the
treatment eliminates every Request-stage value read, but the exact paired micro
is 3.223837x slower than control. The implementation therefore remains
default-off and does not alter native DX100 or ordinary SoA/JIT behavior.

No full GZP run was performed.

## OffsetTableEntry liveness

`OffsetTableEntry` remains exactly 16 bytes.

| Field | SoA/JIT liveness after Fill | Treatment use |
| --- | --- | --- |
| `wid` | Live: identifies the A word in the retained row/line chain. | Preserved unchanged. |
| `next_itr` | Live: preserves duplicate chain order. | Preserved unchanged. |
| `itr` | Control-only: rediscovers the logical value operand and checks alias identity. | Low 32 raw operand bits. |
| `pass` | Dead in this SoA/JIT path; inserted as `-1` and not consumed. | High 32 raw operand bits. |

The treatment copies raw bits with `memcpy`, so FP32 and FP64 payloads,
including NaNs, are not numerically converted. Duplicate application still
follows the existing `next_itr` chain. A focused cancellation-sensitive unit
contract covers this order.

## Bounded treatment

During Fill, one physical value line is fetched sequentially before predicate
readiness/accounting. A retry can therefore stall only before predicate hit/use
ledgers mutate. Selected raw operands are inserted into the dead `itr`/`pass`
pair; rejected operands are not inserted. Request materializes the carried bits
in the existing lookahead slot and applies duplicates in retained chain order,
without issuing a value-cache request.

Incremental datapath storage is:

- 0 bytes per OffsetTableEntry; entry size remains 16 bytes.
- 73 modeled bytes per indirect unit: 64-byte line, 8-byte physical address,
  and 1-byte state.
- 80 bytes in the C++ host layout because of alignment, plus one configuration
  enable bit. Trace/stat counters are simulation instrumentation, not modeled
  datapath storage.

The option is default-off and mutually exclusive with value-prefetch credits.
Exact physical owner matching, generation/state checks, range/alignment checks,
response accounting, terminal ledgers, and the existing 16K reorder scope remain
fail-closed.

## Exact r4 evidence

Raw root:
`/data1/nier/dx100-runs/2026-08-14-soa-descriptor-value-carry-r4-9529b407`

- Source commit: `9529b4073399477dc3a46ca09b29ddba2b2e0461`
- Committed source archive SHA-256:
  `bdcde997f6199db90f60948744e3150574569449ed468e6a9e39d43b900b1477`
- gem5 SHA-256:
  `b3ba8ca705fcfba569c7ec58eae4300119eb8cc8149b8e6fd43803380966e43b`
- Guest SHA-256:
  `c7fb4f8dd038cb129115f11a11390aa672bd4e9fba4f05573e4aa257e089c497`
- Shared checkpoint:
  `/data1/nier/dx100-runs/2026-08-14-soa-jit-overlap-premerge-fast/c8l8-checkpoint`
- Expected and observed output hash: `2761840269561229581`; errors: 0.

Both arms used logical16/physical4K SoA, 32 contexts, predicate credits 16,
lookahead 8, value cache enabled, value prefetch credits 0, 32 value owners,
and one apply lane. The only treatment delta was
`maa_soa_jit_descriptor_value_carry=true`.

| Rep | Arm | simTicks | Fill cycles | Request cycles | Later value reads | Fill value reads |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | control | 80,828,181 | 109,383 | 46,051 | 18,147 | 0 |
| 1 | treatment | 260,576,882 | 699,635 | 30,006 | 0 | 2,048 |
| 2 | control | 80,828,181 | 109,383 | 46,051 | 18,147 | 0 |
| 2 | treatment | 260,576,882 | 699,635 | 30,006 | 0 | 2,048 |

The treatment eliminates 100% of later value reads and reduces Request cycles
by 34.842%, but raises Fill cycles by 539.620% and simTicks by 222.384%.
Repetitions are bit-for-bit identical in the reported metrics.

Every run produced one exact functional result, two terminal SoA completions,
and two reconciled `inherited/partitioned` reorder summaries. Aggregate
selected/rejected counts were 29,689/3,079. Per terminal operation, predicate
hits and uses equaled 16,384; A read/write issue-response counts balanced;
lookahead, delivery, alias, carried-operand, and carried-apply counts equaled the
selected population. Treatment value reads were `0/0`, carry Fill reads were
`1024/1024` per operation, and control carry ledgers were zero. Independent
recomputation of committed-source, binary, and guest hashes passed.

## Validation

- Focused FP32/FP64 raw-bit and duplicate-order unit: pass.
- New descriptor-carry contract and existing hybrid SoA contract: pass.
- `build/X86/gem5.opt`: built and incrementally validated.
- Fresh r4 two-repetition control/treatment harness: strict pass.
- Independent r4 functional/hash/terminal/reorder ledger audit: strict pass.
