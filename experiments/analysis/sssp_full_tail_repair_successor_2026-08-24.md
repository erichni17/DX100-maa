# SSSP full-tail repair successor handoff (2026-08-24)

## Decision

The independent-review code gaps are closed at commit `7b6f9c21ab22`.  The
repaired small exact gate is accepted.  One fresh full S22 candidate-only gate
is active, but it is **not promoted** and has no accepted correctness or
performance result until its persistent wrapper and an explicit post-exit
validation both pass.

The preserved admission proof remains limited to positive, nonoverflowing
weights.  At an iteration snapshot, an active source has distance at least the
current lower bound.  Adding a positive weight cannot move a relaxation below
that bound; MIN cannot make an inactive source active.  Therefore range-loop
membership and the coherent CPU cursor select the same frontier occurrences
when the hybrid proof rejects an iteration.  Admitted hybrid windows further
require bounded FP32 operands, no active-source destinations, and no
cross-chunk destination ownership.

## Review-gap closure

- `sssp_tail_replay.hh` now contains the production cursor, published-page
  replay, ordered MIN, and page-local old-result reconstruction helpers used by
  `sssp.cc`.
- `sssp_tail_replay_test.cpp` executes those helpers for batch sizes 0, 4,095,
  4,096, 4,097, 4,133, 8,192, 12,288, and 16,384.  It also covers interrupted
  published-page replay, destinations duplicated across physical pages,
  inactive and repeated frontier entries, cursor exhaustion, and ordered-MIN
  old-result/winner selection.
- A single `curr_size=16384` selects ordered CPU fallback.  A hybrid window is
  formed only by four consecutive, separately admitted 4,096-word pages.
- Every iteration snapshots its exact active-edge total and fails closed unless
  measured produced and consumed deltas both equal that total.  Terminal
  coverage also requires consumed = accelerated + bounded-SPD + CPU and CPU =
  scalar-CPU + exact-CPU.
- Host SPD element access is guarded.  An oversized attempt increments the
  measured `illegal_host_spd_attempts` counter and aborts before dereference;
  the terminal record prints the measured counter.
- Full launch takes a persistent, atomic `gate/launch.lease` before inspecting
  or writing intent and holds exclusivity across `systemd-run`.  The executable
  race test starts two launchers and observes one launch and one lease refusal.
- The restart-disabled persistent systemd wrapper runs final validation and
  atomically renames `systemd.result`.  Manual validation remains available as
  `run_sssp_tail_repair_gate.sh --validate GATE UNIT`.  No `dx-runtime` watch is
  part of acceptance.

Focused validation at `7b6f9c21ab22` passed 25/25 Python/compiled contract
tests, `bash -n`, `git diff --check`, gem5 style checking, and a production
guest compile with `-Wall -Wextra -Werror`.

## Accepted small exact gate

The evidence base is `/data1/nier/worktrees/codex-coordination/sessions/sssp-tail-repair-successor-20260824-155812-7c1e3190/evidence`.

- Root: `$EVIDENCE_BASE/sssp-tail-repair-7b6f9c21-small-r2`, where
  `EVIDENCE_BASE` denotes the exact evidence base above.
- Unit: `dx100-sssp-tail-repair-7b6f9c21-small-r2.service`, inactive/dead,
  `Result=success`, `ExecMainStatus=0`, `NRestarts=0`.
- Frozen and small guest SHA-256:
  `b9f1ad65c4b28066c2be97eda9a669ed6629f75f1d36b74860df3581da89a8fb`.
- Exact fingerprint: 69,633 reached vertices, distance sum 135,168, maximum
  distance 2, hashes `a0531a7ddb9387df` / `39f1ea63bc8817e8`, no graph
  violations.
- Coverage: total = produced = consumed = 69,632 words; 65,536 accelerated;
  4,096 scalar CPU; zero bounded or exact-tail words; three iterations; zero
  measured illegal host-SPD attempts.
- Mechanism: four eligible/routed windows, 65,536 captures, and 49,699 old-
  result write issues matched by 49,699 responses.  `simTicks=10595863174` is
  recorded only as small-gate provenance, not a full-application claim.

## Full S22 gate (terminal rejection)

- Gate root: `$EVIDENCE_BASE/sssp-tail-repair-7b6f9c21-r1`.
- Unit: `dx100-sssp-tail-repair-7b6f9c21-full-r1.service`, terminal failed
  with `ExecMainStatus=1`; the restore process aborted with status 134.
- Launch accepted at `2026-08-24T16:39:48-04:00`; the exclusive lease, intent,
  and acceptance ledgers each identify this unit and `launch_count=1`.
- Candidate-only, full S22, no native arm, no shell/systemd wall timeout, and
  `Restart=no`.
- Frozen identity:
  `943e9ce26dd432cb23369e9af4e213b1d3963d9fb63cb3fd6d879f654bee3619`;
  frozen guest hash is the accepted small-gate hash above.
- Exact graph SHA-256 required by preparation and final validation:
  `23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc`.

The post-exit validator returns nonzero, as required for this failed root:

```text
experiments/scripts/run_sssp_tail_repair_gate.sh --validate \
  /data1/nier/worktrees/codex-coordination/sessions/sssp-tail-repair-successor-20260824-155812-7c1e3190/evidence/sssp-tail-repair-7b6f9c21-r1 \
  dx100-sssp-tail-repair-7b6f9c21-full-r1
```

At tick `239,082,572,292`, the O3 CPU issued a cacheable SPD line request whose
first element was 4,096, immediately beyond the physical range 0--4,095. The
printed `Starting DeltaStepMAA: 4132 elements` is the frontier size, not the
range-tile size: RangeFuser is already allocated with the 4,096-element
physical capacity. The accepted small runner has no L1 stride prefetcher; the
full runner enables one. A full-page host scan therefore allows a speculative
next-line request to cross the physical aperture, and `CpuSidePort` currently
passes that request to `SPD::getDataPtr()` as though it were an architectural
demand. This exact small/full configuration difference explains why the small
gate did not expose the failure.

No fingerprint, terminal coverage record, final stats window, or performance
result was produced. The frozen graph, guest, checkpoint, and binary remain
useful failure provenance only.

Worker commit `2040dfd9` is not a repair and must not be integrated. It uses
aggregate frontier-edge count as a preflight and sends every chunk above 4K to
CPU, including valid four-page logical 16K windows; larger chunks can exceed
the fallback arrays. The required successor must instead distinguish a
non-binding speculative line outside physical SPD from a real demand. Only the
former may receive a harmless dropped response; architectural out-of-range
reads and writes must still fail closed. A targeted stride-prefetch reproduction
must pass before any fresh small/full gate.

## Inadmissible roots

All evidence predating `7b6f9c21ab22` remains inadmissible, including the
4,133-tail failure root `2026-08-24-sssp-old-result-full-e690867f-r1`, every
root associated with `94cafc7c` through frozen `f1624ddd`, the completed old
`dx100-sssp-tail-repair-f1624ddd-r1.service`, the never-launched prepared
`sssp-tail-repair-e11a155d-r1`, and the two cooperatively stopped foreground
small roots `sssp-tail-repair-e11a155d-small-r1` and
`sssp-tail-repair-7b6f9c21-small-r1`. The now-terminal full root is also
inadmissible because both its wrapper and explicit validator fail. The accepted
small `r2` gate remains narrow correctness evidence only.

Lead, CG, and IS sources/services were not modified or stopped.
