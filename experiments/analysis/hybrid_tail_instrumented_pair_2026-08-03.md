# Instrumented hybrid-tail pair

## Outcome

The fresh shared-checkpoint pair completed with exact output hash `7228541527853630339`: native **40,153,518** versus transparent **45,122,080** simTicks (delta **4,968,562**).

After all four pages were ready, **5,245,880** ticks remained to controller retirement. They reconcile exactly to STREAM busy **4,925,368** (93.89%) plus ALU busy **320,512** (6.11%); producer-not-ready, IF-full, slot-owned, serialization, runnable, transition, other, and inactive are all zero. This is blocker residency, not a causal speedup estimate.

All **5,306** recorded producer backing writes completed by all-ready, controller backpressure was **0**, **2,052** consumer packets were accepted (513/page), controller bookkeeping retirement took zero ticks, and the remaining ROI epilogue was **38,186** ticks.

## Evidence boundary

Accepted arms reaudited: **5**. Fresh completed arms: **2**. Preserved failed launch attempts: **1** (configuration failure before any completed arm). There is one observation per completed arm and no variance claim.

The accepted pair's all-ready point was 298,915 ticks after the native endpoint; in the fresh pair it was 313,626 ticks before it. That alignment is not replicated and is not used as causal evidence.

## Falsifiable next test

Hold the accepted workload binary and instrumented gem5 fixed, then intervene on one consumer service constraint only. The intervention must reduce STREAM-busy residency by the same simTick amount without creating producer-not-ready, IF-full, output, or provenance changes.
