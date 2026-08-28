# Live bounded CG combiner-capacity sweep (2026-08-28)

## Decision

**Reject destination-combiner capacity growth as a selected optimization.**

All five legal NA256 successors preserve exact output, deterministic
reductions, strict 16K admission, semantic work, and retirement ACK closure.
Larger line tables reduce write transactions substantially, but none produces
a meaningful end-to-end gain.  Retain the 16-line baseline.

| Line slots | Shared payload words | `simTicks` | Change vs. 16 | Writes | Write reduction | Full / partial writes |
|---:|---:|---:|---:|---:|---:|---:|
| 16 | 256 derived | 246,463,712 | baseline | 26,672 | baseline | 0 / 26,672 |
| 32 | 512 derived | 246,571,697 | +0.043814% | 26,672 | 0% | 0 / 26,672 |
| 64 | 1,024 derived | 246,459,956 | -0.001524% | 26,607 | 0.244% | 3 / 26,604 |
| 128 | 2,048 derived | 246,577,957 | +0.046354% | 24,296 | 8.908% | 127 / 24,169 |
| 256 | 3,968 capped | 246,901,912 | +0.177795% | 18,299 | 31.392% | 945 / 17,354 |
| 512 | 3,968 capped | 248,354,232 | +0.767058% | 13,301 | 50.131% | 6,015 / 7,286 |

The nominal 64-line minimum is only 3,756 ticks below baseline in one
deterministic observation.  That 0.0015% movement is not a useful architecture
gain and does not justify changing capacity.

## Physical bound

The first 256/512 attempts used `virtual_combine_words=0`, which derives one
payload word for every line-table word.  They failed closed before ROI:

- 256 lines: `result_capacity_too_large (feeder=1024 result=4224)`;
- 512 lines: `result_capacity_too_large (feeder=1024 result=8320)`.

The accepted replacements keep eight FP32 response lines (128 words) and cap
the shared combiner pool at 3,968 words, so response plus combiner payload is
exactly 4,096 words.  Increasing line slots then adds only tags/control; it
does not silently increase result payload beyond the physical4K bound.

Under the current four-unit storage geometry, the corrected comparable lower
bound is 1,596,712 B at 16 slots, 1,714,080 B at 256, and 1,749,088 B at 512.
The corresponding storage reductions versus native are 49.733%, 46.038%, and
44.936%.  These are packed semantic ledgers, not synthesized area/Fmax.

## Why fewer writes did not win

The 512-line arm cuts MAA cache writes from 37,008 to 23,637, but total MAA L3
demand misses remain exactly 2,971 in every arm.  Capacity growth therefore
does not remove the first-write allocation misses identified in the matched
hybrid bottleneck audit.  It mainly turns repeated masked writes into delayed
full-line writes.

The strict B and A work is stable.  Backing/page cycle counters improve
slightly as writes fall, while the 512-line arm increases B-fetch and A-issue
cycles and still regresses overall.  This is consistent with a changed
retirement/cache schedule, not with a correctness or semantic-work change.
The evidence does not justify a stronger causal decomposition of the 0.77%
regression.

The result matches the earlier fixed-16-line policy sweep: transaction count
alone is not the current end-to-end bottleneck.  The next useful test is the
dense first-write/no-read-allocation bracket, which targets L3/DRAM misses
rather than only transaction count.

## Provenance and scope

- Matched source checkpoint: accepted strict non-fused CG NA256 r7.
- gem5 SHA-256:
  `182a6696a60983aa690fa6b4131592cff4408b380891fa31098f1f978cdada0d`.
- Feeder: fixed depth 64, one generated line per cycle.
- Geometry: logical16K/physical4K, four-way/four-bank combiner, eight response
  slots, 32 retirement credits, masked 64-byte writes.
- Raw accepted roots:
  `/data1/nier/dx100-runs/2026-08-28-lead-fixed-combiner-na256-c{32,64,128}-r1`
  and
  `/data1/nier/dx100-runs/2026-08-28-lead-fixed-combiner-na256-c{256,512}-r2`.
- Rejected guard roots preserve the uncapped 256/512 failures at suffix `r1`.
- Combined artifact ledger:
  `hybrid_cg_combiner_capacity_artifacts_2026-08-28.sha256`.

This is a short CG engineering gate, not a full-application or suite-wide
capacity conclusion.  The clear negative result is sufficient to reject an
NA1024/full-CG capacity launch in the current optimization loop.
