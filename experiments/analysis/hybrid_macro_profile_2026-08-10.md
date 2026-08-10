# Hybrid 4K/native-table macro profile (2026-08-10)

## Result

The matched hybrid case completed in 45,282,023 `simTicks`, between native16
(40,062,748) and native4 (60,314,474), with exact output hash
`7228541527853630339` in every arm.  Hybrid is 5,219,275 ticks (13.028%) slower
than native16 in this matrix.

The causal sweep does **not** support producer result-backing credits or local
transport throughput as the cause of that gap.  The strongest legal
transport-throughput arm (unlimited local retirement rate, 512 write credits)
preserved real memory writes, ACKs, and later consumer visibility and changed
`simTicks` by exactly zero.  It is a throughput upper bound, not a zero-latency
ACK experiment.  ACK latency therefore remains untested, not causal.  A fake
ACK was not used because it could acknowledge data before it was visible to
the consumer.

The post-all-pages-ready hybrid tail is real but is not wholly hybrid-only
overhead.  It is 5,356,056 ticks here: 674,828 page-fill ticks (12.599%),
320,512 ALU ticks (5.984%), 4,360,716 stream-store ticks (81.417%), and zero
truly exposed idle.  The combined STREAM_LD/STREAM_ST share is 94.016%, which
reconciles the earlier 93.9% STREAM / 6.1% ALU classification.  Native16 also
has a long common ALU/STREAM_ST phase: 37,269,223 ticks of native producer /
consumer overlap, including 37,247,313 ticks of producer / STREAM_ST overlap.
Those common stages must not be labeled hybrid-only overhead.

## Matched matrix

All arms used one frozen binary, workload, Ramulator library, and deferred
checkpoint.  Arms ran serially through the checkpoint's absolute selector.

| Arm | words/cycle | credits | `simTicks` | delta vs hybrid | backing credit stalls | backing HWM | last issue to last ACK |
|---|---:|---:|---:|---:|---:|---:|---:|
| native16 | 4 | 64 | 40,062,748 | -5,219,275 | — | — | — |
| hybrid baseline | 4 | 64 | 45,282,023 | 0 | 122 | 64 | 19,406 |
| native4 | 4 | 64 | 60,314,474 | +15,032,451 | — | — | — |
| hybrid width 2 | 2 | 64 | 45,341,493 | +59,470 (+0.131%) | 127 | 64 | 16,902 |
| hybrid width 8 | 8 | 64 | 45,282,023 | 0 | 122 | 64 | 19,406 |
| hybrid credits 16 | 4 | 16 | 45,172,473 | -109,550 (-0.242%) | 3,241 | 16 | 15,024 |
| hybrid credits 32 | 4 | 32 | 45,297,047 | +15,024 (+0.033%) | 228 | 32 | 17,215 |
| hybrid credits 128 | 4 | 128 | 45,282,023 | 0 | 113 | 128 | 30,048 |
| transport upper bound | unlimited | 512 | 45,282,023 | 0 | 0 | 385 | 118,001 |

The credit results are small and non-monotonic: 16 is slightly faster, 32 is
slightly slower, and 128 and the upper bound are identical to baseline.  They
show that credits can perturb scheduling, but neither an ACK-latency claim nor
an explanation of the 5.22M-tick gap follows.  Width 2 is slightly slower;
width 8 is identical to width 4, so this workload sees no benefit above four
virtual words per cycle.

## Hybrid macro ledger

The dedicated `MAAMacroEvent` flag adds two aggregate records per hybrid
descriptor (one producer, one consumer) and zero records for native arms.  It
does not add per-line messages.  Existing `MAAVirtualTrace` line events remain
enabled only because the anchored runner's correctness gates consume them.

Producer baseline boundaries and volumes:

| Phase | first | last issue/insert | last response/ACK | volume / pressure |
|---|---:|---:|---:|---|
| B stream | 3,436,188,494 | 3,458,582,079 | 3,458,865,657 | 1,025 lines, 65,600 B, 0 retries, queue HWM 4 |
| Row/Offset fill | 3,436,229,497 | 3,458,865,657 | — | 16,384 insertions / 17,236 attempts, 852 row-pressure, 0 offset-pressure events |
| A requests | 3,440,368,609 | 3,475,116,930 | 3,475,577,040 | 9,654 lines, 617,856 B, 100 capacity retries, slot HWM 96, word HWM 209 |
| Result backing | 3,440,703,206 | 3,475,680,017 | 3,475,699,423 | 5,133 lines, 328,512 transport B, 131,072 semantic B, HWM 64 |
| Page readiness | 3,466,308,171 | 3,475,699,423 | — | 4 pages; 9,391,252-tick first/last span |

The producer was registered at tick 3,436,188,182 and began execution 312
ticks later.  Its first backing issue to last ACK residence was 34,996,217
ticks, but final drain from last issue to last ACK was only 19,406 ticks.
Pipeline accounting was 1 no-source/no-write cycle, 6,621 source-only cycles,
391 write-only cycles, and 105,865 source/write overlap cycles.  The
no-source/no-write value is intentionally not called globally exposed idle.

The consumer submitted at tick 3,436,602,281, observed all pages ready at
3,475,699,423, and retired at 3,481,055,479.  Across submit-to-retire it had:

- page fill: 1,350,282 active ticks, 4 actions, 2,052 lines / 131,328 B;
- ALU: 641,024 active ticks, 4 actions;
- stream store: 7,940,184 active ticks, 4 actions, 2,052 lines / 131,328 B;
- producer/consumer active overlap: 4,575,434 ticks;
- consumer exposed idle: 34,521,708 ticks, of which 34,521,083 were classified
  `producer_not_ready`;
- action overlap: 0 ticks; post-ready exposed idle: 0 ticks.

The stream-store byte/line fields describe consumer transport footprint; they
are not producer backing ACKs.  Producer result-backing, consumer page-fill,
and consumer stream phases are separate records.

## Native16 coarse timeline

The timeline replay added only `MAATrace` to the debug flags and reproduced
the native16 reference exactly: `simTicks=40,062,748`, output hash
`7228541527853630339`, and byte-identical `result.tsv` SHA-256
`f46854070c5fd04b204fc99ace6c8149363f0e26437191b3781e9e5c55084034`.

| Native stage | start tick | end tick | duration |
|---|---:|---:|---:|
| INDIR_LD_INDEX | 3,436,198,823 | 3,473,576,031 | 37,377,208 |
| ALU_SCALAR | 3,436,306,808 | 3,473,717,820 | 37,411,012 |
| STREAM_ST | 3,436,328,718 | 3,475,568,589 | 39,239,871 |
| STREAM_LD | 3,475,590,499 | 3,475,637,136 | 46,637 |

The union of native ALU/STREAM_ST time overlapping INDIR_LD_INDEX is
37,269,223 ticks.  STREAM_ST alone overlaps the native producer for 37,247,313
ticks and continues 1,992,558 ticks after the producer ends.  These are coarse
unit-active intervals, not exclusive work attribution.  In particular, the
hybrid post-ready tail cannot be subtracted from native completion or called
hybrid-only merely because it contains stream-store and ALU activity.

## Reconciliation and evidence boundary

The earlier `hybrid_tail_causal_audit_2026-08-03.md` reported a 5,256,522-tick
post-ready tail (93.9% STREAM, 6.1% ALU) and zero producer backing writes after
all pages were ready.  This run independently finds a 5,356,056-tick tail;
page-fill plus stream-store is 94.016%, ALU is 5.984%, exposed idle is zero,
and the final backing ACK coincides with all-pages-ready.  Thus the phase
classification and absence of post-ready producer writes reconcile, while the
99,534-tick absolute difference remains a cross-commit/run difference.

The prior per-write trace (5,274 writes, HWM 64, 322 issues at HWM, first issue
to last ACK 35,582,466 ticks, last issue to last ACK 19,406 ticks, latency
median 15,024 / p95 187,487 / max 610,976) is preserved as separate prior
evidence.  This run has 5,133 writes and uses an aggregate credit-stall counter
with different semantics, so those counts are not merged.  Both show long
write residence but short final drain; only the matched sensitivity sweep is
used for causal interpretation.

The anchored meeting matrix remains a different experiment:
native16=40,874,044, hybrid=46,708,677, native4=60,408,687.  The 66.7M/41.3M
pair remains uncomparable absent provenance.  This report does not combine
their `simTicks` with the present matrix.

## Provenance and raw artifacts

Raw root:
`/data1/nier/worktrees/codex-coordination/sessions/hybrid-macro-profile-20260810-20260810-113256-355e631a/artifacts/matrix_948fb143`

- simulator source commit: `948fb143d79485f1d352940c35f65c7ac64aba9d`
- starting commit: `ee08be4bb902ac72ced1f34ed02771cbe9588114`
- gem5 SHA-256: `90e55f95cbd41524c72424838a1d234943060ba4b5d5baa8e4068c25438eb0ee`
- workload SHA-256: `83474017043e110a2623d224f2094d805df684028efaccbe436e0538d4b4c02d`
- Ramulator SHA-256: `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`
- source archive SHA-256: `d84dd43d934e0daa64b5ce69f8abb0d808cbc7bf7423e44179bf3d166cad0e9c`
- checkpoint identity SHA-256: `2079da0984e16520f745c148afc9e2bff143bb79743dca56fdbf12e2e7559c5b`
- `macro_profile.json` SHA-256: `ef90bf8fb3897ae7c502184aec028459ee03dd55b1d435343e1147c21986e41e`
- `macro_profile.tsv` SHA-256: `2742ba11e2c340f907d50ba1363c2fdafc1104aaec0e43f0409fd6d727f7d587`
- `native16_timeline.json` SHA-256: `ca23694239a78929fad2ee00635ac548c61604efb9c8bb8d1af36a2e48c00b37`
- `native16_timeline.tsv` SHA-256: `37fbc0d35e4ac5c2fdf3ec60fea83b258a3510004e4076c4b82c5e0e1c131ae7`
- native timeline trace SHA-256: `361530808de62f8ae62216188f6e2fd85ab85ef4f668c459986dce79dec3264e`

The preliminary `matrix_356924e9` directory was interrupted after exposing a
registration-versus-execution timestamp parser assumption.  It is diagnostic
only and is not used by this report.

## Implementation and validation

Before modification, existing statistics provided coarse fill/request cycles,
write issue/completion counts and HWM, page readiness, and transparent blocker
time.  They lacked one generation-correlated macro ledger with B and A
boundaries, Row/Offset fill, backing issue/ACK closure, consumer action unions,
and true no-action idle.  The anchored runner also hardcoded words/cycle=4 and
write credits=64.  The new aggregate ledger fills those gaps, and the runner
now validates and reports both controls.

Validated with the optimized gem5 build; aggregate tracker optimized and
ASan/UBSan unit tests; deterministic parser contract tests; transparent SPD
controller regressions; shell syntax; Python AST/Black/isort; gem5
modified-region style; and fail-closed matrix/timeline parsers.  No bounded
model file was modified.
