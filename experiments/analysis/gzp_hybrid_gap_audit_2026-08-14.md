# Full-GZP hybrid gap audit — 2026-08-14

## Finding

The small exact API result does not predict full GZP because it measures one
logical gather and its four-page materialization, not GZP's repeated indirect
RMW consumers.  Its `hybrid_token_stream_ld` arm is only **2.510%** behind
native16 (`18,903,009` versus `18,440,082` `simTicks`) and executes one
indirect read, zero indirect RMWs, four materializer submissions, and one
materializer retirement.

The matched completed full-GZP arms show a different mechanism mix:

| arm | `simTicks` | gap vs native16 | indirect RMWs | stream reads |
|---|---:|---:|---:|---:|
| native16 | 5,827,869,383 | baseline | 124 | 310 |
| native4 | 7,633,155,101 | +30.977% | 490 | 1,225 |
| current hybrid | 7,601,511,114 | +30.434% | 490 | 981 |
| volume-only SoA/JIT | 7,239,192,956 | +24.217% | 307 | 737 |

The current hybrid therefore saves materialization work but leaves GZP's 4K
consumer cadence: 490 RMWs rather than native16's 124.  The volume-only
SoA/JIT arm proves that reducing that cadence matters—it is 4.766% lower in
`simTicks` than current hybrid—but its new RMW implementation exposes a much
larger per-alias service path:

- 61 exact SoA/JIT instructions select 949,411 aliases;
- 880,458 aliases (92.737%) issue timed value-line reads; only 29,785 hit and
  39,168 merge behind an existing fill;
- the 32-owner pool records 878,506 evictions and 1,615,554 value/lookahead
  stalls; and
- 509,830 A-line reads, 509,830 response-bearing A-line writes, and 3,749,936
  context-scoreboard stalls close exactly.

Thus the remaining gap is not evidence that the logical-16K gather failed. It
is the cost of GZP's repeated RMW/stream consumers, followed by SoA/JIT's
current `A read -> value demand -> ordered apply -> A WriteResp` service for
nearly one million selected aliases. The small API has none of that RMW work.

## Evidence boundary

The small API evidence is the PASS report under
`/data1/nier/dx100-runs/2026-08-14-general-hybrid-api-capture512-opt-65738674-r2`
(clean source `657386740838166a7225e6947b31e2422d80b5aa`, one replica,
exact hash `7228541527853630339`).

The full-GZP observations are the four individually successful arms under
`/data1/nier/dx100-runs/2026-08-14-gzp-soa-jit-live-context32-fab420af-r1`
(clean source `fab420afc13b2652464c3e3d1c5c15132bb3f39b`, one replica,
exact hash `11225737641199706160`). Each has wrapper status zero, one normal
`m5_exit`, zero reference errors, and closed response counters. The campaign
as a whole is not promotable: its separate two-RMW correctness-only arm
aborted and left `campaign.exit=1`. The table is consequently a single-run
mechanism audit, not repeated promotion evidence. Host time and MAA aggregate
cycle estimates are intentionally unused.

## Highest-confidence general optimization

Issue each claimed A row's **exact ordered value lookahead while its A-line
read is outstanding**, then apply only after the A response. This is a general
SoA/JIT scheduling change, not a GZP opcode: the Row/Offset chain already
identifies the exact values that will be consumed, and every prefetched value
keeps its existing `(generation, context, slot, offset)` waiter. It preserves
one A read/write pair per row, Offset-chain FP order, and final WriteResp
completion. Unlike sequential stream prefetch, it neither guesses an address
nor adds a value read.

The current default-off sequential prefetch is not the recommendation. Its
exact micro sweep at
`/data1/nier/dx100-runs/2026-08-14-soa-value-prefetch-76a21e25-r1` has a best
two-credit result of `48,893,730` versus `50,169,518` disabled `simTicks`
(2.543% lower), but only 24 of 2,011 prefetches promote to a waiting demand;
1,987 discard. One, four, and eight credits regress, and eight credits discard
all 2,048 requests. Larger apply width is also rejected by the exact micro
evidence: two and four lanes are 4.538% and 4.426% slower than one. These
signatures favor exact row-directed latency overlap over more speculative
traffic or arithmetic width.

### Exact code locations

- `src/mem/MAA/IndirectAccess.cc:4251-4305`: `serviceSoaJitBuild()` claims the
  row, installs `AwaitARead`, and issues the A read. Invoke the bounded initial
  lookahead here after the context identity is complete.
- `src/mem/MAA/IndirectAccess.cc:4311-4412`: permit
  `issueSoaJitValueRead()` for `AwaitARead` as well as `Active`; retain all
  generation, span, waiter, coalescing, and issue accounting.
- `src/mem/MAA/IndirectAccess.cc:4415-4438`: permit
  `fillSoaJitLookahead()` to allocate slots while `AwaitARead`.
- `src/mem/MAA/IndirectAccess.cc:4441-4572`: keep delivery/application gated
  on `Active`, so no value can modify an A line before that line arrives.
- `src/mem/MAA/IndirectAccess.cc:4630-4651`: the authenticated A response
  remains the sole `AwaitARead -> Active` transition.
- `src/mem/MAA/SoaJitOverlapState.hh:270-315`: reuse the existing cache-line
  owner and waiter mask; a waiting row-directed value cannot be evicted.
- Add cumulative `pre_a_value_issues`, `pre_a_ready_at_a_response`, and
  `pre_a_value_uses` beside the SoA/JIT counters in
  `src/mem/MAA/IndirectAccess.hh`, and register/report them with the existing
  SoA/JIT statistics in `src/mem/MAA/MAA.hh` and `src/mem/MAA/MAA.cc`.

No guest ABI or GZP source change is required. The API already supplies exact
index/value/predicate spans at `benchmarks/API/MAA_gem5.hpp:718-733`, and GZP
uses it at `benchmarks/UME/gradzatp.cpp:504-521`.

### Hardware cost

The treatment adds **zero value-payload bytes, zero context slots, zero value
owners, and zero ports**. It reuses the already provisioned 32 contexts, eight
lookahead slots per context, and value-owner/coalescer entries. The datapath
change is an eligibility condition and scheduler call; a retained experiment
knob costs one control bit. Its real resource pressure is additional
simultaneous use of the existing cache request path/MSHRs, which the experiment
must expose rather than assume away.

## Minimal exact experiment (do not start with GZP)

Extend the existing `test_hybrid_rmw_soa` shared-checkpoint micro matrix with
two default-off arms, repeated twice: control and pre-A lookahead. Freeze
logical/metadata 16K, physical SPD 4K, contexts 32, lookahead 8, predicate
credits 16, value owners 32, apply lanes 1, and sequential prefetch credits 0.
Change only the pre-A eligibility knob.

Require in every arm:

1. exact output hash `2761840269561229581`, `errors=0`, two terminal
   completions, and unchanged selected/rejected and A read/write-response
   totals;
2. `pre_a_value_issues == pre_a_value_uses > 0`,
   `pre_a_ready_at_a_response > 0`, and all existing value/delivery/Offset and
   WriteResp ledgers closed;
3. no sequential-prefetch issues and no increase in total value-read issues
   versus control; and
4. lower repeated `simTicks` accompanied by fewer value/lookahead or context
   stalls. Reject a faster arm if its mechanism signature, exact order, or
   completion ledger differs.

Only after that two-arm micro passes should the same knob be tested on the
performance-authorized volume-only GZP arm with a fresh same-commit,
same-checkpoint native16/current/volume matrix. No gem5 run was launched for
this audit.
