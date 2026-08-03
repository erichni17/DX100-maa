# Logical SPD bridge-local lifecycle slice — 2026-08-03

## Completed scope

- `LogicalSPDCacheGem5Bridge` constructs and initializes exactly one Runtime
  per `maa_id` and
  exposes no guest registration, operation-admission, Packet, port, or MMIO
  path. `admissionClosed()` is constant true.
- Each MAA has one finite callback-owner slot with exact `maa_id`, monotonic
  generation, process-wide nonrepeating Runtime-incarnation identity, and
  bridge-local callback identity. Duplicate, stale-generation,
  wrong-incarnation, and wrong-callback acknowledgements cannot release
  ownership. Destroying and reconstructing a bridge cannot authenticate an old
  token even when its `maa_id`, generation, and first callback identity match.
- Runtime-incarnation allocation is atomic across bridge constructions. The
  final nonzero incarnation and callback identities are each usable exactly
  once; zero is an exhausted sentinel, and construction/claim fail closed
  rather than wrapping. Generation exhaustion remains fail closed.
- Quiescence, reset, abort progress, dirty-flush ownership, guarded teardown,
  sealing, and destruction-safety are explicit. Reset requires bridge and
  Runtime quiescence. Callback dirty ownership and the Runtime's generated
  abort-flush correlation are independent truths: acknowledging the callback
  can return Busy but cannot hide a Runtime dirty flush before its exact
  transport completion. Teardown refuses live state and verifies Runtime
  `sealed()` plus `destructionSafe()` before accepting.
- Runtime `ProductionStop`, `Poisoned`, and lifecycle-impossible statuses map
  to a persistent bridge `ProductionStop` state; reset cannot reopen it.
- Runtime construction is factory-injectable for host-only failure testing
  without changing Runtime. The optimized and ASan/UBSan host gate covers
  partial construction cleanup, one Runtime per MAA, stale/duplicate callbacks,
  destroyed-bridge token replay, incarnation/callback/generation exhaustion,
  reset, abort ownership, and a real Runtime dirty writeback flush concurrent
  with a bridge callback and held through the 511th line until the exact 512th
  acknowledgement, plus guarded teardown.

## Explicitly deferred

- Native MAA drain/checkpoint integration is not claimed;
  `nativeDrainIntegrated()` is constant false.
- No MAA-wide quiescence, checkpoint serialization, CPU-response draining,
  port retry, IF/FU/Table state, native admission, logical guest admission, or
  gem5 panic/audit wiring is added here. Those require the separately owned
  MAA/IF/port/FU/Table/admission paths.
- The bridge owner token models the bounded future adapter callback boundary;
  no cache request is issued by this slice. Runtime remains the sole authority
  for its existing transport abort drain, dirty writeback flush, and exact
  transport completion authentication.
