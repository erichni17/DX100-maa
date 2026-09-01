# SSSP per-chunk admission promotion review

## Decision

**Reject promotion from commits `462a9cb8`, `545baa0b`, `af0adba9`, and
`c73a51d4` as currently evidenced.**  The per-chunk admission implementation is
sound at the reviewed granularity, and the mixed graphs encode the intended
hazards, but neither mixed integration case can reach gem5.  The full runner is
a candidate correctness/mechanism collector, not a performance-promotion gate.

## Blockers

1. **The mixed integration gates always stop before checkpoint creation.**
   `run_sssp_old_result_hybrid_small.sh:125-132` builds its oracle without
   `MAA`, so `main` selects `DeltaStep` (`sssp.cc:1247-1252`).  The fingerprint
   added by `af0adba9` is only at the end of `DeltaStepMAA`
   (`sssp.cc:1078-1080`).  Consequently the oracle logs contain GAPBS
   `Verification: PASS` but no `SSSP_FINGERPRINT`, and the assignment/grep at
   runner lines 188-192 exits under `set -e`.  The existing active-source and
   cross-owner roots contain only binaries, graph files, and `graph/oracle.log`;
   neither has a manifest, checkpoint, restore log, result, or completion
   marker.  The unit helper passing does not substitute for these integration
   cases.

2. **The same mixed path violates the stated no-native-rerun contract.**  It
   compiles and executes a host `DeltaStep` oracle at runner lines 125-132 and
   187-193 while still recording `native_arms=0` at line 265; the inherited
   comment at lines 135-138 says this is not a native/oracle rerun.  The full
   S22 runner itself is compliant: it reads and hashes the frozen native16 log
   and stats and records `native_checkpoint_execution=not_reused` and
   `native_guest_execution=not_reused`; it does not execute a native arm.

3. **`SSSP_OLD_RESULT_HYBRID_FULL_PASS` cannot authorize performance
   promotion.**  Validation requires exact output, completion, nonzero routed
   work, and mechanism closure, but it never compares candidate `simTicks` with
   the frozen native16 value or applies a performance threshold
   (`run_sssp_old_result_hybrid_full.sh:191-343`).  This is correctly disclosed
   as `comparison_status=measured_candidate_unpromoted` at line 377.  Treating
   its terminal `FULL_PASS` as a promotion verdict would therefore be a false
   positive.

## Reviewed conclusions

### Admission semantics: accepted

- Preflight and execution use the same `SsspHybridChunkFrontierWords` mapping.
  The tracker is reset before the implicit `omp single` barrier and is read-only
  during the parallel chunk loop.
- An active-source destination rejects only the source chunk that contains the
  hazardous edge.  Cross-owner tracking retains the first owner and marks it
  plus every later owner, so a destination seen in three or more chunks rejects
  all participants.  Invalid bounds/domain data still rejects every chunk.
- Destination tracking includes active work only, matching the MAA range-loop
  predicate.  Owners with incomplete logical windows are still included in the
  conflict proof, preventing a routed chunk from racing a legacy tail that
  shares its destination.

### Unsafe behavior: successful legacy path preserved, failure behavior tightened

For an unsafe chunk, `route_page` is false and the pre-existing ordered MIN,
final-distance reload, winner predicate, coherent publication/consumption,
cursor advance, and legacy-word accounting execute unchanged.  Cross-owner
admission prevents safe and unsafe chunks from sharing destinations.  Thus the
successful unsafe path preserves legacy duplicate order and results.

It is not *literally* identical for every failure: `545baa0b` changed the final
`hybrid_observed_words == hybrid_chunk_words` assertion from safe-only to
unconditional.  That is a fail-closed strengthening, not a successful-result
change, and should be described as such rather than as exact failure-behavior
preservation.

### Mixed variants: graph construction accepted, executable evidence absent

- `active_source` changes the first edge of source 1025 (chunk 1) to active
  vertex 1, while no other active edge names vertex 1.  Expected routing is
  3 safe / 1 active-source-rejected window.
- `cross_owner` makes sources 1025 and 2049 (chunks 1 and 2) share destination
  20481, which is non-active and otherwise belongs to the former.  Expected
  routing is 2 safe / 2 cross-owner-rejected windows.
- Each chunk otherwise has exactly 16,384 edges, so the expected full-page and
  fallback counts isolate admission rather than tail behavior.

These are the intended hazards, but blocker 1 means only the pure tracker unit
currently exercises them.

### Full S22 runner: correctness gate mostly fail-closed, with bounded edge cases

The runner pins the graph, guest, candidate gem5, Ramulator, checkpoint, source
helpers, exact fingerprint, two stats windows, wrapper status, terminal exit,
and response/write closure.  It rejects the old routed-zero result by requiring
`routed > 0`.

Two residual gate asymmetries remain:

- reason coverage is only aggregate
  (`unsafe == 0 || bounds + active + cross > 0`), so the validator cannot
  independently prove that every unsafe window has a reason; and
- `fallback_pages > 0` can reject a legal all-routed execution containing only
  coherent tails and no full legacy page.

Also, the opt-in aperture check uses an optional-zero stat lookup, so a custom
candidate that omits the aperture statistic can be mistaken for zero
rejections.  None of these changes the current runner's explicitly unpromoted
status, but they should be closed before it becomes promotion authority.

### Storage accounting: modeled shared payload accepted; guest metadata undisclosed

The `462a9cb8` shadow-free successor is consistent with the implementation:
shared mode configures zero `VirtualResponsePayloadStore` lines; source and
combiner words share the single configured allocator; and the lower-bound
ledger charges the allocator once plus fixed per-response word references and
15-bit fanout counters.  The reports correctly limit this to modeled bounded
storage/liveness evidence, not synthesized area or speedup.

The new SSSP admission tracker adds a dynamically allocated reason byte per
frontier chunk, with capacity retained by `std::vector`.  It is guest coherent
memory, not hidden SPD/result payload, so it does not contradict
`new_dedicated_payload_bytes=0`; however, no manifest or terminal field reports
its requested size, peak capacity, or traffic.  This is a residual disclosure
risk for exact storage/performance accounting.

## Validation performed

- `tests/maa/run_sssp_chunk_admission_unit.sh`: optimized and ASan/UBSan passes.
- SSSP contract suites: 23/23 pass.
- Focused shared-storage report tests: 4/4 pass.
- Both SSSP runners pass `bash -n`.
- No gem5 process was launched and no production source was edited.
