# Compact write-retirement A/B recovery (2026-08-24)

## Decision

**Reject and keep default-off.** The certified four-arm A/B is exact and the
mechanism is live, but it fails the predeclared performance gate: both kernels
must be nonregressing and at least one must improve first-window `simTicks` by
0.5%. SSSP ties exactly; HashJoin PRO improves only 0.012326154%.

No native baseline or full application was run. This result compares the
accepted eight-context hybrid against the same hybrid with compact A-write
retirement enabled.

## Frozen evidence

- Raw root:
  `/data1/nier/worktrees/codex-coordination/sessions/hybrid-compact-write-retirement-20260824-071509-d5b4789c/evidence/compact-write-retirement-0d88fb41-r2`
- Source: `0d88fb41ec6b8fcb8e5c1640809616ee8cab3663`.
- Certified gem5 SHA-256:
  `9fd99209470e51a8ee9e994b598969df4bf3480bdde8130ffd9bb14413a1c819`.
- Frozen Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
- All four `gem5.rc` files are zero, each log has one normal `m5_exit`, and
  each final stats file is nonempty.
- The copied build certificate passes its complete SHA-256 manifest.
- The 71-file recovery ledger is
  `experiments/analysis/hybrid_compact_write_retirement_recovery_2026-08-24.sha256`.

The systemd wrapper itself exited 127 after all four simulations because the
runner invoked `rg`, but user-systemd did not inherit Codex's private `rg` path.
Consequently the root's generated `results.tsv` contains only its header and no
top-level gate was written. Recovery validates the immutable raw artifacts
directly; it does not relaunch or overwrite any arm.

## Correctness and mechanism

- Both SSSP arms have the exact 69,633-vertex fingerprint, four eligible/routed
  logical windows, 65,536 old-result words, zero legacy words, and closed
  A/old-result issue-response ledgers.
- Both HashJoin PRO arms return exactly 65,536 matches, route all 8/8 logical
  windows, apply 131,072 aliases, use 4K physical SPD with 16K logical reorder,
  and close A issue-response ledgers.
- Configs differ only in
  `soa_jit_compact_write_retirement=false/true`; both retain eight contexts.
- Compact retirement is live: SSSP observes 4 enabled terminals and credit HWM
  31/32; HashJoin observes 8 enabled terminals and HWM 22/64. Both report zero
  compact-credit stalls.

## Performance

| Kernel | Baseline ticks | Compact ticks | Change | Decision |
|---|---:|---:|---:|---|
| SSSP old-result | 9,976,182,331 | 9,976,182,331 | exact tie | nonregressing, not meaningful |
| HashJoin PRO | 6,531,120,600 | 6,530,315,564 | 0.012326154% lower | below 0.5% threshold |

The HashJoin speedup is `1.000123277x`. SSSP context-admission stalls fall from
239,267 to 237,445 and HashJoin stalls from 113,660 to 113,280, but neither
change is performance-relevant. SSSP old-result writes rise from 17,805 to
19,873 (11.614715%), showing that earlier A-context release perturbs result-line
coalescing without reducing latency.

## Hardware accounting

The mechanism keeps eight response credits per indirect unit. Persistent state
is 1,168 bits = 146 bytes per unit, or 584 bytes across four units. At most 512
transient payload bytes and 24 response-tag bits are live per unit. This cost is
small relative to the removed SPD payload, but a nonzero cost with no meaningful
speedup is still a rejection.

## Operational correction

Future durable runners must resolve every external utility before launching
gem5 or use baseline tools available in user-systemd (`grep` here). A missing
post-processing command must fail during preflight, not after expensive arms
finish. An inactive/collected unit must not be interpreted from default
`systemctl show` fields; journal exit status and frozen evidence are
authoritative.
