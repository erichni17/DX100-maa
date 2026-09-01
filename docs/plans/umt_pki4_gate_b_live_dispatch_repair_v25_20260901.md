# UMT PKI4 Gate-B live dispatch repair v25

Status: failure audit, validator repair, and no-launch successor review request
only. This change authorizes no build, systemd action, gem5/opcode/RTL launch,
evidence mutation, or Gate-B result claim.

## Failure audit

The failed campaign remains
`/data1/nier/dx100-runs/2026-09-01-umt-pki4-gate-b-lifecycle-v22-live`.
Its frozen contract SHA-256 is
`21a3dc8a50b6a6ebac3771e6e2b4e3b526638ecfcd257b65b77078d62924ad0b`
and its dispatch-reservation SHA-256 is
`88f5e4002ed7aad43285616a0450e927de073bb89a30f2b3a101cbe5892cc5aa`.
The no-clobber forensic receipt has SHA-256
`6259f981a508716811a1f20872985e39f67d5293f1f0467cf0f4d1b3f786706e`;
its capture script has SHA-256
`924d4edf8574ff87c482e57f25aa97141f1dc89887a1d44d76f26d0d4bafe150`.

The manager reserved both arms, launched only D32/G32, and then timed out while
rejecting `WorkingDirectory=!/home/nier`. The D32 unit subsequently proved
active/running under reviewed unit
`umt-pki4-gate-b-live-v22-d32-g32-20260901.service`, invocation
`786ac53e3d9f4f1ba76c4feec12b561e`, wrapper PID 1637210, and gem5 child PID
1641896. The D64/G31 unit and arm root were absent. These are time-bounded
observations, not terminal evidence. The forensic receipt is failure-only and
must never be promoted into a manager-live or dispatch receipt.

The host rendered the implicit user-manager working directory as exactly
`!/home/nier`. The repaired validator accepts only that byte-exact value. It
rejects the prior speculative values (`""` and `/home/nier`), normalization,
duplicate prefixes, trailing separators/space, and other homes.

## Recovery decision

The existing campaign is not resumable. Its contract freezes the old harness
and test SHA-256 identities, the original manager did not publish a manager-live
binding, and the two-arm dispatch receipt is absent. A repaired manager cannot
retroactively fill those names without crossing the independent-review and
no-clobber boundary. The running D32 arm may finish under its reviewed four-hour
systemd cap; its outputs remain orphan/failure evidence. No process, unit,
receipt, root, or reservation in this campaign may be stopped, reset, deleted,
rewritten, completed, or reused by the repair.

## Exact no-launch successor plan

A separately reviewed successor must use fresh identities:

- campaign root:
  `/data1/nier/dx100-runs/2026-09-01-umt-pki4-gate-b-lifecycle-v25-live`
- D32/G32 unit:
  `umt-pki4-gate-b-live-v25-d32-g32-20260901.service`
- D32/G32 root: successor campaign plus `/arms/d32-g32`
- D64/G31 unit:
  `umt-pki4-gate-b-live-v25-d64-g31-20260901.service`
- D64/G31 root: successor campaign plus `/arms/d64-g31`

Before any future launch authority, a new dry plan, implementation bundle,
contract, reservation schema instance, exact argv hashes, and independent PASS
must bind the repaired commit and reviewed file SHA-256s. The freezer must prove
all successor campaign, arm, unit, dispatch-manager, manager-owned, and receipt
paths absent. It must retain the existing exact resource limits, maximum-two
concurrency, service-owned no-clobber reservation, PID plus `/proc` start-tick
binding, invocation binding, terminal journal binding, and postprocessing gates.
It must also show that neither old unit is active and that no old PID identity is
being adopted; this check is ownership validation, not permission to stop a
process.

## Independent review request

Review is requested only for this validator repair and the no-launch successor
plan. Recompute the changed source/test/document hashes; confirm the positive
fixture is exactly `!/home/nier`; run the adversarial suite; confirm every other
working-directory representation fails before manager evidence publication;
confirm the v23 contract no longer validates against the changed implementation;
and confirm all v22 campaign evidence stays byte-identical. Review must reject
any proposal to bind the existing invocation, backfill manager or dispatch
receipts, launch D64 in the old campaign, reuse old units/roots, or treat the
forensic receipt as success evidence.

The requested decision is `PASS_REPAIR_AND_FRESH_SUCCESSOR_PLAN_NO_LAUNCH` or
`FAIL`. A PASS authorizes preparation and review of the fresh successor contract
only. It does not authorize build, systemd, gem5, opcode, RTL, remote Git, or
promotion actions.
