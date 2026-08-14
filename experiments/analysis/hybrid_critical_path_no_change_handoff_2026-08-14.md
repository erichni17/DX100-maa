# Hybrid critical-path no-change handoff — 2026-08-14

## Decision

**No new scheduler or retirement mechanism is selected.**  The existing
logical-16K / physical-4K SoA/JIT RMW remains unchanged.  The current source
commit is `5e94e22020451202100a7f03fc2b4f89348581fe`.

This handoff intentionally does not duplicate active context-64, dual-RMW
publication, 2K+2K ping-pong, or owner-128 work.  It also does not turn an
already-provisioned owner prefix into a new hardware claim.

The incremental cost of this no-change decision is **0 B of hardware state
and 0 new ports**.  The retained contract is unchanged: a 16K Row/Offset
reorder scope, 4K physical SPD/result payload, exact ordered RMW, and the
general cache request path for A and value lines.

## Evidence that is valid for the audit

| Evidence root | Matched configuration | Key result | What it establishes |
|---|---|---|---|
| `/data1/nier/dx100-runs/2026-08-14-soa-owner-prea32-64-ef039c96-r3` | API micro; c32, owner64, pre-A, 4K physical result; two replicas | `72,364,974` ticks, exact hash `2761840269561229581` in both replicas | The post-owner64 micro profile and exact A/value/WriteResp closure.  This API micro uses the separate-predicate form, not masked indices. |
| `/data1/nier/dx100-runs/2026-08-14-gzp-owner-prea32-64-22ffe3de-r1` | Full GZP; c32, owner64, pre-A; two replicas | `7,083,313,009` ticks, exact hash `11225737641199706160` in both replicas | Application-level owner64 and pre-A counters; this earlier application pair also used the separate-predicate form. |
| `/data1/nier/dx100-runs/2026-08-14-soa-jit-apply-lanes-p16v32-08845927-r1` | Shared-checkpoint API; 1/2/4 apply lanes | Lanes 2 and 4 are 4.538% and 4.426% slower than lane 1 | A wider apply/service arbiter is a measured negative candidate. |
| `/data1/nier/dx100-runs/2026-08-14-soa-owner-prea64-128-5b02ced5-r1` | API micro; c32, pre-A, 64 versus 128 active owners; two replicas | 128 owners is 1.004728173x faster and removes micro value stalls | Owner128 is an existing, already-provisioned prefix.  It is not a distinct new scheduler mechanism and awaits the lead-owned masked full-GZP gate. |

The accepted checkpoint remains the governing storage and semantic record:
`experiments/analysis/hybrid_optimization_checkpoint_2026-08-14.md`.

## Current critical-path profile

The valid c32/owner64/pre-A API replicas each close the traffic ledgers with
29,689 selected aliases, 3,079 rejected predicates, 11,478 value issues and
responses, 126 A reads and responses, and 126 A writes and WriteResps.  Their
value profile is 5,656 ready hits, 12,555 merged waiters, 11,350 evictions,
and 106,454 value/lookahead stalls.  Pre-A issues/uses close at 957/957, with
551 values ready when their A response arrives.  The configured one apply lane
has high water one per instruction; context high water reaches all 32 slots.

The matched full-GZP c32/owner64/pre-A replicas close at 949,411 selected and
50,013 rejected entries, 823,275 value issues/responses/fills, and 509,830 A
read issues/responses/write issues/WriteResps.  Pre-A issues/uses close at
949,027/949,027 and 525,264 are ready at A-response time (55.35%).  The
application still records 819,371 owner evictions, 43,249 value stalls, and
3,428,318 context-full claim attempts, while its one apply lane is at high
water one for each of the 61 completed logical windows.

These are bounded-resource pressure counters, not permission to release a
modified A line early: a context must remain live until its authenticated
WriteResp to preserve exact RMW ordering and response ownership.

## Service and retirement audit

1. `serviceSoaJitBuild()` admits one completed Row/Offset A line only into a
   free context.  The c32 profile reaches that capacity, so a larger context
   pool is the only direct response to its claim stalls; context64 is already
   an excluded active investigation.
2. `serviceSoaJitLookahead()` permits pre-A value requests while a context is
   in `AwaitARead`, but applies only after the authenticated A response.  The
   55.35% GZP ready-at-A-response fraction confirms that this overlap is
   actively used.  The alternative of widening apply lanes was measured and
   rejected above.
3. `retirementWriteComplete()` calls `completeSoaJitWrite()` and immediately
   schedules execution after the matching A WriteResp.  There is no polling or
   deferred context release to remove.
4. The valid owner64 GZP run reports both
   `virtual_retirement_native_deferrals=0` and
   `virtual_retirement_queue_deferrals=0`.  SoA/JIT A writes use the normal
   cache-forced request path rather than the virtual-retirement queue; there
   is no measured serialized retirement FIFO to widen or bypass.

## Ranked disposition

| Rank | Candidate/resource | Evidence-based disposition |
|---:|---|---|
| 1 | Owner64 -> owner128 prefix | Do not duplicate: the lead owns the masked full-GZP confirmation.  It has no incremental physical bytes or ports because all 128 owner records already exist. |
| 2 | Context capacity beyond 32 | Out of scope here: c32 fills its scoreboard, but context64 is already active elsewhere. |
| 3 | Apply/service lanes 2 or 4 | Rejected: both exact shared-checkpoint treatments regress by more than 4%. |
| 4 | Early A-context release or altered WriteResp retirement | Rejected: it would violate the exact ownership contract, and the deferred-retirement queue counters are both zero. |
| 5 | More index credit, result retention, banks, or final-retirement width | Rejected by the accepted checkpoint and prior probes: each is noncritical or regresses. |

## Lead-owned masked composition gate status

The intended exact evidence root is
`/data1/nier/dx100-runs/2026-08-14-gzp-combined-f5a74b68-r2`.  Its c32,
masked-index, pre-A owner64 and owner128 invocations are not valid results:
their archived `inputs/configs/deprecated/example/se.py` rejects
`--maa_soa_jit_pre_a_value_lookahead` before simulation.  Therefore those
arms provide no `simTicks`, output, or traffic evidence and must not be
reported as a negative owner128 result.  No repair, relaunch, or duplicate was
performed from this worktree.

## Handoff

Keep the existing hybrid and wait for a valid lead-owned masked owner64/128
composition result before changing the owner selection.  A future scheduler
proposal needs a new counter that identifies a resource not covered above,
plus an exact same-checkpoint two-replica gate with output equality,
`simTicks`, and A/value/WriteResp traffic closure.  Until then, this audit
supports no simulator-side change.
