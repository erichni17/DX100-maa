# Logical SPD bridge-local lifecycle slice — 2026-08-03

## Completed scope

- `LogicalSPDCacheGem5Bridge` constructs and initializes exactly one Runtime
  per `maa_id` and
  exposes no guest registration, operation-admission, Packet, port, or MMIO
  path. `admissionClosed()` is constant true.
- Each MAA has one finite callback-owner slot with exact `maa_id`, monotonic
  generation, and monotonic identity. Duplicate, stale-generation, and
  wrong-identity acknowledgements cannot release ownership.
- Quiescence, reset, abort progress, dirty-flush ownership, guarded teardown,
  sealing, and destruction-safety are explicit. Reset requires bridge and
  Runtime quiescence. Abort retains a live/dirty callback owner until its exact
  acknowledgement. Teardown refuses live state and verifies Runtime `sealed()`
  plus `destructionSafe()` before accepting.
- Runtime `ProductionStop`, `Poisoned`, and lifecycle-impossible statuses map
  to a persistent bridge `ProductionStop` state; reset cannot reopen it.
- Runtime construction is factory-injectable for host-only failure testing
  without changing Runtime. The optimized and ASan/UBSan host gate covers
  partial construction cleanup, one Runtime per MAA, stale/duplicate callbacks,
  reset, abort ownership, a real Runtime dirty writeback flush held through the
  511th line until the exact 512th acknowledgement, and guarded teardown.

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
