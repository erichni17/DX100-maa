# Full page-fed p16/q16 cache-on lane-4 certificate (2026-08-26)

## Decision

**ACCEPT the lane-4 full candidate through its read-only successor
certificate.** The original service wrapper exited 1 only because its
post-restore gate incorrectly required every sparse operation to reach
four-lane occupancy. The checkpoint and restore wrappers exited 0, the guest
reached one `m5_exit`, final stats/config are complete, and the successor
replays all evidence with the corrected condition:

- `ActiveApplyLanes == 4 * instructions`; and
- `3 * instructions < HWM <= 4 * instructions`.

Raw root:

`/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-value-cache-lane4-full-r1`

Fresh certificate root:

`/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-value-cache-lane4-full-certificate-r3`

The certificate and explicit `--validate` report
`PASS_NUMERICAL_MECHANISM_CORRECT`. The classifier launches zero gem5 runs
and leaves the historical raw root unchanged.

## Terminal and provenance identity

The launch registration and journal agree on service
`dx100-cg-page-fed-p16q16-value-cache-lane4-full-r1.service`, PID 3,758,672,
`/proc` start ticks 312,112,040, boot
`b2060699a6dc49eb926042ccb3779afe`, and invocation
`997f720c6de149aa83fee7ce5f75e81b`. The registered PID is absent. All 18
journal records belong to that invocation; the traceback ends only at the old
`four-lane active/high-water closure failed` check, followed by systemd
`status=1/FAILURE` and `result=exit-code`.

The successor pins the raw manifest, checkpoint/restore logs and exit files,
stats, resolved config, command JSON, and before/after source records. It
re-hashes all 33 artifact-ledger entries and all 13 checkpoint-ledger entries.
Before/after artifact, checkpoint, commit, and clean-status identities match.
The raw root contains no result or gate written by the failed wrapper.

## Correctness and mechanism closure

The frozen tolerant full-CG numerical authority is verified before accepting
the candidate. All six reported numerical deltas are zero and remain within
the authority bounds. The exact candidate closure is:

- `p16_reorder_preserved=true` and `q16_reorder_preserved=true`;
- 10,960 full windows: 8,768 q and 2,192 residual;
- 10,960 virtual-p gathers, 43,840 product pages, 43,840 page-fed admits,
  10,960 closes, and 10,960 terminals;
- 179,568,640 product, selected, applied, delivered, admitted,
  SPD-index-read, and row-written words;
- 57,491 A read issues/responses and write issues/responses;
- 11,223,040 publisher issues/accepts/WriteResps and 43,840 publisher
  terminals;
- zero predicate rejects, epoch drains, bounded-global-merge fallbacks,
  coherent q-index traffic, host payload access, hidden spill, and value,
  lookahead, or context stalls.

Retained-line identity closes exactly within each arm. Lane 4 records
11,266,328 issues/responses/fills/cached responses, 168,302,249 hits, and 63
merged waiters; lane 1 records 11,266,329, 168,302,256, and 55 respectively.
Both satisfy `issues + hits + merged = deliveries = 179,568,640`.
Cross-arm equality of these scheduling-dependent cache events is deliberately
not claimed; invariant work and terminal geometry are exact.

Storage closes at 524,288 B physical SPD plus 524,288 B external coherent
backing, split into 262,144 B virtual-p and 262,144 B product backing. There
is no coherent q-index backing or host payload. The resolved candidate uses
four apply lanes, 32 active value-owner lines per indirect unit, and eight
tiles/core. All four lane owners per unit and their 576-B-per-MAA pool are
already fixed in both arms, so lane selection adds zero payload bytes,
control bytes, or ports.

## Lane and performance result

The lane totals are:

- instructions: 10,960;
- active apply lanes: `43,840 = 4 * 10,960`;
- apply-lane high-water: `43,478`; and
- corrected bound: `32,880 < 43,478 <= 43,840`.

The accepted lane-1 certificate, raw stats, resolved lane-1 config, terminal
geometry, and 13-entry certified ledger are verified only after the candidate
passes independently. Arithmetic then uses:

| Arm | First-ROI `simTicks` |
|---|---:|
| Accepted cache-on lane 1 | 162,849,334,269 |
| Cache-on lane 4 | 158,381,418,273 |

The exact lane1/lane4 ratio is
`162849334269/158381418273 = 1.0282098496447273...`. Lane 4 saves
4,467,915,996 ticks, or 2.7435887% of lane-1 latency. Each arm is one
deterministic full observation; no variability estimate is claimed.

## Seal and claim boundary

The four read-only external files are sealed by:

- manifest SHA-256:
  `5006e14d07782e93e968899c6b28b7e5fd9d34da23642825476b5739b99bd002`;
- certificate SHA-256:
  `a3892ba6d96ef899cf741be8674cd646eb350fe9a09df9e4a9e25431b96f0f93`;
- input-ledger SHA-256:
  `670ec9e33e2d57be807fbf75d9cd3df1eea685fcbfbe405d0b5f3a75dc9f5258`;
  and
- gate SHA-256:
  `1b88b0e153360644fe7eb88c026893562d8807faf7becfe18d8b311011a74ff3`.

The selected r3 seal includes every imported validation-source dependency and
was created from the hook-normalized source. The earlier r1/r2 seals are
unmodified and superseded; neither is the authority cited by this report.

This is a lane1-versus-lane4 comparison of the same p16=true/q16=true
cache-on design. It is **not** a native-speedup claim, an iso-area claim, an
official NAS verification, a native rerun, or a full-promotion claim.
