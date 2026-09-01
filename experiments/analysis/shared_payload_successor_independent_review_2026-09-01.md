# Shared-payload successor independent review — 2026-09-01

## Promotion decision

**BLOCK pending one focused production-path test.** Commits `c06e43b3` and
`6602846c`, as present at base `dffa5573`, close the prior response-line-shadow
and pre-scan-admission defects. The frozen runs establish exact output, bounded
17-word liveness, scan serialization, successful final-use transfers, and ACK
closure. They do not execute the newly changed production rollback branch:
`r5`, `r6`, and `r11` all report zero shared-payload rollbacks. The standalone
fanout unit test rolls back a use counter, but it does not exercise restoration
of `shared_word_refs[word_id]`, `slot.reserved_words`,
`virtual_reserved_response_words`, and `virtual_response_payload_words` after
a failed final-use insertion. Promotion should remain blocked until a legal
shared-pool-pressure case or focused unit around the production ownership
transition observes at least one final-use rollback and exact empty closure.

No source defect was found in the inspected rollback algebra: a final-use
attempt removes one response reference and decrements all three response
occupancy counters; failure restores the same reference and counters before
retry, while success increments combiner ownership without allocating a copy.
The blocker is that this changed ownership path is not independently executed
by the supplied successor evidence.

## Source audit

| Requirement | Source result |
|---|---|
| No shared response-line shadow | Pass. Shared mode passes `packedResponse=true` to `VirtualResponsePayloadStore::configure` (`IndirectAccess.cc:195-201`), so its line vector remains empty. Terminal closure requires `payloadBytes()==0` and reports `line_shadow_bytes=0`. |
| Actual fixed-pool source word references | Pass. `recvData` allocates only words with nonzero fanout into `VirtualCombinePayloadStore` and saves their `WordRef`s in the fixed 16-entry `shared_word_refs` array (`IndirectAccess.cc:10077-10140`). Retirement reads those references directly; a final use transfers the same reference into the combiner. |
| Transfer conservation | Pass for the successful path. Final use decrements response-resident and reserved ownership by one, then insertion increments combiner ownership by one without a second allocation. Occupancy assertions require `used == combine + response_payload` before and after the transition (`IndirectAccess.cc:11026-11102`, `11776-12072`). |
| Rollback conservation | Source algebra passes; successor execution coverage fails as described above. The sanitized `VirtualSourceFanout` unit covers consume/rollback limits, but not the production `WordRef`/credit restoration lambda. |
| Explicit scan-ready admission | Pass. Both row-table and bounded-global paths call `deferVirtualSourceFanout` before credit inspection and `issueVirtualSource`; a pending descriptor is retried only at or after its ready tick (`IndirectAccess.cc:2860-2882`, `2985-3002`, `7463-7502`, `7593-7679`). |
| Multi-event serialization | Pass. Each scan starts at `max(curTick, virtual_fanout_scan_finish_tick)` and advances the single finish token by `ceil(logical_uses/4)` cycles (`IndirectAccess.cc:2835-2857`). |
| 17-word liveness bound | Pass. Shared mode rejects a configured sum at or below the maximum 16 unique words in one cache line (`MAA.cc:450-465`). Thus one source line can be admitted after legal combiner draining/spilling; a final-use transfer preserves total pool occupancy. |
| Terminal closure | Pass in source. Shared completion requires zero reserved words, zero response-resident words, zero combiner words, zero line-shadow bytes, no spill bits, and high water within the configured pool (`IndirectAccess.cc:8164-8199`). |

The `dffa5573` changes after `6602846c` are analysis-only; there is no diff in
the simulator or relevant runner sources. `c06e43b3` is an ancestor of
`6602846c`, which is an ancestor of `dffa5573`.

## Frozen-run audit

### Maximum duplicate fanout: `r5` and `r6`

Roots:

- `/data1/nier/dx100-runs/2026-08-31-shared-fanout-6602846c-r5`
- `/data1/nier/dx100-runs/2026-08-31-shared-fanout-6602846c-r6`

Both have checkpoint and restore exit status zero, a final `m5_exit`, one
result with `errors=0`, and identical `result.tsv` SHA-256
`bb4b202142389b1d2ab10453b7002844134f2b66122c79fa4180e2aca6c75de2`.
The observed hash `7221120122736935811` independently recomputes from 16,384
copies of the exact expected 32-bit value `source[13] = 224`; the guest also
checks every output and both guards.

Each run records one 16,384-use scan, exactly 4,096 scan and wait cycles, one
source read, one successful shared transfer, zero rollbacks, shared high water
17/17, and 1,024 retirement issues matched by 1,024 completions. `r5` names
source commit `6602846c`; `r6` names `dffa5573`, whose relevant source is
identical. The repeated result and architectural counters are exact matches.

The input/source checksum manifest now covers `VirtualSourceFanout.hh`,
`VirtualResponsePayloadStore.hh`, and `VirtualCombinePayloadStore.hh`; every
entry resolves and matches. Its runner entry is relative to the source
worktree, so `sha256sum -c artifact_sha256.txt` from the run root reports one
missing file even though the recorded digest matches the runner at both the
recorded worktree and `dffa5573`. This is an archival ergonomics caveat, not a
source-identity mismatch. These two roots also lack a sealed output checksum
ledger; the hashes above seal the reviewed `result.tsv` state in this report.

### Bounded-global minimum pool: `r11`

Root:
`/data1/nier/dx100-runs/2026-08-31-bounded-global-unified-pool-6602846c-r11`

The complete input, checkpoint, and run-output ledgers verify. Checkpoint and
restore exit status are zero; the restore ends at `m5_exit`; final stats are
nonempty. The guest reports exact output hash `7228541527853630339` with zero
errors, matching an independent recomputation of all 16,384 FP64 outputs and
the guest's element/guard checks.

The trace contains 9,523 fanout scans and 9,523 bounded-global source issues.
An independent ordered parse found:

```
scans=9523 issues=9523 serial_bad=0 ready_bad=0 early_issue=0
head_mismatch=0 issue_without_scan=0 pending=0
```

The scans cover 16,384 logical and 16,384 unique source words in 9,523 cycles.
The terminal shared-payload record is exactly `capacity=17 high_water=17
line_shadow_bytes=0 transfers=16384 rollbacks=0`. The 9+8-word configured pool
therefore reaches the legal minimum bound and completes without fallback.
Bounded-global admissions and retirements close at 16,384/16,384; sorted-run
writes and ACKs close at 1,536/1,536; virtual retirement issues and completions
close at 15,430/15,430; the aggregate terminal-ACK count is 30,329. The single
`bounded_global_merge_complete` event occurs only after the source responses,
descriptor-spool reads/writes, merge reads/writes, and retirement scoreboard
are empty.

This is liveness and correctness evidence, not performance evidence. It also
does not cover rollback: the trace and stats both report zero.

## Corrected GZZ storage arithmetic

Root: `/data1/nier/dx100-runs/2026-09-01-gzz-storage-ledger-6602846c-r2`

`artifacts.sha256` verifies completely, the manifest's reporter SHA-256
`9829f45a612ff90d38563d93b03d78713368d7bb83079fbed47bed6d58a87a6c`
matches `6602846c`/`dffa5573`, and all three report pass sentinels are present.
The strict-hybrid report charges one unified 4,096-word allocator per indirect
unit, zero response-line shadow bytes, 16 x 12-bit pool references and
16 x 15-bit fanout counters per each of 128 response slots, the fixed spill
bitmap, direct-index feeder state, combiner tags/allocator control, retirement
metadata, and the configured direct-handoff lower bound.

The exact byte sums are internally consistent:

| Arm | Recomputed configured comparable total |
|---|---:|
| native16 | 2,106,624 bounded + 65,536 readiness + 1,013,760 descriptors = **3,185,920 B** |
| native4 | 533,760 bounded + 16,384 readiness + 850,944 descriptors = **1,401,088 B** |
| strict logical16/physical4 hybrid | 984,616 bounded + 16,384 readiness + 1,013,760 descriptors = **2,014,760 B** |

For the hybrid, the bounded subtotal itself recomputes as 606,208 bytes of
physical SPD plus active virtual payload, 368,924 bytes of packed virtual
metadata across four indirect units, 12 completion bytes, and 9,472 direct
handoff bytes. The resulting comparisons are exactly 36.7604961832% below
native16 and 43.7996756806% above native4, matching `summary.json`.

These are configuration-specific packed lower bounds, not synthesized area,
power, or timing. The GZZ strict arm has bounded-global merge disabled, so its
totals must not be generalized into a storage claim for the separate `r11`
bounded-global mechanism.

## Validation

No gem5 process was launched and no simulator source was changed. Review
validation comprised commit/source comparison; all frozen input, checkpoint,
run-output, and storage ledgers where supplied; independent trace and output
hash recomputation; and these focused tests:

- `tests/maa/run_virtual_source_fanout_unit.sh` (optimized and ASan/UBSan)
- `tests/maa/run_virtual_combine_payload_store_unit.sh` (optimized and ASan/UBSan)
- `tests/maa/run_virtual_response_payload_store_unit.sh` (optimized and ASan/UBSan)
- `python3 -m unittest experiments.tests.test_ume_two_pass_matrix experiments.tests.test_report_maa_storage` (27 tests)

All passed. The blocker is the missing execution of the new production
final-use rollback ownership path, not a failure in the completed checks.
