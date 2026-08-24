# One-shot hybrid full-result audit — 2026-08-24

This is a read-only, one-shot classification.  It neither waits for a root nor
starts a native, full, or recovery simulation.

## Current CG and IS roots

The worker's sandbox-local process snapshot contained no matching CG, IS, or
gem5 owner, but that view was not authoritative for host user-systemd units.
A lead-side systemd check after integration confirmed both services remain
active: CG MainPID 1856183 and IS MainPID 1753022. Process absence in a worker
PID namespace must not be used to classify host service liveness.

| Root | One-shot phase/classification | Why it is fail-closed |
| --- | --- | --- |
| `/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-baf142f7-r1` | checkpoint phase; `incomplete` | only `checkpoint.log`/manifest are present; no restore log or zero restore exit, terminal certificate, gate, final config/stats, or hash ledger. |
| `/data1/nier/dx100-runs/2026-08-24-is-scalar-soa-full-a44aaa60-r5` | restore reached ROI end; `incomplete` | checkpoint exit is zero, but the restore has neither one `m5_exit`, explicit zero restore exit, terminal status, NAS verification, nor the required full row. |

Those roots are live but not terminal-valid and contribute no performance
result. Their artifact classification remains `incomplete`; liveness does not
relax any terminal or correctness requirement.

## HashJoin PRH full root

The raw root is
`/data1/nier/dx100-runs/hashjoin-hybrid-prh-full-d7d29bf5-20260824-061147`.
Its PRH log has exactly one cardinality marker (`result=2000000`) and one
`m5_exit`.  Its guest mechanism marker closes the first pass:

```
first_eligible=240 first_routed=240
second_eligible=0 second_routed=0 second_tails=1024
first_scatter_4k_actions=984 second_scatter_4k_actions=1024
```

This is algorithmically expected for this fixed full input, not a guest
instrumentation or hybrid-routing bug.  The first histogram operates on the
four large thread chunks, so complete 16K windows exist and route.  PRH's
shifted histogram (`radix_cluster_maa`) instead receives first-pass radix
partitions.  Its candidate path only routes when an individual partition has
at least 16,384 tuples; all 1,024 observed shifted calls took the existing 4K
tail path.  The shifted scatter remained live, as its 1,024 physical actions
show.  Zero shifted logical-window eligibility therefore means **tail-only
shifted-pass coverage**, not that PRH skipped the second phase.

The first statistics-window `simTicks` value is **46,706,090,681**.  It is
recorded only as an observation from this raw, pre-hardening root.  That root
has no frozen `mechanism.status` contract and no terminal manifest/gate/hash
ledger, so it is `incomplete` under the hardened classifier and is not exact
terminal correctness evidence or performance-promotable evidence.

## Contract change

The runner now freezes a per-kernel `mechanism.status` file after the exact
cardinality, exit, routing, and statistics ledgers close.  It distinguishes:

- PRO: shifted pass `not_applicable` and zero/zero is required.
- PRH: zero/zero is accepted as `tail_only`; positive matched counts are
  accepted as `routed`; mismatched counts still fail.

The classifier independently checks that file against the guest marker.  A
terminal-valid HashJoin record now reports exact terminal correctness,
first/shifted mechanism coverage, and `performance_promotable=false`: the
runner is candidate-only and contains no matched baseline.  Tail-only PRH
also explicitly lacks a routed shifted-pass window.  No nonzero coverage was
forced and no simulation was rerun.
