# UMT PKI4 Gate-B fresh successor v25

Status: implemented and tested for independent review; no launch authorized.
This change performs no build, systemd launch, gem5 run, opcode execution, RTL
run, remote Git operation, or mutation of the failed v22 campaign.

## Fresh namespace

The only proposed campaign root is
`/data1/nier/dx100-runs/2026-09-01-umt-pki4-gate-b-lifecycle-v25-live`.
It contains exactly two arms, `arms/d32-g32` and `arms/d64-g31`, with units
`umt-pki4-gate-b-live-v25-d32-g32-20260901.service` and
`umt-pki4-gate-b-live-v25-d64-g31-20260901.service`. Every campaign, arm,
identity, dispatch-manager, manager-owned, receipt, snapshot, and derived path
is new. Freeze fails unless the entire successor campaign root is absent.
Dispatch fails unless both arm roots, both dispatch-manager roots, and all
manager-owned paths are absent.

The failed v22 campaign is read-only evidence. Its contract, reservation,
forensic receipt, and capture script are rehashed against the independent
repair review. Neither old unit, invocation, PID, arm root, manager path, nor
receipt may be adopted, completed, backfilled, or reused. Freeze and dispatch
independently require both old units to report the exact
`not-found/inactive/dead`, empty-invocation, zero-PID state.

## Frozen authority

The plan binds repair commit `f7ace47631793ead7ab3af0de192184defa03b7f`,
tree `bf08f415487c69010ad445e044ea68c18aa703f5`, independent repair review
commit `d0199bdc4a4d0b0fbf41427a960776a71478344e`, and review JSON SHA-256
`7e44107c67b3d3d9a12244d704f620ca2c545c733f47091b39692513b3e95873`.
That review authorizes only preparation and review after the old D32 arm is
terminal; it is not launch authority.

The existing v21 terminal build proof, exact proof audit, gem5 binary, original
dry-plan finalizer hash, audited successor finalizer hash, and exact validator
stdout hash remain mandatory. Freeze checks the proof audit before consuming
the proof, binary, or validator. The systemd identity validator accepts only
the byte-exact `WorkingDirectory=!/home/nier`; it performs no normalization and
publishes no manager-live evidence before that check passes.

## Exact lifecycle gates

The plan retains exactly two concurrent arms and a maximum concurrency of two.
Each command retains `CPUQuota=400%`, `CPUWeight=1000`, `MemoryHigh=14 GiB`,
`MemoryMax=16 GiB`, `MemorySwapMax=0`, and `RuntimeMaxSec=4h`. The immutable
implementation plan publishes the exact gem5, wrapper, and systemd argv arrays
and their canonical JSON SHA-256 values.

Freeze, dispatch, live identity, terminal capture, and postprocessing are all
no-clobber gates. The service reserves outputs and service receipts before
child admission. The manager binds unit invocation, wrapper PID and `/proc`
start ticks, exact systemd resources/argv, terminal status, and binary-safe
journal export. Postprocessing requires the service and manager terminal hash
chains before taking one `O_NOFOLLOW`, descriptor-stable, fsynced snapshot.
That one snapshot feeds both strict prefix streams and is rehashed through the
pinned canonical-v3 and canonical-v4 paths. Correctness and work equations run
before queue or lifecycle interpretation.

## Review boundary

The committed implementation plan, adversarial transcript, and review request
are immutable inputs to a new independent review. The requested PASS must bind
their exact SHA-256 values, both command hashes, all build anchors, the repair
lineage, the preserved predecessor anchors and terminal observations, the
exact working-directory value, maximum-two concurrency, and resource map.
Until that separate PASS exists, contract freeze and dispatch fail closed.

No C++/RTL equivalence, observed RTL timing, performance, mapped-cost,
generality, universal-default, or Gate-B promotion claim is made.
