# Hybrid-tail causal forwarding review

Verdict: **ACCEPT for the exact uninterrupted, one-MAA experiment scope at
`d71e25de`; do not accept timing evidence or broader checkpoint/multi-MAA
promotion yet.**

Reviewed `b59f85e2` through `d71e25de` against code base `0108d9b`.
`c2de176` is a sibling evidence-provenance commit with merge-base `0108d9b`;
it changes no simulator source and is not an alternate code base. The later
`b9f29747` fragment-priority change arrived during review and is not part of
this verdict; it changes cross-address retirement priority, not the safety
argument below.

## Findings and blockers

1. **[P1, broader use] Mid-treatment checkpoint/drain is unsafe and must stay
   out of scope.** The new live state consists of owning packet pointers in a
   scheduled multiset, a separate address-ownership set, an ordinal, and an
   event (`src/mem/MAA/MAA.hh:879-942`). `MAA` does not override `drain`,
   `serialize`, or `unserialize`; it therefore inherits the default immediate
   `Drained` result and empty serialization (`src/sim/sim_object.hh:282-316`).
   A checkpoint while a forward is pending can consequently omit the owned
   read packet and event while preserving surrounding architectural memory,
   leaving the restored STREAM request without its response. The destructor
   panic (`src/mem/MAA/MAA.cc:373-381`) detects a nonempty queue only during
   destruction; it does not make checkpointing or early teardown drain-safe.
   The analyzer accurately discloses this limitation
   (`experiments/analysis/analyze_hybrid_tail_issue_ready.py:449-463`), and the
   reviewed runner avoids it by taking the shared checkpoint before treatment
   (`experiments/scripts/run_hybrid_tail_issue_ready_pair.sh:69-96`). This is
   not a blocker for the exact pair, but it is a correctness blocker for any
   mid-treatment checkpoint, fork, or generalized platform claim.

2. **[P2, validation gap] The safety-critical state machine has no executable
   C++ unit test.** The added tests assert source substrings and relative text
   positions (`experiments/tests/test_hybrid_tail_instrumentation.py:45-120`);
   they do not instantiate the queue to exercise older deferred packets,
   write-ack-before-forward, same-address successors, queue-full fallback,
   one-line-per-cycle service, packet deletion, or STREAM rejection. Source
   inspection supports the scoped acceptance, but a behavioral unit with
   packet-ownership assertions is required before treating this as a durable
   regression gate.

No additional correctness finding was found in the exact uninterrupted,
one-MAA path.

## Safety audit

- **Exact-address order:** forwarding requires no older deferred packet and no
  existing scheduled forward (`src/mem/MAA/Port.cc:57-83`). A scheduled address
  blocks both ordinary sends and FIFO release (`Port.cc:89-103,273-292`). If the
  retirement acknowledgment wins the race, response handling erases the
  outstanding write but FIFO release still observes the scheduled-address set
  (`Port.cc:796-825`); service removes that set only immediately before
  delivering the copied response and then tries the FIFO (`Port.cc:334-360`).
  Later same-address traffic therefore cannot overtake either the retirement
  write or an older deferred packet.

- **Single ownership/no duplicate send:** a successful schedule returns before
  normal outstanding-map insertion (`Port.cc:74-104,195-271`). The scheduled
  packet is delivered directly to the STREAM unit and deleted exactly once
  (`Port.cc:339-355`); it is never also sent through a cache/memory port and
  cannot receive a normal timing response. Queue-full returns false before
  taking ownership and falls through to exact-address deferral
  (`Port.cc:295-330,85-103`).

- **Payload lifetime:** the producer packet's 64 bytes are copied into the
  already allocated consumer packet before scheduling (`Port.cc:314-320`). A
  retirement acknowledgment can delete or repurpose its own packet without
  invalidating the delayed consumer payload.

- **One mux/event and bounds:** the queue, address set, ordinal, and event are
  single members of the `MAA` object (`MAA.hh:915-942`). Service removes one
  entry and schedules the next no sooner than one clock period later
  (`Port.cc:334-360`). Issue-ready mode rejects a configured retirement bound
  above 64 and zero forward latency (`MAA.cc:175-182`); scheduling caps queue
  occupancy at that configured bound, which the reviewed arm fixes at 64
  (`Port.cc:295-330`; `run_virtual_tile_consumer_case.sh:628-646`). Address-set
  insertion is unique per queued line. A full queue falls back to the ordinary
  exact-address FIFO rather than dropping or duplicating the read.

- **Page-release legality:** a page cannot release until scanning and issue
  counts are complete. Completion is additionally required in control mode;
  issue-ready mode instead requires zero pending unforwardable writes
  (`src/mem/MAA/IndirectAccess.cc:3004-3016`). Only an unmasked 64-byte write is
  marked forwardable (`IndirectAccess.cc:3055-3105`). Partial or masked writes
  increment the per-page gate and decrement it only on acknowledgment
  (`IndirectAccess.cc:3108-3135`). The retirement packet is handed to MAA before
  readiness is reconsidered (`IndirectAccess.cc:3197-3208`).

- **Generation/stale delivery:** a scheduled entry has no explicit instruction
  generation, but uninterrupted execution cannot recycle the STREAM unit while
  the response is pending: the request contributes to `my_sent_requests`, and
  instruction execution resumes/completes only after `my_received_responses`
  catches it (`src/mem/MAA/StreamAccess.cc:449-530`). This is sufficient for the
  exact run. Cancellation, checkpoint restore, or a future path that resets a
  STREAM unit with pending responses would need an explicit generation/cancel
  contract; none exists today.

- **One-MAA restriction:** the evidence runner explicitly configures and checks
  `num_maas=1` and one indirect unit (`run_virtual_tile_consumer_case.sh:628,
  665-683,1086-1155`), while the analyzer requires those resolved values
  (`analyze_hybrid_tail_issue_ready.py:122-147`). The global mux is therefore
  evaluated only in that scope. Multi-MAA replication/arbitration is not
  evidence from this candidate.

- **Stats/reconciliation:** the runner collects release-with-pending, copied
  bytes, scheduled/delivered forwards, high-water, queue-full, and both
  deferral counters (`run_virtual_tile_consumer_case.sh:725-805`). The analyzer
  requires pending-word trace reconciliation, scheduled = delivered, copied
  bytes = scheduled x 64, `0 < high_water <= 64`, no queue-full, and no fallback
  deferrals (`analyze_hybrid_tail_issue_ready.py:225-279`). Control activation
  must remain zero. These are appropriate fail-closed checks for the exact
  evidence arm.

- **Area accounting:** the report labels 5,312 bytes as a packed target-semantic
  treatment-only lower bound, separates the existing and added 4,096-byte
  payloads, leaves the combined total unset, and explicitly excludes dynamic
  `Packet`/`Request`, multiset, unordered-map/set nodes, and requester vectors
  (`analyze_hybrid_tail_issue_ready.py:393-433`). This is appropriately scoped;
  it is not simulator heap measurement or an implementation area estimate.

- **Selector race/unlike-arm serialization:** the pair runs the unlike arms in
  a foreground loop and checks selector absence before and after each arm
  (`run_hybrid_tail_issue_ready_pair.sh:98-125`). The case runner validates the
  exact treatment record, compares the copied selector, then atomically moves
  the shared selector to the arm output and confirms absence
  (`run_virtual_tile_consumer_case.sh:702-713`). `d71e25de` therefore closes the
  stale-selector reuse race without making the arms concurrent.

## Validation performed

- `git diff --check 0108d9b..d71e25de`: PASS.
- `bash -n` on both changed runners: PASS.
- 56 focused Python unit/source tests spanning issue-ready analysis,
  instrumentation, transparent-controller contracts, attribution, and XRAGE
  runner ABI: PASS.
- No gem5 simulation was run, and no timing result was accepted.
- No local gem5 build tree exists in this worktree; a from-scratch build was
  not cheap enough for this source-only review, so no object-build claim is
  made.

## Handoff

**ACCEPT** `d71e25de` for the exact treatment-neutral checkpoint followed by an
uninterrupted, serialized, one-MAA pair. **Block broader promotion** on (1)
drain/checkpoint serialization or an explicit fail-closed prohibition, (2) a
behavioral C++ queue/ownership test, and (3) separate multi-MAA arbitration
validation. Timing evidence remains unreviewed and unaccepted.
