# SSSP full-S22 tail repair handoff (2026-08-24)

## Status

The benchmark-side repair, deterministic boundary tests, frozen candidate, and
exactly one candidate-only full-S22 launch are complete.  The persistent O3
restore is running; this handoff does **not** claim full correctness or a
performance result before the terminal validator closes.

- Unit: `dx100-sssp-tail-repair-f1624ddd-r1.service`
- Main PID at launch: `2257335`
- PID start: `2026-08-24 15:26:34 EDT`
- systemd monotonic start: `2943169802288`
- Raw root: `/data1/nier/worktrees/codex-coordination/sessions/sssp-full-tail-repair-20260824-143251-d2926ff3/evidence/full-s22-candidate-f1624ddd-r1`
- Source commit: `f1624ddd97571e6da19fa53b79dc55731ca9e92c`
- Frozen guest SHA-256: `580c31eb6d348817b03036f789aa9d865cae3cbe98eb51fa0e028be61ff23d1b`
- S22 graph SHA-256: `23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc`
- Immutable checkpoint identity: `0ce83958e5f5e4fbd0f738502efc001f5751f5a1448b9d68d636089da04d2a6b`
- Launch count: exactly `1`
- Native arms: `0`
- Wall timeout: none

The `dx-runtime` process-exit watch
`sssp-tail-repair-f1624ddd-complete` is bound to PID `2257335` and Linux start
time `294316978`.  Its idempotent callback reruns the full evidence validator.

## Exact failure diagnosis

The retained failed evidence is:

`/data1/nier/dx100-runs/2026-08-24-sssp-old-result-full-e690867f-r1/run/restore.log`

It reaches `Starting DeltaStepMAA: 4133 elements (maa-1024)` and then gem5
panics in `SPD::check_tile_element_id`: CPU access to SPD element `4096` exceeds
physical capacity `4096` (logical capacity `16384`).  The backtrace enters MAA
through a CPU/cache timing request, grounding the fault in the benchmark's
host dereference of a logical Row/Offset result, not in the SPD bound.

The old fallback read `tilei_ptr[j]`, `tile1_ptr[j]`, and `tilev_ptr[j]` for
every logical result word.  A 4,133-word range result therefore made lane
4,096 the first illegal host-visible SPD ID.  The fix does not change
`SPD.hh`, logical or physical capacity, native targets, CG, or IS.

## Repair contract

- Batches of 1 through 4,096 words keep the bounded legacy SPD instruction
  path; the maximum host-visible element is explicitly recorded.
- Non-page batches of 4,097 through 16,384 words never enter the host SPD
  aperture.  They are reconstructed from the original frontier/range cursor
  and replayed in ordinary coherent memory with the exact ordered-MIN plus
  post-instruction reload winner rule.
- Four consecutive 4,096-word publications still form the existing 16,384-word
  SoA/JIT old-result hybrid window.
- If a non-page batch interrupts a partially published logical window, those
  pages are exactly replayed and explicitly counted as discarded publications;
  semantic work is neither padded nor truncated.
- Terminal accounting requires published pages, routed logical windows,
  bounded words, exact-CPU words, legacy words, and response ledgers to close.
  It reports `out_of_range_spd_ids=0` and requires
  `max_host_spd_element < 4096`.

## Deterministic and small-gate evidence

The compiled boundary test covers 4,095, 4,096, 4,097, 4,133, and 16,384
words.  Its aggregate expected routing is three selected batches/windows and
two exact-CPU fallbacks:

| Boundary | Route | Host SPD maximum |
|---:|---|---:|
| 4,095 | bounded SPD | 4,094 |
| 4,096 | bounded SPD | 4,095 |
| 4,097 | exact CPU | none |
| 4,133 | exact CPU | none |
| 16,384 | logical hybrid window | none |

The tests assert selected words `24,575`, fallback words `8,230`, one exact
4,133 batch, and identical ordered-MIN output/winners at every boundary.

- Focused and existing SSSP contracts: `25/25` pass.
- Candidate guest builds with C++11, `-Wall -Wextra -Werror`.
- Required pre-edit small exact gate: PASS, exact fingerprint, `4/4` windows,
  `49,226/49,226` old-result write issue/response ledger.
- Repaired small exact gate: PASS, unchanged fingerprint, `4/4` windows,
  zero fallback/discarded pages, `out_of_range_spd_ids=0`, and
  `49,578/49,578` old-result write issue/response ledger.

## Frozen full-S22 evidence

The candidate manifest records `candidate_guest_origin=prebuilt_frozen`,
logical capacity `16384`, physical capacity `4096`, the exact graph, archived
gem5/Ramulator hashes, and the selected non-treatment knobs.  The checkpoint
completed with one checkpoint exit marker and was changed to read-only before
restore.  The restore consumed the same immutable checkpoint and exact graph.

The immutable launch intent and acceptance ledgers each record
`launch_count=1`, `native_arms=0`, and `wall_timeout=none`.  A second launch is
fail-closed because the launcher refuses any existing intent, acceptance
ledger, full output, or systemd unit.

Terminal acceptance additionally requires:

- exact SSSP fingerprint for 4,194,304 reached vertices;
- one `m5_exit` and one `ROI End!!!`, with two complete stats windows;
- at least one explicitly counted 4,133-word exact-CPU batch;
- no panic, interrupt, assertion, silent truncation, or out-of-range SPD ID;
- balanced predicate, value, A-read/A-write, old-result-write, and terminal
  response ledgers;
- unchanged frozen guest, graph, runner/source hashes, and before/after/callback
  checkpoint identities.

## Monitoring and terminal validation

Read-only status:

```bash
experiments/scripts/run_sssp_tail_repair_gate.sh --status \
  /data1/nier/worktrees/codex-coordination/sessions/sssp-full-tail-repair-20260824-143251-d2926ff3/evidence/full-s22-candidate-f1624ddd-r1 \
  dx100-sssp-tail-repair-f1624ddd-r1
```

After the unit is inactive, fail-closed acceptance:

```bash
experiments/scripts/run_sssp_tail_repair_gate.sh --validate \
  /data1/nier/worktrees/codex-coordination/sessions/sssp-full-tail-repair-20260824-143251-d2926ff3/evidence/full-s22-candidate-f1624ddd-r1 \
  dx100-sssp-tail-repair-f1624ddd-r1
```

Only a zero exit from that command supports a full-S22 PASS claim.  Performance
must use the recorded first-ROI `simTicks`; host time is not architecture
evidence, and the frozen native result is referenced but was not rerun.
