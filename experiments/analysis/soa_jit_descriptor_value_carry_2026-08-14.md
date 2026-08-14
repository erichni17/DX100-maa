# SoA/JIT descriptor value carry audit (2026-08-14)

## Decision

Do not promote descriptor value carry as an optimization. The r4 one-owner
result is an exact deterministic rejection: 260,576,882 versus 80,828,181
ticks, despite eliminating every Request-stage value read. A single bounded
follow-up with a fixed 16-line owner pool also rejects. Credits 1/4/8/16 all
close exactly, but the best setting (16) still takes 91,043,562 ticks, 12.638%
slower than control. The implementation therefore remains default-off and does
not alter native DX100 or ordinary SoA/JIT behavior.

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

## r4 one-owner treatment

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

The Fill serialization is direct: the sole line owner issues one sequential
64-byte value read, then the current logical operand and therefore Fill wait for
that response. Only after the final word in that line is consumed can the owner
be released and the next of 2,048 lines issued. This creates a dependent chain
of 2,048 line-latency waits on Fill's critical path. The 16,045-cycle Request
saving cannot offset the resulting 590,252-cycle Fill increase.

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

## Bounded multi-line follow-up

The only follow-up replaces the one owner with a fixed pool of 16 physical
64-byte line owners and makes the active credit limit configurable as 1, 4, 8,
or 16. The feature remains default-off; when enabled, the credit setting
defaults to 1. The feeder scans forward in logical source order, issues each
unique line into a free owner, and carries operands into the unchanged retained
Offset descriptors. Responses match an exact physical owner. Each owner also
holds the smallest bounded virtual identity needed to distinguish live lines: a
16-bit block ordinal relative to the registered value range. Distinct virtual
ordinals mapping to one live physical line fail closed.

Incremental datapath storage per indirect unit is fixed, independent of the
number of descriptors:

- 0 bytes per OffsetTableEntry; it remains exactly 16 bytes.
- 75 modeled bytes per owner: 64-byte payload, 8-byte physical address, 2-byte
  block ordinal, and 1-byte state.
- 16 owners = 1,200 modeled bytes per unit; the C++ host layout is 80 bytes per
  owner and 1,280 bytes per unit.
- A 2-bit active-setting selector encodes 1/4/8/16, in addition to the existing
  one-bit carry enable. Trace counters are not modeled datapath storage.

Raw root:
`/data1/nier/dx100-runs/2026-08-14-soa-descriptor-value-carry-fill-credits-8d9127ee`

- Source commit: `8d9127ee8c7652490c04206640137268714550c8`
- Committed source archive SHA-256:
  `ceb4767cd4ccd965a5bd81073f47f978dec09f2a001099b95b673f8907ef1216`
- gem5 SHA-256:
  `9d265ac7dd6aa5e95866259c15274b058d274c1aa30f4bff569121541ca30769`
- Guest SHA-256:
  `c7fb4f8dd038cb129115f11a11390aa672bd4e9fba4f05573e4aa257e089c497`
- Shared checkpoint:
  `/data1/nier/dx100-runs/2026-08-14-soa-jit-overlap-premerge-fast/c8l8-checkpoint`
- Expected and observed output hash: `2761840269561229581`; errors: 0.

All other controls match r4. Results below are identical in repetitions 1 and
2; the table therefore reports the exact value observed in both repetitions.

| Arm | simTicks | Fill cycles | Request cycles | Later value reads | Fill value reads | Owner HWM | simTicks vs control |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 80,828,181 | 109,383 | 46,051 | 18,147 | 0 | 0 | baseline |
| credits 1 | 260,576,882 | 699,635 | 30,006 | 0 | 2,048 | 1 | +222.384% |
| credits 4 | 127,013,209 | 273,376 | 30,007 | 0 | 2,048 | 4 | +57.140% |
| credits 8 | 94,710,044 | 171,425 | 30,007 | 0 | 2,048 | 8 | +17.175% |
| credits 16 | 91,043,562 | 159,099 | 30,007 | 0 | 2,048 | 16 | +12.638% |

More credits remove the one-owner dependency chain, but they do not make value
fetch free: all 2,048 lines are now charged to critical Fill, capped at 16
in-flight owners. At credits 16, Fill is still 45.451% above control while
Request is 34.840% lower. Control instead performs the 18,147 alias requests in
reordered Request order through its existing 32-owner value machinery, where
line fills and A-line work overlap. The treatment's smaller Request phase saves
16,044 cycles, but its Fill phase costs 49,716 extra cycles. Thus no setting
beats control, and the bounded follow-up is rejected.

Every one of the ten runs produced one exact functional result, two terminal
SoA completions, and two reconciled `inherited/partitioned` reorder summaries.
Each treatment recorded 29,689 selected and 3,079 rejected operands, zero late
value reads, 2,048 balanced Fill reads, carried operands/applies equal to the
selected population, and owner high-water no greater than its configured
credit. Both repetitions are identical across simTicks, Fill/Request cycles,
read counts, and high-water marks. Independent recomputation of the committed
source archive, gem5 binary, and guest hashes passed.

## Validation

- Focused FP32/FP64 raw-bit and duplicate-order unit: pass.
- New descriptor-carry contract and existing hybrid SoA contract: pass.
- `build/X86/gem5.opt`: built and incrementally validated.
- Fresh r4 two-repetition control/treatment harness: strict pass.
- Independent r4 functional/hash/terminal/reorder ledger audit: strict pass.
- Committed-source credits 1/4/8/16, two-repetition shared-checkpoint sweep:
  strict pass; decision reject.
- Independent ten-run functional/hash/terminal/reorder/storage audit: strict
  pass.
