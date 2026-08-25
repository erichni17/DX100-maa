# Independent SSSP physical-SPD prefetch-boundary review (2026-08-24)

## Decision

**Do not integrate the observed draft policy yet.**  The stride-prefetch
diagnosis is plausible and is the best explanation of the exact 4,096 boundary,
but the frozen log does not record request provenance and the accepted small
run is not a matched cache experiment.  More importantly, the draft's
classification/response contract can weaken fail-closed demand behavior.

The failed full S22 root is failure provenance only.  Its `restore.log` ends at
tick `239082572292` in `SPD::check_tile_element_id()` with element 4,096 and a
`BaseCache::sendMSHRQueuePacket -> CpuSidePort::recvTimingReq` stack.  It has no
terminal fingerprint, coverage record, or final stats window, so it supports no
correctness or performance claim.

## What the evidence and source establish

The accepted small runner and failed full runner use the same 64-byte lines and
16K-logical/4K-physical SPD geometry, but their cache policies differ:

| cache policy | accepted small | failed full S22 |
|---|---|---|
| L1D hardware prefetch | none | `StridePrefetcher` |
| L1I hardware prefetch | none | `StridePrefetcher` |
| L2 hardware prefetch | none | `StridePrefetcher` |
| MAA L2/L3 exclusion | absent | both `--maa_l2_uncacheable` and `--maa_l3_uncacheable` |

The small run also uses a different graph/options and omits several full-run MAA
knobs, so it is evidence that the repaired algorithm can work, not a controlled
prefetch A/B.  With the full exclusions, an L1D stride prefetch over cacheable
SPD is the relevant new path.

The exact packet transformation is subtle:

1. `Queued::DeferredPacket::createPkt()` creates a new `Request` with flags `0`,
   sets `taskId(Prefetcher)`, and wraps it in `HardPFReq`.
2. On the L1 miss, `Cache::createMissPacket()` reuses that same `Request` but
   creates the downstream packet as `ReadSharedReq`.
3. Therefore the MAA can receive a real L1 stride prefetch as
   `ReadSharedReq` with `pkt->isPrefetch() == false` and
   `req->isPrefetch() == false`.  `req->taskId() == Prefetcher` and the
   prefetcher's requestor ID survive.  **`req->isPrefetch()` is not preserved
   and cannot be the discriminator in this tree.**

The functional-unit audit does not support the claim that a 4,132-element
RangeFuser result caused this panic.  `MAA` allocates StreamAccess, ALU, and
RangeFuser with `physical_tile_elements`; RangeFuser caps its output index at
that value; normal ALU/stream sizes ultimately pass `SPD::setSize()`, which
rejects more than physical capacity; and every payload accessor checks the
physical bound.  Logical-page controller micro-ops use bounded physical
subspans.  The printed 4,132 is the frontier cardinality.  In the frozen guest
source, exact-CPU batches are outside the bounded-SPD branch, and the host SPD
loop is guarded before use.  Direct RangeFuser/ALU/stream access would also not
produce the captured CPU/cache stack.

`Invalidator` is not the initiating source either.  It deliberately tracks the
logical cacheable address aperture, while its data ingestion still writes the
physical SPD.  This makes a phantom out-of-physical cache line especially
undesirable: tracking it can later make invalidation ingest nonexistent SPD
data, while not tracking a resident line leaves stale data outside invalidation
ownership.  A dropped speculative request must touch neither SPD state nor the
invalidator and must leave no resident cache line.

Reserved logical-page frame lanes and response-bearing completion-owned lanes
remain architectural ownership boundaries.  Their checks currently occur in
`tryTiming()` and the normal request handler.  A fast drop before `tryTiming()`
must repeat those checks; the observed draft bypasses them.

## Safe policy contract

A request may take a nonbinding padding-drop path only when **all** of these are
true:

- it is a request-bearing, response-requiring `ReadSharedReq`, never
  `ReadExReq`, a write, writeback, or another command;
- hardware-prefetch provenance is exact for this implementation:
  `req->taskId() == context_switch_task_id::Prefetcher` (packet/request
  prefetch bits may be logged but are not sufficient or required here);
- it targets the cacheable SPD data range and an existing ordinary tile, not a
  reserved frame lane or a completion-owned lane;
- it is one aligned, fully byte-enabled cache line with overflow-safe geometry;
- the complete line is within one logical tile and begins at or beyond that
  tile's physical payload end.  A line that straddles either the physical or
  logical boundary is not droppable.

The drop must not call `setTileDirty`, `Invalidator::read/write`, or any SPD
accessor.  It must consume the accepted downstream packet exactly once and
return it through the existing timing-response ownership path.

An ordinary zero-valued `ReadResp` is **not safe**.  The L1 MSHR allocates the
hardware-prefetch fill, marks it prefetched, and can later satisfy a real demand
with zero without revisiting the MAA.  It also creates cache pollution in the
logical padding and escapes invalidator ownership.

The draft's `BadAddressError` avoids a fill for a lone prefetch, but it is not
yet a sufficient replacement: a demand can merge into the prefetch MSHR before
the response, and non-invalidating error handling extracts all targets.  That
demand then receives the error instead of being reissued to reach the required
MAA panic; the O3 error path is not a demonstrated architectural substitute.

A zero-filled `ReadRespWithInvalidate` is a plausible narrow implementation
because existing MSHR code services only the initial prefetch target, leaves
later CPU targets pending for a fresh request, and invalidates the transient
fill.  It is not approved without a packet-level test demonstrating no
resident line, no demand satisfaction, correct retry/ownership, and the later
demand panic.  An explicit cache-side “discard prefetch target and replay other
targets” response would also satisfy the contract.

All of the following must still panic: any out-of-physical demand; any
out-of-physical `ReadExReq`, write, or writeback even if tagged as prefetch;
partial physical/logical boundary crossings; malformed size/alignment/byte
enables or overflowed geometry; invalid tile IDs; reserved/owned lanes; and all
existing illegal range/command combinations.  In-physical reads retain normal
tile-readiness deferral, dirty marking, invalidator ownership, and real data.

## Residual diagnosis risk and required tests

The frozen backtrace proves a cache-originated read line, not that the first
MSHR target was a hardware prefetch.  An actual guest demand caused by a host
control-flow error, stale size, or compiler-generated over-read remains an
alternative until provenance is captured.  The 4K bound checks and repaired
exact-CPU control flow make those alternatives less likely, but do not replace
an observation.  The small/full cache difference is corroboration, not proof.

Before integration, require:

1. A real cache+MAA stride test that scans the last physical line and records
   downstream `ReadSharedReq`, both false prefetch predicates, preserved
   `Prefetcher` task ID, drop count, no SPD/invalidator mutation, no cache
   residency, and correct packet destruction.
2. A demand merged behind that prefetch; the prefetch must retire, the demand
   must reissue, and element 4,096 must panic.  Also test a later unmerged
   demand after the drop.
3. Negative packet cases for tagged `ReadExReq`, every write/writeback,
   partially crossing and malformed lines, disabled bytes, reserved lanes,
   completion-owned lanes, and invalid geometry; each must panic.
4. Positive first/last physical lines, all four degree-4 padding prefetches,
   multi-tile/multi-core routing, and a retry attempt carrying a different
   logical request, with unchanged valid-read and readiness behavior.
5. A fresh small SSSP gate using the full runner's exact L1/L2 prefetch and
   MAA-exclusion configuration, followed only then by a fresh full S22 gate.
   Acceptance still requires terminal exact output/coverage and final stats;
   the boundary-drop counter must be nonzero and all rejection/illegal-host-SPD
   counters zero.

Frozen inputs reviewed read-only:

- `.../sssp-tail-repair-7b6f9c21-r1/full/run/restore.log`
- `.../sssp-tail-repair-7b6f9c21-r1/full/run/command`

No implementation, simulator, evidence, or service was modified or launched.
