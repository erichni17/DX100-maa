# SoA/JIT 64-owner bounded micro gate — 2026-08-14

## Scope

This gate evaluates the existing fixed 128-line SoA/JIT value-owner pool with
32 active owners (control) and 64 active owners (default-off treatment). It
uses one newly generated 16K logical Row/Offset / 4K physical-SPD checkpoint,
then runs each arm twice from that same checkpoint. It does not run GZP.

The treatment is motivated by the accepted volume-only GZP mechanism audit:
with 32 active owners it recorded 878,506 owner evictions and 1,615,554
value/lookahead stalls. That identifies bounded owner churn as a measurable
candidate, independent of pre-A value lookahead, masked-index predicates, and
descriptor value carry.

## Boundaries and acceptance

The guest and normal cache timing are unchanged. Both arms freeze logical and
metadata scope at 16K, result storage at 4K, 32 contexts, lookahead eight,
predicate credits 16, index buffer four, one apply lane, pre-A disabled, and
sequential prefetch disabled. Only `active_value_owners` changes from 32 to
64. The fixed 128-entry coalescer is already provisioned, so this selection
adds no payload, tag, queue, or port state; it activates an existing bounded
prefix and must not be represented as a storage reduction.

Every replica fail-closes on output hash, result errors, ROI/exit markers,
resolved configuration, two terminal completions, predicate classification,
value read/response/fill closure, delivery/alias closure, and authenticated
A read/write-response closure. Promotion requires lower measured `simTicks`
and lower evictions in both replicas with identical output and work ledgers.
Otherwise the result is a rejection, not a speedup claim.

## Rejected hypotheses held fixed

- More gather-result retention: accepted GZP evidence eliminated all backing
  fallbacks yet regressed by 0.0552%.
- Wider apply lanes: two and four lanes were 4.538% and 4.426% slower.
- Pre-A value lookahead, masked-index predicate elimination, and descriptor
  value carry are deliberately excluded because they are separately assigned.
