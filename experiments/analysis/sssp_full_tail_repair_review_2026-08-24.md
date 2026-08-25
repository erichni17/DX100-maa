# Independent review: SSSP full-tail repair (2026-08-24)

## Recommendation

**HOLD WHILE THE FULL GATE IS ACTIVE.** If the frozen `f1624ddd` gate reaches a
clean terminal state and the explicit gate validator returns zero with the
exact fingerprint, immutable hashes, closed ledgers, and at least one 4,133-word
CPU fallback, the benchmark repair is **INTEGRATE**-worthy. If any condition
fails, **REJECT** the three-commit series. The dead watch means terminal
acceptance must not be inferred from the handoff; the run owner must perform or
safely re-arm explicit post-exit validation. Independently, **FIX** the launch
tool's concurrency hole before reusing it as an exactly-once primitive. Do not
rerun the native arm.

## Findings

### 1. Medium: the focused tests do not execute the cursor/replay equivalence contract

The 25 focused tests all pass, but they do not directly exercise the new C++
implementation path. The boundary/winner test computes the same Python
`reference` function twice (`experiments/tests/test_sssp_tail_repair.py:86-111`);
it never calls `RunSsspExactCpuWords`, `FillSsspExactCpuBatch`, or
`AdvanceSsspHybridCursor`. The compiled route test omits 0, 8,192, and 12,288,
and treats 16,384 through `recordLogicalWindow()` rather than
`SelectBatchRoute(16384)` (`experiments/tests/test_sssp_tail_repair.py:14-84`).
There is no executable test for an interrupted published-page sequence, a
duplicate destination across physical pages, cursor closure with repeated or
inactive frontier entries, or an unsafe-but-positive-weight iteration.

Inspection supports equivalence for the admitted SSSP domain, as detailed
below, but these should become executable unit tests. Until the active full
gate closes, the test suite alone is not sufficient promotion evidence.

### 2. Medium: terminal counters do not independently prove total edge coverage

Terminal `counts_close` proves only internal route-accounting identities
(`benchmarks/gapbs/src/sssp.cc:950-955`). It does not assert a total such as
`routed_words + bounded_words + exact_cpu_words == actual_range_words`, and the
end-of-chunk observed-word equality is conditional on the hybrid safety proof
(`benchmarks/gapbs/src/sssp.cc:855-860`). The printed
`out_of_range_spd_ids=0` is a literal, not a measured counter
(`benchmarks/gapbs/src/sssp.cc:956-978`). The full validator repeats the same
route identities and SoA/JIT response-ledger balances
(`experiments/scripts/run_sssp_old_result_hybrid_full.sh:163-266`); those
ledgers cover routed SoA/JIT instructions rather than CPU cursor consumption.

This is an observability gap, not an identified admitted-input correctness
failure: each nonterminal batch advances or fills exactly `curr_size`, the
cursor helpers abort if they run out early, and positive weights preserve the
condition-membership invariant. Still, a total produced/consumed word counter
and a measured illegal-host-access counter would make skipped or duplicated
work fail closed instead of relying on inspection plus the final fingerprint.

### 3. High operational finding: the advertised completion callback is not alive

The handoff says watch `sssp-tail-repair-f1624ddd-complete` is bound to PID
`2257335` and will rerun validation (`f0c92e53:experiments/analysis/
sssp_full_tail_repair_2026-08-24.md:23-25`). Read-only
`dx-runtime watch status` at 2026-08-24 15:39 EDT instead reported
`state=watching`, `worker_pid=2258329`, and `worker_alive=false`; `ps` confirmed
that PID absent, and no callback log exists. The target PID remains alive with
the matching Linux start time. The callback command itself is idempotent in
effect (`experiments/scripts/run_sssp_old_result_hybrid_full.sh:324-337`), but
it is not currently armed.

The systemd wrapper independently validates once after the full runner returns
and writes `systemd.result` (`experiments/scripts/run_sssp_tail_repair_gate.sh:
157-190`), so the dead watch does not by itself corrupt the running simulation.
It does invalidate the autonomous callback claim. Acceptance now requires an
explicit post-exit `--validate` with the matching inactive successful unit, or
a safely re-armed PID/start-bound watch by the run owner; this review did
neither and did not modify active evidence.

### Post-exit resolution

The later reviewed successor `7b6f9c21` also failed its full S22 gate. At tick
`239,082,572,292`, a cache-origin line request reached physical SPD element
4,096. A stride prefetch is plausible because the small gate omitted the full
prefetch configuration, but the log does not preserve request provenance and
an L1D-only ablation later reproduced the failure with L2 prefetch enabled.
RangeFuser itself was
already capped at 4,096; the adjacent 4,132 log value was frontier cardinality,
not an oversized range result. The full source is rejected pending a
speculative-prefetch boundary fix that preserves fail-closed architectural
demands. No full correctness or performance claim survived this review.

### 4. Medium: the launcher is fail-closed serially, but not concurrency-safe exactly once

`launch_gate` checks for absent ledgers/output, then creates a conventional
`.tmp`, renames it, and calls `systemd-run`
(`experiments/scripts/run_sssp_tail_repair_gate.sh:108-147`). There is no
gate-root lock or atomic create-if-absent operation spanning the checks and
launch. Two concurrent callers that passed the checks can overwrite/recreate
the temp/intent and, if given different unit names, both reach `systemd-run`.
The contract test only counts one textual `systemd-run` call and checks ordering
(`experiments/tests/test_sssp_tail_repair_gate.py:38-50`); it does not race two
launchers.

For this actual gate, the evidence supports one launch: intent and acceptance
both say `launch_count=1`; systemd enumerated exactly one matching transient
unit, `dx100-sssp-tail-repair-f1624ddd-r1.service`; it has `NRestarts=0`, PID
`2257335`, and monotonic start `2943169802288`. Thus no duplicate was observed,
but the launcher should not be called an exactly-once primitive until it takes
an exclusive gate-root lease independent of the caller-supplied unit name.

## Semantic equivalence audit

When the admission proof holds, the ordered CPU MIN helper is equivalent to
the legacy batch contract: its first pass captures each lane's pre-update value
and applies MIN in lane order; its second pass reloads the batch-final
destination and applies `candidate == final && old > final`
(`benchmarks/gapbs/src/sssp.cc:231-265`). Duplicate candidates therefore select
the same first lane that reaches the batch-final minimum. The surrounding
critical section excludes cross-thread `dist` mutation, and the safe admission
forbids active sources and cross-chunk destinations from being updated by other
chunks.

The oversized fallback remains cursor-equivalent when the hybrid admission
proof is false for a valid positive-weight graph. At the iteration snapshot an
active source has `dist[u] >= lower_bound`. Every relaxation generated by an
active source has `candidate = dist[source] + w > lower_bound` because `w > 0`;
MIN can lower another active source but cannot move it below the condition
threshold. An inactive source is already below the threshold and MIN cannot
increase it. Consequently `hybrid_active_sources` and the accelerator's later
`GTE` condition select the same frontier occurrences even though condition and
range formation precede the critical section (`benchmarks/gapbs/src/sssp.cc:
529-592`, `698-723`). Inside the critical section the accelerator candidate is
completed before route selection, and an unsafe iteration cannot have pending
published pages. `FillSsspExactCpuBatch` therefore observes the same source
distances, walks frontier occurrences and adjacency edges in the same order,
and consumes exactly the reported batch size (`benchmarks/gapbs/src/sssp.cc:
267-347`, `723-851`). This proof depends on the benchmark's positive,
nonoverflowing weight domain; invalid/nonpositive inputs are outside the frozen
gate contract.

Pending published pages are replayed in page order before the interrupting
batch (`benchmarks/gapbs/src/sssp.cc:767-803`). Their cursor had already advanced
when published (`benchmarks/gapbs/src/sssp.cc:733-763`), and replay does not
advance it again, so the inspected safe path neither duplicates nor drops those
pages. Four uninterrupted 4,096-word pages still invoke one 16,384-word hybrid
window. Duplicate reconstruction remains page-local, matching the legacy
post-RMW reload boundary (`benchmarks/gapbs/src/sssp.cc:195-228`).

The exact boundary behavior is:

| `curr_size` | Actual branch |
|---:|---|
| 0 | terminal; no work |
| 4,095 | bounded SPD; max host element 4,094 |
| 4,096 | bounded SPD unless it is an admitted hybrid page; max host element 4,095 on bounded path |
| 4,097 | exact CPU |
| 4,133 | exact CPU; separately counted |
| 8,192 | exact CPU |
| 12,288 | exact CPU |
| 16,384 | exact CPU if presented as one batch; hybrid only when presented as four consecutive admitted 4,096-word pages |

That last distinction is not represented by the current boundary test or by
the handoff table, which labels 16,384 unconditionally as a logical hybrid
window. The implementation preserves page-assembled 16K windows; it does not
route a single 16K `curr_size` through the hybrid.

## Frozen gate audit

Read-only checks at 2026-08-24 15:45 EDT found the unit still `active/running`,
PID `2257335`, `NRestarts=0`. The restore had reached the full S22 graph load
but had not emitted a terminal marker. `checkpoint.after`, callback identity,
`restore.exit`, wrapper status, `result.txt`, `gate.complete`, and
`systemd.result` were correctly absent for a running gate; no full PASS is
claimed.

- Frozen and copied guest SHA-256 both equal
  `580c31eb6d348817b03036f789aa9d865cae3cbe98eb51fa0e028be61ff23d1b`.
- Copied graph SHA-256 equals
  `23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc`.
- All entries in `frozen/files.sha256` and
  `full/provenance/artifacts.before.sha256` verify.
- All 13 files in the 729 MiB checkpoint verify against
  `checkpoint.before.files.sha256`; that manifest hashes to the recorded
  immutable identity
  `0ce83958e5f5e4fbd0f738502efc001f5751f5a1448b9d68d636089da04d2a6b`.
- The full runner reads and hashes the frozen native log/stats but never invokes
  a native executable (`experiments/scripts/run_sssp_old_result_hybrid_full.sh:
  19-24`, `356-365`, `417-434`). No native rerun occurred.
- Neither runner uses a shell timeout nor systemd `RuntimeMaxSec`; manifests
  record `wall_timeout=none` and `native_arms=0`.
- Terminal full validation explicitly requires
  `exact_cpu_4133_batches > 0`, `exact_cpu_fallback_words >= 4133`, exact
  fingerprint/exit/stats markers, and the listed response-ledger balances
  (`experiments/scripts/run_sssp_old_result_hybrid_full.sh:78-266`).

## Validation performed

No simulation was launched or rerun. The focused command was:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v \
  experiments.tests.test_sssp_old_result_hybrid_contract \
  experiments.tests.test_sssp_old_result_hybrid_full \
  experiments.tests.test_sssp_tail_repair \
  experiments.tests.test_sssp_tail_repair_gate
```

Result: **25 tests passed**. `git diff --check 94cafc7c^..f1624ddd` also passed.
These successes are contract evidence only and do not override Findings 1-4.
