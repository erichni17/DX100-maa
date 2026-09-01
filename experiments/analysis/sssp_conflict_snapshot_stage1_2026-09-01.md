# SSSP conflict-snapshot stage-1 prototype (2026-09-01)

## Decision

**PASS for the bounded, default-off stage-1 functional prototype.** Both
directed hazard gates route all four eligible logical windows and reproduce
their exact graph-specific shortest-path fingerprints. `ActiveSource` and
`CrossOwner` remain observed and are counted as tolerated; neither becomes an
admission rejection. `Bounds` remains the only reason accepted by
`Tracker::snapshotSafe` as a rejection.

This is not a production or performance promotion. The OpenMP critical region
still serializes routed issue/candidate/window work, the original ordered
old-result reconstruction and physical-page alias order remain intact, and
stage 2's overlapping-owner linearizability gate has not been attempted.

## Implementation contract

- `SSSP_CONFLICT_SNAPSHOT_PROTOTYPE=1` is required explicitly and also
  requires `SSSP_OLD_RESULT_HYBRID`; ordinary targets and the rejecting hybrid
  remain unchanged.
- Before each MAA-sized wave, one `WeightT` is copied per frontier occurrence
  into ordinary coherent external storage, followed by an explicit OpenMP
  barrier.
- Admission activity/candidate bounds, the MAA active predicate and candidate
  stream, fallback pages, and coherent tails consume the occurrence snapshot.
  They do not reread live source distances during that wave.
- Destination MIN stays coherent and ordered, old results remain ordinally
  associated, reconstruction preserves the original four 4K physical-page
  boundaries, and completion precedes response consumption.
- The host predictor defaults to `reject-hazards`; the explicit
  `--admission-policy snapshot-tolerant` policy mirrors the prototype's source
  snapshot and routes through only the two observed data hazards.

## Source/model validation

Local accepted source commit:
`91953cd072cb38d31429dd3ab2aaf1e60adc33e4`.

- 30/30 focused source, predictor, legacy-hybrid, and executable-model tests
  passed.
- The exhaustive model checked all 64,512 legal two-owner RMW/reconstruction
  schedules, the internal-lane reorder counterexample, active-source snapshot
  repair, and the repeated-iteration search (9 states, 17 transitions).
- Both the default rejecting hybrid and the opt-in snapshot prototype compiled
  with `-Wall -Wextra -Werror`.
- Shell syntax, `git diff --check`, Python formatting, and gem5 style checks
  passed.

The first checkpoint attempt let isort/black reformat the new source contract
and exposed one 81-column predictor help line. After that style fix, all content
hooks passed. The repository commit-message hook then crashed because this
worktree lacks `MAINTAINERS.yaml`; only that broken hook was skipped for the
local commits.

## Accepted candidate-only gem5 evidence

Both runs use gem5 SHA-256
`45206b3433449e10b26bbd8ff32281c06e533c101213097a27d50c364ca3c267`,
guest SHA-256
`a60d15696379750c376fdc0f6afd30246cfba6d54de5c7a24688b3e61c752d91`,
four CPUs, 16K logical/4K physical geometry, four indirect units, 32 row-table
slices, no native arm, no wall timeout, and no full graph.

| Gate | Fingerprint hashes (`hash_a` / `hash_b`) | Routed | Observed/tolerated | Rejected/fallback | `simTicks` |
|---|---|---:|---:|---:|---:|
| `active_source r2` | `24951adf631ff822` / `1d2f7d2e3ed1aa0f` | 4/4 | active 1/1 | 0/0 | 8,467,630,316 |
| `cross_owner r2` | `4ab569558e397822` / `005c7757503cab01` | 4/4 | cross 2/2 | 0/0 | 8,478,759,970 |

Each run reports 65,536 selected old-result words, 16 index and 16 value page
publications, zero legacy/fallback words, zero host-SPD reads, zero aperture
rejections, `response_closure=1`, and `counts_close=1`. Write issues exactly
match responses: 20,378 for active source and 19,559 for cross owner. Timing is
diagnostic only because the directed graphs differ; no speedup is inferred.

Accepted immutable roots:

- active source:
  `/data1/nier/worktrees/codex-coordination/sessions/sssp-conflict-snapshot-prototype-20260901-20260831-232943-9aba9af8/evidence/sssp-conflict-snapshot-active-source-r2`
  - result SHA-256:
    `9eb9590218e47d60ab2d87e5f098f9f50a86e4044ad716a17783f32582a57936`
  - restore SHA-256:
    `f85fc30f7e69923092f195691abbf99f216445afaa03670771b87871611cb78f`
  - manifest SHA-256:
    `1561347b8ed0642c55a96ffc9aacb83d0ef298c53b57077bc904cdf0a40b36be`
- cross owner:
  `/data1/nier/worktrees/codex-coordination/sessions/sssp-conflict-snapshot-prototype-20260901-20260831-232943-9aba9af8/evidence/sssp-conflict-snapshot-cross-owner-r2`
  - result SHA-256:
    `afa1b8e4e8bd9c37cfa45ce478ffba43a76a7725903bb3b3c738ab00c3f7ce16`
  - restore SHA-256:
    `8e4891dc9a609a3db199c70ce5735ffee294f717cc891af96fd27dba5103c4f1`
  - manifest SHA-256:
    `acd0373524b101a9b81162fae7b6a40fb01323329a30c263f40adf9624c824b5`

The wrapper independently closed before/after artifact ledgers and required a
nonempty final stats window and the m5-exit marker for each accepted root.

## Storage disclosure

For both accepted fixtures:

- snapshot capacity: 69,632 words / 278,528 bytes;
- copied across two MAA-sized barriers: 69,631 words / 278,524 bytes;
- `new_dedicated_payload_bytes=278528`, equal to the external snapshot
  capacity;
- `source_snapshot_span=coherent_external`; and
- `hidden_source_snapshot_bytes=0`, `hidden_logical_spd_bytes=0`, and
  `hidden_result_payload_bytes=0`.

The initial `r1` runs passed correctness and mechanism closure at commit
`607c2ad2`, but terminalized the snapshot capacity alongside the inherited
`new_dedicated_payload_bytes=0` field. They are retained as successful attempts
that exposed a disclosure inconsistency, not accepted as final evidence. The
local disclosure-only successor `91953cd0` corrected that field and produced
the accepted `r2` roots above.

## Terminal handoff

- `all_safe` was not needed and was not launched.
- No native simulator arm, full S22 run, or other full graph was launched.
- Stage 1 is complete for the bounded functional prototype.
- Production promotion remains **NO-GO** pending stage 2's overlapping-owner
  same-line atomic-MIN/old-result/order/completion/response-closure proof and
  independent target validation.
- Remote state was left untouched after the user's stop-publication
  instruction. Commit `607c2ad2` had already been published before that
  instruction; `91953cd0` and this report handoff remain local only.
