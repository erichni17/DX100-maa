# Independent virtual-tile architecture and evidence review

Date: 2026-08-08  
Audited ref: `origin/codex/integrate-virtualization-week-20260803`  
Audited commit: `0108d9b7a0c9f7818be75745aef3f8b72146c7d4`  
Review scope: source and frozen evidence only; no architecture source changes and no new heavy simulation.

## Verdict

The branch contains useful, correctness-checked mechanisms and honest caveats,
but it does not yet establish a timing-legal virtual-tile architecture or a
design that preserves one unrestricted 16K reorder opportunity. The strongest
accepted result is narrower: the exact-output ping-pong microbenchmark shows a
0.820651469% treatment-only improvement over serial 2K, and the bounded oracle
shows that an offline row-range partition is only 0.658% faster than modulo on
one input. Neither result supports promotion of a general architecture.

The hybrid inherits the native finite RowTable traversal but demonstrably
drains within the 16K instruction. The 4K epoch, modulo, range, and oracle arms
all reorder only within finite batches. Ping-pong leaves producer ordering
unchanged and therefore inherits, rather than preserves, whatever ordering the
producer produced. The live logical-SPD slice starts from coherent backing
memory and has no indirect producer/reorder state at all.

## Findings, ordered by severity

### S1 — The live logical-SPD compute is zero-time host execution

`MAA::serviceLogicalSPD()` invokes `driveCompute()` directly when transport has
no work ([MAA.cc:1481](../../../src/mem/MAA/MAA.cc#L1481)). `driveCompute()`
immediately calls `executeCompute()`
([LogicalSPDCacheRuntime.hh:475](../../../src/mem/MAA/LogicalSPDCacheRuntime.hh#L475)),
which loops over all 4,096 FP64 elements in `Datapath::transform()`
([LogicalSPDCacheDatapath.hh:83](../../../src/mem/MAA/LogicalSPDCacheDatapath.hh#L83))
and completes the controller action in the same simulator call. It does not
reserve the modeled 16-lane ALU, schedule ALU cycles, or contend with ordinary
ALU instructions.

The memory fills and write acknowledgements are real timing requests, so this
is a functional vertical slice, not a fully timing-legal one. Its smoke runner
correctly declares `isoarea_timing_claim=0`
([run_logical_spd_cache_live_smoke.sh:53](../../scripts/run_logical_spd_cache_live_smoke.sh#L53)),
but any live-slice `simTicks`, overlap, or speedup would be invalid until
compute consumes modeled cycles and resources.

### S1 — No measured design preserves one unrestricted 16K reorder window

The matched campaign calls its control `hybrid_full_metadata`, but the actual
control has 16K Offset entries and a finite RowTable with 64 rows per slice
([run_bounded_row_matched_matrix.sh:109](../../scripts/run_bounded_row_matched_matrix.sh#L109)).
The RowTable is drained on insertion failure
([IndirectAccess.cc:1128](../../../src/mem/MAA/IndirectAccess.cc#L1128)). In the
frozen accepted raw control, `system.maa.I0_IND_NumRTFull=859`, and the campaign
records 9,729 inserted source lines and 102 build rounds. Thus this workload did
not retain all 16K candidates in one jointly visible ordering domain. It
inherited native finite row scheduling and lost global visibility at its drain
boundaries.

The status by design is:

| Design | 16K reorder status | Evidence |
|---|---|---|
| Hybrid 4K payload / 16K Offset | **Inherited but not preserved.** One B scan, native row order within each finite RowTable batch; raw control has 859 full events. | Source above; [campaign counters](../../campaigns/2026-08-03_bounded_row_matched_oracle/tables/counters.tsv#L2). |
| One-pass 4K Row/Offset epoch | **Lost.** Fill/drain epochs deliberately end joint visibility at 4K or earlier row pressure. | [offset-capacity conclusion](../../campaigns/2026-07-26_transparent_virtual_direct_gather/notes/offset_capacity_epoch.md#L38). |
| Four-pass modulo | **Partitioned, not preserved.** Every item is globally classified from a repeated B scan, but only one finite pass/epoch is jointly reorderable. | [selector source](../../../src/mem/MAA/IndirectAccess.cc#L717). |
| Fixed/source/oracle ranges | **Partitioned and sometimes split again.** The source range admits 5,743 in pass 0 and the oracle admits 4,560 in pass 3, both above the 4,096 active limit. Internal drains split those passes. | [pass populations](../../campaigns/2026-08-03_bounded_row_matched_oracle/tables/pass_populations.tsv#L6). |
| 2K ping-pong consumer | **Inherited unchanged.** All arms have the same producer source-read and inserted-row counts; the controller changes consumer scheduling only. | [accepted JSON](../../analysis/isoarea_pingpong_final_reproduction_2026-08-03.json#L1). |
| Live logical-SPD slice | **Not present.** It registers an already materialized coherent source and performs scalar page transforms; no indirect producer metadata crosses the interface. | [submission path](../../../src/mem/MAA/MAA.cc#L1177). |

The phrase “full metadata” is therefore a capacity/configuration label, not
evidence of a full-instruction ordering lifetime.

### S1 — Published total-storage comparisons are superseded and the live range tracker is undercharged

Older notes still quote 653,138-byte bounded and 842,482-byte full-metadata
totals ([storage_model.md:17](../../campaigns/2026-07-26_transparent_virtual_direct_gather/notes/storage_model.md#L17),
[hybrid_reorder_cost_analysis:119](../../analysis/hybrid_reorder_cost_analysis_2026-08-02.md#L119)).
The later field-complete audit explicitly removes those totals and withholds a
new full-mechanism total until SPD, combiner, readiness, and writeback state are
field-complete ([bounded report:243](../../bounded_row_study_2026_08_03/report.md#L243),
[bounded report:270](../../bounded_row_study_2026_08_03/report.md#L270)). They
must not be used for an area or percentage-reduction claim.

There is also a concrete undercount in the live range-pass source.
`chargedBytes()` counts two bitmaps, two 64-entry counter arrays, and one
64-entry finished array
([BoundedRangePass.hh:237](../../../src/mem/MAA/BoundedRangePass.hh#L237)), but
omits `passRanges[64]` and all scalar configuration/count state declared at
[BoundedRangePass.hh:283](../../../src/mem/MAA/BoundedRangePass.hh#L283).
Source-semantic arithmetic is 5,733 bytes, not the reported 4,672: 4,096 bitmap
bytes + 512 counter bytes + 64 finished bytes + 1,024 range bytes + 37 scalar
bytes. The live checker is underreported by 1,061 bytes before host padding.

Finally, current gem5 allocates every RowTable organization even when
reconfiguration is disabled
([IndirectAccess.cc:206](../../../src/mem/MAA/IndirectAccess.cc#L206)). A
target-only active-layout ledger may remove those arrays, but then it is a
synthesis/design assumption, not “current allocated source.”

### S2 — The accepted ping-pong number is reproducible but not promotion-grade provenance

I regenerated the committed JSON byte-for-byte from the accepted raw root. The
three arms use the same gem5 (`edad388f...`) and workload (`84062d31...`), have
empty source status/diffs, matching exact output, and resolved MAA configs that
differ only in `transparent_spd_mode`. The inner matrix has `matrix.exit=0` and
`matrix.complete`.

However, the committed analyzer reads only `result.tsv` and the controller
trace ([analyze_isoarea_pingpong.py:30](../../analysis/analyze_isoarea_pingpong.py#L30),
[analyze_isoarea_pingpong.py:179](../../analysis/analyze_isoarea_pingpong.py#L179)).
It does not authenticate terminal logs, artifact hashes, source snapshots,
resolved configs, or checkpoint contents. The raw root contains no frozen
checkpoint inventory/hash, and the outer `matrix.launch.exit` is missing, as
the accepted report acknowledges
([final reproduction:14](../../analysis/isoarea_pingpong_final_reproduction_2026-08-03.md#L14)).
There is one observation per arm. Therefore 0.820651469% is an accepted narrow
observation with a wrapper/provenance caveat, not a general useful-concurrency
or promotion result. The interval intersections are envelopes, not dual-progress
proof.

### S2 — The “balanced 4K oracle” is offline, not capacity-balanced, and gets free policy logic

The boundaries are injected through a configuration vector
([MAA.py:110](../../../src/mem/MAA/MAA.py#L110)) and linearly searched at zero
selector-specific modeled latency
([BoundedRangePass.hh:145](../../../src/mem/MAA/BoundedRangePass.hh#L145)). The
common 16-words/cycle filter charge accounts for examined B words, but not
discovering/sorting the boundaries, storing a profile, comparator depth, or the
different modulo-versus-range datapath. Only the range arm carries the exact-once
tracker storage.

The oracle is also not actually balanced to 4,096: populations are
3,716/4,055/4,053/4,560. The tracker checks exact once and final closure, but it
does not enforce `passAdmissions <= activeEntries`
([BoundedRangePass.hh:156](../../../src/mem/MAA/BoundedRangePass.hh#L156)). The
4,560-entry pass is made legal by an internal 4K drain, which further limits
reorder visibility. The repeated deterministic 0.658% win over modulo is still
useful as an upper diagnostic, but its 410,656 ticks are smaller than unmodeled
online policy cost could plausibly be. It does not justify an online range
controller.

### S2 — The live logical-SPD evidence is not independently frozen at this ref

The branch includes a fail-closed runner and exact-output benchmark, but no
committed live-smoke manifest/result and no discoverable raw live-smoke root.
The benchmark covers one aligned, non-overlapping FP64 multiply
([test_logical_spd_cache_live.cpp:47](../../../benchmarks/API/test_logical_spd_cache_live.cpp#L47)).
The source-contract/unit tests validate controller identities and bounded
ownership, not the gem5 timing path.

Uncovered live cases include back-to-back logical operations, shared-port
retry pressure, dirty CPU source lines, stale/duplicate real responses,
mid-operation checkpoint/drain, multiple MAAs, a competing native ALU, and an
indirect producer. The translation path additionally panics unless timing
translation completes immediately
([MAA.cc:1318](../../../src/mem/MAA/MAA.cc#L1318)), so TLB-miss/page-walk behavior
is outside current evidence.

### S2 — Source-relative ranges assume endpoint-bounded physical placement

`directIndexSourceGrowRange()` translates only the first and last virtual
addresses and treats their min/max grows as the entire interval
([IndirectAccess.cc:727](../../../src/mem/MAA/IndirectAccess.cc#L727)). That is
safe for the measured contiguous SE placement, but it is not a general proof
for fragmented physical pages or a non-monotone mapping. An intermediate grow
outside the endpoint interval fails closed at `passForGrow`; it does not
silently corrupt output, but the source-relative mechanism is not generally
available under ordinary virtual-memory placement.

### S3 — Representativeness and comparison boundaries remain narrow

All headline integration measurements use the same synthetic 16K FP64 consumer
and one output hash. The oracle campaign has replicas only for modulo and the
offline oracle; deterministic identical observations do not add workload
diversity. The hybrid attribution itself warns that one observation cannot
establish causality
([hybrid tail audit:3](../../analysis/hybrid_tail_causal_audit_2026-08-03.md#L3)).

Missing gates are: a row-buffer-locality sweep, adversarial pass skew in live
gem5, a representative XRAGE/CG path at this audited commit, multi-unit and
multi-MAA contention, conditional/false-predicate inputs, unaligned B/C spans,
and repeated logical producer-consumer chains. A separate session reported an
XRAGE successor commit `f8b56d35396e`; it is not part of audited commit
`0108d9b` and requires independent provenance/correctness review before it can
close this finding.

The matched bounded matrix is an architecture comparison, not a single-factor
experiment: its control changes Row capacity, Offset capacity, pass count, B
traffic, filter work, and checker state together. Oracle versus modulo is the
closest policy comparison, but still differs in range lookup and exact-once
tracker state. Ping-pong 2K versus serial 2K is the only clean treatment-only
schedule comparison; serial 4K is fixed-area design context because chunk size
also changes.

## Recalculated storage ledger

These are source-grounded capacities, not synthesized area. “Simulator
semantic” counts the byte-width arrays represented by current C++ source;
“target lower bound” independently byte-packs fields as documented by the
finite model. Cache tags, SRAM periphery, arbitration, wiring, allocator nodes,
and physical design remain excluded.

| Component | Bytes | Scope / derivation |
|---|---:|---|
| Visible 4K SPD payload | 524,288 | 32 lanes × 4,096 × 4 B. |
| Private logical-SPD payload | 65,536 | Two FP64 4K slots; always constructed per MAA at this ref. [Runtime:17](../../../src/mem/MAA/LogicalSPDCacheRuntime.hh#L17). |
| Total MAA-local payload currently allocated | 589,824 | Visible plus private logical payload; identical across ping-pong arms. |
| Visible SPD simulator metadata | 131,392 | Includes one byte per element readiness; [ledger source:56](../../analysis/isoarea_pingpong_ledger.py#L56). Bit-packing readiness would reduce this hardware lower bound to 16,704 B. |
| Logical Runtime packed total / non-payload state | 66,785 / 1,249 | Compile-time source assertion; [Runtime:239](../../../src/mem/MAA/LogicalSPDCacheRuntime.hh#L239). |
| Transparent controller semantic state | 183 | Common to serial/ping-pong arms. [ledger source:67](../../analysis/isoarea_pingpong_ledger.py#L67). |
| Current hybrid all-organization RowTable arrays | 616,734 | 32,768 entries + 1,920 rows across 2/4/8/16-slice organizations. |
| Current hybrid Offset arrays | 278,528 | 16,384 entries × (12-B fields + 1-B valid + 4-B free index). |
| Current bounded all-organization RowTable arrays | 308,382 | Same source allocation rule at 32 rows/slice: 16,384 entries + 960 rows + 30 request bits represented as bytes. |
| Current bounded Offset arrays | 69,632 | 4,096 entries under the same source-semantic rule. |
| Prospective one-active-layout bounded metadata | 132,236 | Field-complete 4K Offset/row/line, response, invalidator, range/cursor/control ledger. [bounded report:243](../../bounded_row_study_2026_08_03/report.md#L243). Not current all-organization allocation. |
| Destination combiner data + semantic tags | 28,800 | 384 × 64-B data + 384 × 11-B tags. |
| Response line arrays + semantic tags | 10,848 | 96 × 64-B arrays + 96 × 49-B tags. A configured 3,840-B packed-word occupancy exists separately and must not silently replace these arrays. |
| Four-line B feeder payload | 256 | Tags/state and map-node overhead are not closed by the existing ledger. |
| Range exact-once tracker | 5,733 | Corrected source-semantic count; current `chargedBytes()` reports 4,672. |

No honest full-mechanism total follows from mixing the all-organization
simulator allocation with the one-active-layout target ledger. For a target
claim, first choose and state whether inactive RowTable organizations and the
always-constructed 65,536-byte logical Runtime are present. Then close B-feeder
tags, page/write ownership, combiner/writeback state, port queues, and checker
state under the same packing rule. Until then, the prior 72.979% reduction is
withdrawn, not updated.

## Ranked next experiments

| Rank | Experiment | Expected information gain | Implementation / run cost | Decisive outcome |
|---:|---|---|---|---|
| 1 | **16K reorder-survival matrix.** Add treatment-neutral epoch/drain IDs to every admitted and issued A line, then run the existing shared-checkpoint microbenchmark at low/mid/high row locality plus one reviewed XRAGE or CG input. Compare hybrid, one-pass 4K, modulo-4K, and an adequately provisioned no-drain reference. | Very high: directly resolves whether useful 16K ordering is preserved, inherited in finite batches, or lost, and separates row locality from C-write coalescing. | Low–moderate: counters plus existing light cases; one representative arm set. | Per-arm maximum jointly visible admissions, drain count, per-slice row transitions, exact output, and simTicks all reconcile. A “16K” claim requires zero mid-instruction drains and all 16K candidates in one ordering domain. |
| 2 | **Causal hybrid-tail intervention.** With one binary/checkpoint, change exactly one legal STREAM consumer-service constraint and require the reduction in STREAM-busy residency to match the simTick reduction without new producer-not-ready, IF-full, output, or traffic changes. | High: tests the measured ~5.1M-tick post-ready tail rather than inferring causality from residency. | Low–moderate: localized knob/intervention and two arms. | Delta reconciliation either validates consumer service as causal or falsifies it. |
| 3 | **Timing-legal logical-SPD slice with contention.** Route page compute through the existing 16-lane ALU (or an equivalent explicitly scheduled resource), then run exact-output native/logical pairs with a competing native ALU and cache-port backpressure. Freeze a manifest/checkpoint inventory. | High: determines whether the live cache is a viable architecture after removing zero-time compute and exposes resource conflicts hidden by the smoke. | Moderate–high: scheduler/tag plumbing, but only light smoke/pair runs. | No host-loop completion, modeled ALU cycles/ownership, exact ACK completion, exact output, and a matched timing comparison. |

The separately reported XRAGE successor is a useful candidate input for rank 1,
not evidence that rank 1 has already passed.

## Verification performed

- Synced coordination context before inspection; no architecture source was
  modified.
- Regenerated the accepted ping-pong JSON byte-for-byte from
  `/data1/nier/dx100-runs/2026-08-03-isoarea-pingpong-repair-c26a082`.
- Audited raw terminal/config/artifact markers for ping-pong and raw mechanism
  counters for the bounded matched campaign.
- Ran 78 direct/unittest checks successfully: iso-area analyzer/ledger (6),
  bounded-range contract (12), logical-SPD ABI/lifecycle/controller/payload
  contracts (34), and bounded finite model/evidence checks (26).
- `pytest` itself was unavailable in the environment; the same test files were
  executed through their direct `unittest` entry points.

