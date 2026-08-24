# Logical-tile page scheduler contract

This change introduces a standalone, payload-free scheduler control model in
`src/mem/MAA/LogicalTilePageScheduler.hh`. It does not copy or extend the
rejected `e2b226ed` test-only model.

The scheduler fixes the logical geometry at 16,384 elements split into four
4,096-element pages. Each descriptor supplies an exact FP32 or FP64 word size,
so its full-span alignment, page address offset, and page byte length are
derived rather than fixed at 4 KiB. Eight descriptor records and four existing
SPD frame identifiers are stored in fixed arrays; the scheduler owns no data
payload or dynamic storage.

One operation may be active. Materialize, dense store, unary scalar ALU, and
binary vector ALU shapes emit exact `NativeAction` tokens. Distinct binary
sources keep two frame leases through exact compute completion; a self-source
binary uses one source lease. Compute completion releases source frames but
keeps the dirty destination frame leased until the exact destination write
response. Destination readiness is published by that response, never by
compute completion. Materialize and dense-store readiness likewise follows
their exact response-bearing action.

Configuration rejects zero or non-monotonic generations, bad full-span
geometry, overlapping backing spans, incompatible types, and changes to any
descriptor referenced by the active operation. Completion rejects stale or
duplicate transactions, stale generations, and any action, descriptor, page,
frame, address, offset, or length mismatch. Transaction allocation stops at
the maximum identifier without wrapping; a focused idle-only seam covers that
boundary.

Validation is provided by
`experiments/scripts/run_logical_tile_page_scheduler_unit.sh`, which compiles
and runs optimized and ASan/UBSan variants with leak checking disabled, then
runs the Python source-contract check. The C++ test covers FP32 and FP64
geometry, all four shapes, distinct and self-source vectors, active
reconfiguration, unavailable frames, descriptor aliases, exact response
matching, duplicate responses, response-gated readiness, and transaction
exhaustion.
