# SoA/JIT value-owner pool handoff — 2026-08-14

## Scope

The fixed value-line owner/coalescer pool is physically provisioned for 32
64-byte lines.  Runtime selection is deliberately fail-closed: only 4, 8, 16,
or 32 active owners are legal.  The selected prefix alone may accept a new
fill or an eviction; inactive owners remain free and invariant-checked.

The `--maa_soa_jit_active_value_owners` CLI option is wired through
`MAAConfig.py`, the `MAA` SimObject parameter, and `IndirectAccessUnit`.
Invalid direct SimObject values panic during allocation; invalid state-helper
configuration leaves no active owners and rejects new aliases.

## Correctness boundary

Generation/address response ownership is unchanged.  A response must still
match the exact active generation and physical address, duplicate/stale and
unknown responses fail closed, and a line with waiters cannot be evicted.
The original ordered Offset-chain alias application, single apply lane, final
WriteResp accounting, safety gates, and terminal invariants are unchanged.
Predicate-feeder code is intentionally outside this change.

## Exact modeled storage accounting

Maximum physical value payload provision is `32 * 64 = 2048` bytes per
indirect unit.  Active selections expose respectively 256, 512, 1024, and
2048 payload bytes for 4, 8, 16, and 32 owners.  This is a selection policy,
not dynamic allocation: trace records retain both
`max_physical_value_owner_lines=32` and the active owner count, plus their
payload-byte fields.  `fixed_value_owner_bytes` remains the exact compiled
state-object byte count reported by the simulator trace and is explicitly
separate from the modeled payload-byte accounting.

## Matrix and merge notes

`run_hybrid_rmw_soa_overlap_matrix.sh` adds matched active-owner treatments
for 4/8/16/32, verifies resolved config and `IND_SoaJitActiveValueOwners`,
checks trace closure, and carries the physical/active storage ledger.

Expected integration conflicts against the lead are confined to adjacent
SoA/JIT plumbing hunks in `MAA.py`, `MAA.hh/.cc`, `IndirectAccess.hh/.cc`,
`Options.py`, and `MAAConfig.py`; preserve any predicate-feeder additions when
resolving them.  No predicate-feeder or publisher behavior was modified here.
