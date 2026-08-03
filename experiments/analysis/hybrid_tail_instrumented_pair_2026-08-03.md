# Instrumented hybrid-tail pair

## Outcome

The fresh shared-checkpoint pair completed with exact output hash `7228541527853630339`: native **40,095,613** versus transparent **45,237,890** simTicks (delta **5,142,277**).

After all four pages were ready, **5,256,522** ticks remained to controller retirement. They reconcile exactly to STREAM busy **4,936,010** (93.90%) plus ALU busy **320,512** (6.10%); producer-not-ready, IF-full, slot-owned, serialization, runnable, transition, other, and inactive are all zero. This is blocker residency, not a causal speedup estimate.

All **5,319** recorded producer backing writes completed by all-ready, controller backpressure was **0**, **2,052** consumer packets were accepted (513/page), controller bookkeeping retirement took zero ticks, and the remaining ROI epilogue was **38,186** ticks.

The directly observed STREAM instruction counts were native STRRD/STRWR **1/1** and transparent **5/4**. This supports the interpretation that page refill/drain work is concentrated in STREAM-busy residency, but it does not make the residency counter causal.

## Evidence boundary

Accepted arms reaudited: **5**. Fresh arm launches: **5** (**4** completed across 2 pairs, **1** failed before result). The primary reported pair has **2** arms and reuses the accepted workload binary; the first completed pair used a rebuilt binary and is retained as superseded evidence. There is one observation per completed arm and no variance claim.

The accepted pair's all-ready point was 298,915 ticks after the native endpoint; in the fresh pair it was 291,090 ticks before it. That alignment is not replicated and is not used as causal evidence.

## Falsifiable next test

Hold the accepted workload binary and instrumented gem5 fixed, then intervene on one consumer service constraint only. The intervention must reduce STREAM-busy residency by the same simTick amount without creating producer-not-ready, IF-full, output, or provenance changes.
