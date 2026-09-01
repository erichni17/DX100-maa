# Shared-payload rollback production gate — 2026-09-01

## Decision

**PASS for the focused production-path blocker.** The ownership algebra that
was local to `IndirectAccessUnit::consumeVirtualResponses` is now the bounded
`maa::SharedPayloadTransfer` helper, and all three IndirectAccess insertion
paths use it. The focused unit executes a failed final-use insertion and proves
exact rollback and retry closure under optimized and ASan/UBSan builds.

This closes the missing execution evidence identified by
`shared_payload_successor_independent_review_2026-09-01.md`. No gem5 run was
launched because the extracted production helper provides deterministic access
to the otherwise pressure-dependent failure branch.

## Production ownership algebra

`SharedPayloadTransfer.hh` contains fixed transaction metadata only. It owns no
payload array, vector, or byte buffer and performs no payload allocation. The
configured `VirtualCombinePayloadStore` remains the sole payload owner.

| Transition | Fanout | Response `WordRef` | Response credits | Pool occupancy |
|---|---:|---|---:|---:|
| nonfinal begin | -1 use | unchanged | unchanged | unchanged |
| final begin | -1 use | exact ref removed | slot reserved, global reserved, and response payload each -1 | unchanged |
| failed insert rollback | +1 use | same ref restored | all three +1 | asserted `used == combine + response_payload` |
| successful final commit | unchanged | ref remains in combiner | response credits remain released | asserted conserved; transfer/high-water recorded |

The helper also binds each non-copyable transaction to its creating helper and
state. Unbound, foreign-owner (stale), and already-resolved rollback attempts
return distinct failures without mutating ownership.

`IndirectAccess.cc` constructs one helper for the response-consumption call and
uses it at the lookup-completion, packed-response, and retained-shared-response
insertion sites. Failure still rolls back before the same capacity stall/retry;
success still commits before response progress. Non-shared mode remains a
metadata-only no-op, matching the previous lambdas.

## Focused execution

`tests/maa/shared_payload_transfer_test.cc` configures the real fixed payload
store and fanout metadata with two logical uses of one word. It executes:

1. a nonfinal consume and copied combiner insertion;
2. a final-use begin that removes the exact source `WordRef` and all three
   response credits without changing pool occupancy;
3. a simulated insertion failure;
4. rollback proving the same ref, per-word/total fanout, counters, and occupancy;
5. retry, adoption of the exact ref by the combiner, and successful commit;
6. release of both combiner words and exact empty closure.

A separate final-use transaction rejects illegal, stale-owner, and double
rollback attempts and verifies that the intervening rejected operations do not
alter the removed or restored ownership state. A compile-time size bound keeps
transaction metadata at or below 64 bytes.

## Validation

All commands completed with exit status zero:

- `tests/maa/run_shared_payload_transfer_unit.sh`
  - optimized: `PASS shared payload transfer`
  - ASan/UBSan: `PASS shared payload transfer`
- `tests/maa/run_virtual_source_fanout_unit.sh`
  - optimized and ASan/UBSan passed
- `tests/maa/run_virtual_combine_payload_store_unit.sh`
  - optimized and ASan/UBSan passed
- `tests/maa/run_virtual_response_payload_store_unit.sh`
  - optimized and ASan/UBSan passed
- `util/style.py -m`
- `git diff --check`
- `scons build/X86/mem/MAA/IndirectAccess.o build/X86/mem/MAA/MAA.o -j8`
  - `IndirectAccess.o` SHA-256
    `d0e27f043022020f20f2d439f381a168b8c86bf59169e6fb9121963b11b96535`
  - `MAA.o` SHA-256
    `c19a92665e916e2981474de8e49d71e122dd2a6dc5a85850a3449f2a68187202`

The object build emitted only the existing `MAA.cc` variable-tracking retry
note and the configuration's deprecated-namespace warning.

## Residual risk

The unit executes the exact production helper and real payload/fanout classes,
but it does not run a complete gem5 event flow or validate trace/stat emission
under naturally occurring pool pressure. Integration risk is bounded by the
three direct IndirectAccess call sites and successful object builds. A future
full-workload run may add end-to-end evidence, but is not required to close the
focused rollback-algebra blocker and would not be performance evidence.
