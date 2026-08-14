# SoA/JIT value-owner pool handoff — 2026-08-14

## Scope

The fixed value-line owner/coalescer pool is physically provisioned for 128
64-byte lines. Runtime selection is deliberately fail-closed: only 4, 8, 16,
32, 64, or 128 active owners are legal. The selected prefix alone may accept
a new fill or an eviction; inactive owners remain free and invariant-checked.

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

Maximum physical value payload provision is `128 * 64 = 8192` bytes per
indirect unit. Active selections expose respectively 256, 512, 1024, 2048,
4096, and 8192 payload bytes for 4, 8, 16, 32, 64, and 128 owners. This is a
selection policy, not dynamic allocation: trace records retain both
`max_physical_value_owner_lines=128` and the active owner count, plus their
payload-byte and full-entry-byte fields.

Storage trace schema 2 reports the exact compiled coalescer bytes, owner-entry
bytes, payload and non-payload partitions, a reconstructed 32-owner baseline,
and the incremental bytes for the 128-owner provision. It emits both per-unit
and per-MAA totals, using the resolved indirect-unit count. The selected-entry
fields describe the active prefix; they do not imply dynamic physical
allocation.

For the validated X86 build, each owner entry is 136 bytes, the reconstructed
32-owner coalescer is 4,664 bytes, and the fixed 128-owner coalescer is 17,720
bytes. Thus the fixed physical extension is 13,056 bytes per indirect unit
over the 32-owner baseline (6,144 payload bytes and 6,912 tag/waiter/state
bytes). A MAA containing `U` indirect units pays `U * 13,056` extra bytes.

| Active owners | Selected payload/unit | Selected entries/unit | Selected-entry delta vs 32 | Fixed coalescer/unit | Fixed delta vs 32/unit |
|---:|---:|---:|---:|---:|---:|
| 32 | 2,048 B | 4,352 B | 0 B | 17,720 B | 13,056 B |
| 64 | 4,096 B | 8,704 B | 4,352 B | 17,720 B | 13,056 B |
| 128 | 8,192 B | 17,408 B | 13,056 B | 17,720 B | 13,056 B |

With the default and overlap-matrix setting of one indirect unit per MAA, the
per-MAA values equal the per-unit values above. For other resolved MAA widths,
schema 2 records the exact multiplied totals. The fixed delta is identical for
all active selections because the 128 entries are statically provisioned.

## Matrix and merge notes

`run_hybrid_rmw_soa_overlap_matrix.sh` adds matched active-owner treatments
for 4/8/16/32, verifies resolved config and `IND_SoaJitActiveValueOwners`,
checks trace closure, and carries the physical/active storage ledger.
The CLI, SimObject, allocation-time validation, and unit contract additionally
admit 64/128 selections; no full gem5 simulation was run for this extension.

Expected integration conflicts against the lead are confined to adjacent
SoA/JIT plumbing hunks in `MAA.py`, `MAA.hh/.cc`, `IndirectAccess.hh/.cc`,
`Options.py`, and `MAAConfig.py`; preserve any predicate-feeder additions when
resolving them.  No predicate-feeder or publisher behavior was modified here.
