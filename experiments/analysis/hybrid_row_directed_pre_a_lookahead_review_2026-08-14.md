# Hybrid scheduler review: row-directed pre-A value lookahead

Date: 2026-08-14
Scope: logical/metadata 16K Row/Offset reorder with physical 4K SPD and backing
Status: implemented, micro-validated, and exact-pass on one full GZP pair; CG gate running

## Recommendation

Make one narrow scheduler change: after a SoA/JIT context claims an exact Row chain and issues its A-line read, allow that context to fill and issue its existing ordered value lookahead while it is in `AwaitARead`. Values may become ready in the existing slots, but ordered apply and arithmetic remain gated on the matching A response and the transition to `Active`.

This is smaller and more general than changing the producer/page protocol. It adds no value requests, queues, owners, ports, payload storage, or guest-visible tokens. It overlaps two already-required read streams whose addresses are known at Row claim. Do not widen the index feeder, add sequential value prefetch, widen apply, retain more backing, or add page-priority retirement as part of this slice.

## Implementation and measured result

Implementation checkpoint: `a8af96f20f6aed09010a32c50986a62cc1664389`

Runner-fix/source checkpoint: `c186059f6ee87e705bbbc5d0b5707c9dbb0a523e`

Raw root: `/data1/nier/dx100-runs/2026-08-14-hybrid-pre-a-lookahead-c186059f-r2`

Both replicas restored the same checkpoint, used the same gem5/guest hashes, and were deterministic:

| Arm | Replica 1 ticks | Replica 2 ticks | Value reads | Pre-A issue/ready/use |
|---|---:|---:|---:|---:|
| Control, knob off | 75,100,907 | 75,100,907 | 18,390 | 0 / 0 / 0 |
| Treatment, knob on | 74,629,216 | 74,629,216 | 16,382 | 793 / 491 / 793 |

Treatment is 0.6281% lower in `simTicks` (1.006320460x speedup) in each replica and issues 10.9190% fewer physical value reads. Context stalls fall from 34,981 to 33,062 (-5.4858%); lookahead stalls fall from 966,451 to 822,169 (-14.9291%). Of 793 exact slots issued before A, 491 (61.9168%) are already ready at A activation and all 793 close at ordered use.

Correctness and comparability pass: exact output hash `2761840269561229581`, 29,689 selected plus 3,079 rejected aliases, 126/126 A reads and 126/126 A writes, two terminal completions, zero errors, empty terminal state, and zero sequential-prefetch issues/responses/promotions/discards in every arm. The only command treatment delta is `--maa_soa_jit_pre_a_value_lookahead`; logical/metadata 16K, physical 4K, predicate credits 16, index lines 4, contexts 32, lookahead 8, value cache enabled, owners 32, and apply lanes 1 are fixed.

Per the gem5 evidence checklist, this supports promotion to paired NAS CG and GZP volume-only tests. It does **not** justify default-on status or a full publisher-backed GZP claim. No full GZP run was launched.

### Full GZP volume-only result

Raw root: `/data1/nier/dx100-runs/2026-08-14-gzp-pre-a-pair-f2865321-r2`

The promoted same-checkpoint pair is now complete. Both arms exited zero and
passed exact output hash `11225737641199706160`, zero non-finite values, the
1,180,000-element scalar reference, the GZP terminal marker, all 61 logical
16K SoA/JIT completions, and request/response and terminal-state closure. The
resolved configurations are identical after removing the one treatment line,
`soa_jit_pre_a_value_lookahead`.

| Arm | `simTicks` | Context stalls | Value reads | Pre-A issue/ready/use |
|---|---:|---:|---:|---:|
| Control | 7,293,533,199 | 3,864,092 | 881,146 | 0 / 0 / 0 |
| Pre-A lookahead | 7,115,533,855 | 3,503,414 | 875,572 | 933,759 / 488,290 / 933,759 |

The treatment is 2.4405% lower in ticks, a 1.025015599x speedup. Relative to
the accepted native16 endpoint of 5,826,927,879 ticks, it reduces the hybrid
gap from 25.1694% to 22.1147%. Context stalls fall 9.3341%, and 52.2929% of
the pre-A slots are ready when their A response arrives. Physical value reads
fall 0.6326%.

`IND_SoaJitValueStalls` rises from 1,925,211 to 3,511,097 because the feature
starts bounded value work earlier and therefore exposes more value-owner
backpressure events. That counter increase is not a correctness failure and
does not contradict the lower end-to-end ticks; the treatment's measured
benefit is latency overlap and lower context blocking, not additional ports or
bandwidth. This is one exact paired instance, so it supports the mechanism and
the next combination experiment but is not yet a multi-replica default-on
claim.

## Why this is the next experiment

| Evidence | Result | Consequence |
|---|---:|---|
| Small API hybrid vs native16 | 18,903,009 vs 18,440,082 ticks, +2.510% | A small control-path gap exists even without backing RMW traffic. |
| Full GZP current hybrid vs native16 | 7,601,511,114 vs 5,827,869,383 ticks, +30.434% | The remaining integrated gap is large. |
| GZP volume-only SoA/JIT vs current hybrid | 7,239,192,956 ticks, 4.766% lower | Consumer scheduling is worth isolating, although this campaign is mechanism evidence rather than promotion evidence. |
| Wider B/index feeder, 4 to 32 lines | 45,282,023 to 45,470,136 ticks, +0.42% | It moved pressure into Row insertion/A request and is rejected. |
| Sequential value prefetch, best two-credit arm | 50,169,518 to 48,893,730 ticks, -2.543%, but only 24/2,011 promotions | Directional benefit, but 98.8% of prefetched entries were discarded; exact row-directed work is preferable. |
| Apply lanes 1 to 2/4 | +4.538% / +4.426% | Arithmetic width is not the next bottleneck. |
| Late page-ordered combiner drain | 19,913,686 ticks in both arms | Reordering completed output is too late. |
| Retain all 4,096 gather backing lines | zero fallback reads, but +0.0552% | More backing capacity is not justified. |

The detailed GZP counts come from checkpoint `28b5682767f8`: 61 SoA/JIT operations selected 949,411 aliases and issued 880,458 value reads, while recording 1,615,554 lookahead stalls and 3,749,936 context stalls. The page-order trace also shows why a late consumer policy cannot help: all four pages became ready only after source drain; page-0 contributors still extended through source issue sequence 8,910 of 9,522.

## Current readiness boundary

1. The response-bearing publisher makes each physical 4K page visible only after its exact `WriteResp`; GZP waits before reusing the physical SPD source.
2. The SoA/JIT Fill phase builds the complete logical 16K Row/Offset topology before Build begins. Thus a claimed Row chain contains exact, stable value addresses.
3. `serviceSoaJitBuild()` claims a row, installs an `AwaitARead` context, and issues its A-line read.
4. Before this patch, `fillSoaJitLookahead()` and `issueSoaJitValueRead()` required `Active`, so the exact value stream waited for the A response even though its addresses did not depend on A data.
5. The patch permits bounded fill and slot readiness in `AwaitARead`; `receiveSoaJitData()` still authenticates/copies the matching A line before changing the context to `Active`, and apply still follows Offset-chain order.

Changing publisher overlap first would require a page token/ownership contract across StreamAccess, the guest, and IndirectAccess. It is not the smallest safe change. Pre-A lookahead deliberately leaves producer readiness, backing publication, SPD reuse, and consumer page availability unchanged.

## Minimal implementation

At Row claim in `serviceSoaJitBuild()`:

1. Create the context and issue the existing A read exactly as today.
2. While the context remains `AwaitARead`, call the existing ordered lookahead fill and value-read issue paths for its exact Offset chain.
3. Permit value responses to populate the context's existing lookahead slots while A is outstanding.
4. Do not call the apply/delivery path until the exact A response transitions the context to `Active`.
5. On that transition, consume any ready prefix immediately; otherwise continue through the unchanged waiter/owner machinery.

The implementation is a feature-gated eligibility change, not a second prefetch structure. It infers issue eligibility from `context.state == AwaitARead` and uses one bounded per-context ordered-prefix count so `PreAValueUses` increments only at actual apply.

## Required invariants

1. **Exact scope:** enable only for guarded SoA/JIT RMW after Fill is complete and a valid Row chain has been claimed. Ordinary hybrid and unguarded paths are unchanged.
2. **Stable address:** every early request is derived from the claimed context's generation, slot, Row head, and exact Offset entry; value and output ranges retain their existing non-overlap checks.
3. **No early apply:** an `AwaitARead` context may allocate/fill lookahead slots, but may not mutate A data, advance the ordered apply head, write A, or complete.
4. **Order preserved:** aliases are applied once, in the existing Offset-chain order. Issuing or receiving a value must not consume the Offset entry.
5. **Exact ownership:** existing value owner/waiter coalescing, generation checks, retry handling, and response routing remain authoritative; no speculative sequential address is generated.
6. **Traffic closure:** selected aliases still close exactly as `value read issues + value hits + value merges`; the optimization must not increase logical selected aliases or physical value-read issues relative to the paired control.
7. **A closure:** A read/write issue/response ledgers and the response-bearing terminal completion remain exact and identical to control.
8. **Publication closure:** publisher `WriteResp`, page visibility, 4K SPD reuse, backing validity, and page-token rules are unchanged. No consumer may observe an unpublished page.
9. **Terminal emptiness:** all contexts, lookahead slots, value owners, waiters, retry state, and response trackers are empty at terminal completion.
10. **Disable equivalence:** with the knob off, scheduling and counters reproduce the current implementation.

## Counters and expected signature

Add three attribution counters, counted in logical lookahead slots rather than packets:

- `IND_SoaJitPreAValueIssues`: exact slots first assigned to a value owner while their context is `AwaitARead`.
- `IND_SoaJitPreAValueReadyAtAResponse`: those slots already ready when the matching A response activates the context.
- `IND_SoaJitPreAValueUses`: pre-A-assigned slots eventually applied in order.

At a correct terminal, `PreAValueIssues == PreAValueUses > 0`; `PreAValueReadyAtAResponse > 0` demonstrates real overlap. Existing selected/read/hit/merge and A ledgers must remain exact, and sequential-prefetch issue/promotion counters must stay zero. A useful result has lower value-lookahead/context stall cycles and lower repeated simulated ticks without more value-read issues. A shift in request-stage time is diagnostic, not sufficient by itself.

## Hardware cost

- Performance state: one 8-bit ordered-prefix count per context (32 logical bytes at the fixed 32-context maximum); it fits existing `SoaJitContext` padding, so the compiled context object does not grow. All payload/lookahead/owner state is reused.
- Datapath/storage: **no new payload RAM, tags, queues, owners, or ports**.
- Control: one per-unit enable bit and eligibility logic allowing existing fill/issue work in `AwaitARead`; apply remains gated by `Active`.
- Observability: three optional 64-bit counters per indirect unit (24 bytes if implemented in hardware; simulator-only counters otherwise).

Do not count the already-provisioned fixed owner array as new cost, and freeze its active count at 32 in the experiment.

## Exact test matrix

Use one clean checkpoint per paired campaign. Do not mix commits or completed checkpoints when calculating speedups.

| Gate | Workload | Arms | Repetitions | Fixed configuration |
|---|---|---|---:|---|
| Unit/contract (passed) | optimized+sanitized `run_soa_jit_overlap_state_unit.sh` and `test_soa_jit_pre_a_lookahead.py` | state and source contracts | 1 | Covers exact owner/merge/retry/generation state plus identity-before-issue, AwaitARead eligibility, Active-only apply, default-off plumbing, and terminal closure. |
| Micro correctness/performance (passed) | `test_hybrid_rmw_soa` | `pre_a=0`, `pre_a=1` | 2 each | logical/metadata 16K; physical/backing 4K; contexts 32; lookahead 8; predicate credits 16; value owners 32; apply lanes 1; sequential prefetch 0. |
| Generality | NAS CG residual SoA/JIT | `pre_a=0`, `pre_a=1` | 3 each | Same knobs and checkpoint; identical input/checkpoint and output validation. |
| Integrated benefit | GZP volume-only SoA/JIT | `pre_a=0`, `pre_a=1` | 3 each | Same knobs and checkpoint; use the performance-authorized volume-only path. |
| Publisher guardrail | Full publisher-backed GZP SoA/JIT | `pre_a=0`, `pre_a=1` | 1 correctness run, then 3 each only after both arms pass | Same knobs; require clean publisher and two-terminal ledgers before treating timing as evidence. |

Micro acceptance requires exact hash `2761840269561229581`, zero errors, both terminal completions, identical selected/rejected counts, identical A ledgers, no sequential-prefetch activity, and all terminal structures empty. Promote to the generality gates only if both candidate micro repetitions are correct, `PreAValueIssues == PreAValueUses > 0`, `PreAValueReadyAtAResponse > 0`, value-read issues do not increase, and candidate ticks are lower in both repetitions. Promote beyond GZP/CG only if each workload's three paired repetitions have a lower median with no correctness or traffic regression.

The existing full publisher-backed GZP campaign is not performance-authorized until its separate two-RMW terminal failure is resolved. A passing volume-only result cannot waive that guardrail.

## Code locations

- `src/mem/MAA/IndirectAccess.cc`: `serviceSoaJitBuild()` (context claim/A issue), `issueSoaJitValueRead()`, `fillSoaJitLookahead()`, ordered delivery/apply, and A-response activation (currently approximately lines 4251-4651).
- `src/mem/MAA/SoaJitOverlapState.hh`: existing context state, lookahead slots, owners, and waiters (approximately lines 270-315); add tests, not a parallel structure.
- `src/mem/MAA/IndirectAccess.hh`, `src/mem/MAA/MAA.hh`, and `src/mem/MAA/MAA.cc`: feature gate plumbing and attribution counters.
- `src/mem/MAA/MAA.py`, `configs/common/Options.py`, and `configs/common/MAAConfig.py`: one disabled-by-default experiment knob.
- `tests/`: AwaitARead state/ordering unit cases and the existing SoA/JIT overlap-state runner.
- `experiments/scripts/run_soa_jit_pre_a_lookahead_matrix.sh`: single-checkpoint, two-replica control/treatment driver and exact promotion checks.
- `experiments/analysis/hybrid_row_directed_pre_a_lookahead_micro_2026-08-14.tsv`: committed compact result table; raw outputs remain outside Git.

No first-slice changes belong in `StreamAccess.cc`, `benchmarks/API/MAA_gem5.hpp`, or `benchmarks/UME/gradzatp.cpp`. Keeping those untouched is part of the experiment's isolation.
