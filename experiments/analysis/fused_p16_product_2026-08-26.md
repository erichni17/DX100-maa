# Guarded fused p16 product prototype (2026-08-26)

## Decision

**ACCEPT the bounded `CG_NA=256` prototype evidence. Do not promote to a
native or full workload.** The cache-on page-fed p16/q16 control completed in
419,423,756 simTicks and the fused-p16-product+q16 candidate completed in
397,150,050 simTicks. The candidate is lower by 22,273,706 ticks (5.31055%,
control/candidate 1.0560838555x) after, and only after, every correctness,
configuration, ownership, and mechanism gate below closed.

This decision is limited to the guarded FP32/MUL six-word form and the one
shared-checkpoint `CG_NA=256` pair required by
`experiments/reviews/2026-08-26_fused_p16_gather_map_product_review.md`.
`native_runs=0` and `full_cg_runs=0` are explicit evidence fields.

## Implemented boundary

- `INDIR_LD_VIRTUAL_INDEX`, FP32, MUL is the only new decode. Word 2 names p,
  word 3 product backing, word 4 colidx, and word 5 the dense coefficient
  span. Legacy virtual-index forms retain their old shape.
- The form requires aligned registered 65,536-byte p, product, colidx, and a
  spans, exact `0:16384:1`, one 16K Row/Offset epoch, 16K capacity, p reorder,
  no predicate, no drain, no global fallback, and pairwise-disjoint spans.
- The existing source response reservation and Offset head remain the exact
  owner. Each of the eight response slots has one byte of
  NeedCoefficient/AwaitCoefficient/AwaitMultiply/ProductReady substate.
- Coefficients use the existing cache-on SoA/JIT owner pool with 32 active
  64-byte lines and zero prefetch credit. A tagged, backpressured FP32 pair
  uses the ordinary ALU's one-cycle lane. Its completion overwrites the
  retained p word in place; the original p word is restored only after the
  product is accepted, so duplicate colidx aliases remain correct.
- Products retire through the existing 16-slot, four-way/four-bank bounded
  combiner at one attempted word/cycle. Completion is not visible until the
  exact product WriteResp owner closes. Page-fed q16 starts only after fused
  producer completion.
- The candidate allocates no virtual-p backing and invokes no product SPD
  publisher. There is no hidden spill or fallback path.

## Validation ladder

### Unit and state tests

The optimized and ASan/UBSan C++ state-model tests cover the guarded decode,
misalignment/registration/alias/capacity rejection, all-same and repeated
indices, reversed p responses, shuffled coefficient responses, ALU
backpressure, combiner pressure, reordered WriteResp, stale tags, exact
16,384-word retirement, and empty terminal state. Python contract/runner
tests, guest `-Werror` builds, existing logical page/direct4/page-fed tests,
the touched-source style gate, and a complete `gem5.opt` link also passed.

The matched runner was subsequently tightened in commit `e20ab3ba` to require
exactly ten full windows, with a negative nine-window test. The accepted raw
pair already records exactly ten windows.

### Exact 16K micro

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-fused-p16-product-prototype-20260826-20260826-132434-13cbd7df/fused-p16-product-micro-d9bf1019-r5`

The four-segment collision micro passed at source commit `d9bf1019`:

| Check | Exact result |
|---|---:|
| Logical words / p epochs | 16,384 / 1 |
| Reference/product/q hash | 6939999077410828482 |
| Sentinel words / errors | 0 / 0 |
| p issue/response records | 256 / 256, reordered |
| Coefficient issue/response/fill | 5,712 / 5,712 / 5,712, reordered |
| Coefficient hits / merged waiters / evictions | 8,211 / 2,461 / 5,680 |
| Coefficient deliveries | 16,384 |
| MUL accepts / completions | 16,384 / 16,384 |
| Product insertions / semantic WriteResp completions | 16,384 / 16,384 |
| q operations / page admissions / command responses | 1 / 4 / 5 |
| q value deliveries | 16,384 |
| Epoch drains / fallbacks / publisher lines / virtual-p bytes | 0 / 0 / 0 / 0 |

The measured micro simTicks are 4,107,055,792. This is a correctness and state
handoff micro, not performance evidence.

### Shared-checkpoint `CG_NA=256` pair

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-fused-p16-product-prototype-20260826-20260826-132434-13cbd7df/cg-fused-p16-q16-9d8b8810-na256-r2`

The pair used source commit `9d8b88106e7c15a3f0bf0fd8ae006ee358d10115`,
gem5 SHA-256
`8ab7e265e66e81078c6171f5de1bb3a42982e727c2cd31b1184214c3968354ed`,
Ramulator SHA-256
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`,
and one immutable checkpoint ledger SHA-256
`b6e5d8e88d1d197e58f64289730287cd87acaaa789e15f3424f98ce4fc79f085`.
The before/after artifact and checkpoint ledgers compare byte-for-byte equal.
The raw-root ledger SHA-256 is
`1b970ba6e72dbbbec28d22405071d4ec88a1ccba7af1c3c39d77e3301f459be3`.

Both arms have the identical raw and quantized CG fingerprint, including
`x_raw=d942be57c8fbc635`, `z_raw=f0b4138d16c12153`, and PASS. All eleven
deterministic reduction-evidence lines are byte-for-byte identical. Both arms
close exactly ten p16 and ten q16 windows: 163,840 staged indices, products,
q selections, q aliases, and q value deliveries; 40 page admissions; ten
closes; and 50 command responses.

Candidate fused producer ledger:

| Ledger | Exact count |
|---|---:|
| Operations / complete Row-Offset epochs | 10 / 10 |
| Source ordinals | 163,840 |
| Coefficient read issues / responses / fills | 13,418 / 13,418 / 13,418 |
| Coefficient deliveries | 163,840 |
| Tagged MUL accepts / completions | 163,840 / 163,840 |
| Product insertions / semantic WriteResp completions | 163,840 / 163,840 |
| q value read issues / responses / fills | 10,305 / 10,305 / 10,305 |
| q value hits / deliveries | 153,535 / 163,840 |
| Fused and q epoch drains | 0 / 0 |
| Fused and bounded-global fallbacks | 0 / 0 |
| Publisher issues / responses / fused publisher lines | 0 / 0 / 0 |
| Virtual-p allocation / writes / reads / stat bytes | 0 / 0 / 0 / 0 |
| Hidden spill / host payload access | 0 / 0 |

The 13,418 coefficient line reads are within the honest ten-window floor and
ceiling (10,240 to 163,840); no locality floor was assumed. The q16 read and
RMW ledgers are exactly equal across the matched arms.

## Byte, port, and state accounting

| Item | Candidate delta or charge | Bound/evidence |
|---|---:|---|
| Guest coherent backing | -262,144 B | Candidate retains only the 262,144-B product array; control also allocates four 65,536-B virtual-p spans. |
| Virtual-p traffic per 16K window | -65,536 B writes and -65,536 B reads | Ten-window pair removes 655,360 B writes and 655,360 B reads. |
| Product backing/retirement | 262,144 B backing; 163,840 semantic writes across ten windows | Retained and closed by exact WriteResp; not counted as eliminated traffic. |
| Descriptor payload | 0 modeled B | Reuses existing IF word 5 and decoded address storage. |
| Row/Offset payload | 0 B | Reuses exactly 16,384 provisioned entries and one epoch. |
| Active coefficient payload | 2,048 B per indirect unit | Reuses the existing physical owner array; only its 32-line prefix is active. |
| p response payload | 512 B per indirect unit | Reuses eight existing 64-byte response lines. |
| Product combiner payload | 2,048 B per indirect unit | Reuses 16 slots x 16 words x 8-byte maximum-width payload. |
| Fused response substate | 8 B per indirect unit | One byte on each of eight existing response slots; compile-time size assertion. |
| Timed ALU identity | 8 B semantic sideband for the one-lane prototype | Exact generation/unit/response/Offset identity reuses existing direct-lane storage; one ALU pair may be in flight. |
| External cache/memory ports | 0 | Existing p/coefficient read and acknowledged product-write paths only. |
| New internal ALU path | one 32-bit p input, one 32-bit coefficient input, one 32-bit in-place result, valid/backpressure/token | Ordinary one-cycle FP32 MUL lane; no new multiplier or payload queue claimed. |
| Hidden spill/fallback payload | 0 B | No queue, spill, host-payload, global-fallback, drain, publisher, or virtual-p escape. |

Finite matched settings were eight response slots, 16 combiner slots, four
ways, four banks, one word attempt/cycle, 32 acknowledged writes, 32 retained
coefficient owners, zero response-word pools, and zero prefetch credits.

## Handoff and scope

Implementation began at `4be95adb` and the exact-source milestones through
`9d8b8810` include bounded dump records, duplicate-p preservation, micro
checkpoint/layout checks, finite pair knobs, and prefaulted deferred control
backing. Commit `e20ab3ba` pins the known ten-window gate.

The analysis follows the completion-first rule from the gem5 evidence skill:
terminal exits, artifact/checkpoint identity, fingerprints, reductions, exact
configuration, and every mechanism ledger were audited before simTicks. The
result supports only this bounded prototype. It does not authorize native,
`CG_NA=1024`, or full runs and does not claim promotion readiness.
