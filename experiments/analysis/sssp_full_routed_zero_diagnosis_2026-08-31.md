# Full SSSP S22 routed-zero diagnosis (2026-08-31)

## Decision

The frozen full S22 execution is a **complete, exact-correct fallback run**, but
it is a **rejected hybrid-performance gate**.  gem5 restored and exited through
`m5_exit`, both statistics windows are present, the SSSP fingerprint exactly
matches the frozen native16 oracle, and every coherent fallback ledger closes.
The wrapper and its later callback correctly return one because the runner
requires at least one routed window and the run routed zero.

The immediate routing condition is exact and guest-side:

```c++
const bool route_page = hybrid_iteration_safe &&
    hybrid_observed_words < hybrid_route_words;
```

For the pages belonging to any of the 7,226 complete, size-eligible logical
windows, `hybrid_observed_words < hybrid_route_words` is true.  Consequently
the condition that forced all of those pages into `if (!route_page)` was
`hybrid_iteration_safe == false`.

This is not stale gem5 or MAA configuration.  The configuration resolves the
requested 16,384-word logical aperture, 4,096-word physical payload, 16,384
offset and epoch entries, 32 initial row-table slices, eight active contexts,
64 active value owners, value cache, and pre-A lookahead.  Rather, two facts
coexist:

1. A general undirected SSSP frontier legitimately contains active-source and
   shared-destination hazards which must make the affected aggregation fall
   back.  The safety check is not itself optional.
2. `sssp.cc` stores the result in one iteration-wide Boolean.  A single hazard
   anywhere in an iteration rejects every chunk and every full logical window
   in that iteration.  That all-or-nothing scope is a guest admission-granularity
   bug for a gate that claims to route an eligible subset; it is not intended
   workload behavior and it is not a simulator knob problem.

The frozen record does not preserve which leaf or how many leaves cleared the
Boolean.  It has no rejection-reason counters.  The source permits invalid
delta/bin, invalid source/distance, invalid destination/weight/candidate, a
destination that is also an active source, or destination ownership crossing
frontier chunks to clear the same bit.  The exact fingerprint excludes
nonpositive weights and negative/final-distance corruption, but it cannot
retroactively distinguish the active-source and cross-chunk-owner leaves.
Claims assigning all 7,226 windows to either leaf would therefore exceed the
immutable evidence.

No simulator or benchmark source was changed and no gem5 run was launched for
this diagnosis.

## Frozen identities and completion classification

Candidate root:
`/data1/nier/dx100-runs/2026-08-25-sssp-coherent-full-s22-r2`.

| Item | Frozen identity |
|---|---|
| Source commit | `e152d6922e48ca0342f170e3e73f267d297c315d` |
| `benchmarks/gapbs/src/sssp.cc` | `07b8a02cc96ef8bf42ab2c9622de8da7c99efc8b2fdac257ef355168dbadd116` |
| `sssp_coherent_fallback.hh` | `f583c6dbc60279c7e0eb9747a20f546c9ba58dd611d6fea11cb798e7422357c1` |
| full runner | `d8d5a62d8c4f38760b7252ad106ed0c21e4cc854c7aaeef3425d3c88727bcc96` |
| candidate guest | `3719bf7812a67681c8087887af306ab66c813da77e75678e3d818406c7d4fa17` |
| graph | `23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc` |
| candidate gem5 | `1e079112469892681d661925db09ccfbc845d1a2ce45c79e1d9a4902c19a9863` |
| Ramulator library | `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753` |
| candidate restore log | `run/restore.log`, 3,572 lines |
| candidate stats | `run/stats.txt`, two complete windows |

The present worktree copies of the source, helper, and runner have those exact
hashes; `git diff e152d692 --` for the three paths is empty.  The executable
command is preserved at `run/command`; it restores checkpoint one on four
X86O3 CPUs with the stated cache, memory, and MAA surface.  The checkpoint
builder exits zero and the restore exits zero.  The restore terminates with:

```text
Exiting @ tick 12801723668434 because m5_exit instruction encountered
```

The first ROI statistics window is nonempty and reports
`simTicks=10819081747253`; the second reports `12779921871282`.  Completion is
therefore not inferred from a dead process or from wrapper status.

The exact post-ROI certificate is:

```text
SSSP_FINGERPRINT vertices=4194304 reached=4194304 unreachable=0 distance_sum=569278395 max_distance=258 hash_a=aaf3a6a5d4662d36 hash_b=9ffcf4962b364007 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS
```

It is byte-for-byte the oracle stored in `external_reference.manifest`.
Correctness and fallback response closure pass even though promotion does not.

The distinction is visible in persistent status:

- `checkpoint.exit=0` and `run/restore.exit=0`: simulation success;
- `counts_close=1`, `response_closure=1`, exact fingerprint `PASS`: guest and
  fallback correctness success;
- `wrapper.status: exit_code=1` and `callback.validation.status:
  validation_exit=1`: hybrid gate rejection.

The rejection occurs at the runner's explicit
`(( routed > 0 && routed <= eligible ))` check.  Because it happens before the
SoA/JIT mechanism checks, absent `result.txt`/`gate.complete` must not be
misread as simulator failure.

## Exact route and fallback control flow

The guest performs three separate decisions.

First, once per delta iteration it constructs `hybrid_active_sources` and one
global `hybrid_iteration_safe` latch (`sssp.cc:528-592`).  The latch starts
true and is cleared by any of:

- nonpositive delta or overflowing bin lower bound;
- an invalid frontier source or out-of-domain source distance;
- an invalid destination, nonpositive weight, overflowing candidate, or
  out-of-domain destination distance;
- `hybrid_active_sources[wn.v]`, meaning an aggregated MIN could change a
  source operand used later in the same iteration; or
- the same destination observed under two different
  `chunk_owner = pos / chunk_frontier_words` values.

Second, each MAA frontier chunk independently computes only a **size count**:

```c++
hybrid_route_words =
    (hybrid_chunk_words / kSsspLogicalWords) * kSsspLogicalWords;
eligible_windows += hybrid_chunk_words / kSsspLogicalWords;
```

Thus “eligible” means only that the chunk contains another complete 16K edge
window.  It does not mean that the iteration safety proof admitted it.  The
name is misleading unless paired with a safety-rejection ledger.

Third, for each complete physical page, `route_page` combines the global latch
with the chunk's remaining complete-window bound (`sssp.cc:763-794`).  A false
route executes the ordinary ordered MIN, reload, winner predicate, and
`PublishAndConsumeSsspFallbackPage`.  That function response-publishes index,
final-value, and predicate pages, waits for completion, consumes coherent
backing, restores the all-ones predicate page, and records one fallback page.
The helper's final partial route uses exact cursor reconstruction only when
remaining work is 1--4,095 words; it is not involved in rejecting a complete
page.

The exact terminal line is:

```text
SSSP_OLD_RESULT_HYBRID_TERMINAL treatment=old_result_hybrid eligible_windows=7226 routed_windows=0 index_publish_pages=0 value_publish_pages=0 old_result_words=0 legacy_words=133103306 fallback_pages=31492 fallback_publication_issue_pages=94476 fallback_publication_response_pages=94476 fallback_publication_words=386973696 fallback_publication_bytes=1547894784 fallback_consumed_words=133103306 predicate_restore_words=128991232 coherent_tail_batches=2394 coherent_tail_words=4112074 logical_reorder_words=16384 physical_spd_words=4096 row_table_slices=32 predicate_span=coherent_aligned old_result_span=coherent_aligned duplicate_order=legacy_physical_pages host_spd_reads=0 max_host_spd_element=-1 illegal_host_spd_line_starts=0 new_dedicated_payload_bytes=0 hidden_logical_spd_bytes=0 hidden_result_payload_bytes=0 response_closure=1 counts_close=1
```

The arithmetic closes independently:

- 7,226 eligible windows = 118,390,784 eligible words = 28,904 physical
  pages, 88.946539% of all 133,103,306 legacy words;
- 31,492 full fallback pages x 4,096 = 128,991,232 words;
- 128,991,232 full-page words + 4,112,074 tail words = 133,103,306;
- 31,492 pages x three published arrays = 94,476 issue pages = 94,476
  response pages;
- 94,476 x 4,096 = 386,973,696 published words = 1,547,894,784 bytes
  (1.441589 GiB);
- hardware independently reports 24,185,856 publisher line issues, exactly
  94,476 x 256 cache lines; and
- every per-unit SoA/JIT instruction, terminal, selection, old-result capture,
  and old-result write counter is zero.

The coherent helper did precisely what it was designed to do.  It is not the
source of the routed-zero decision; it is entered after the guest predicate
has rejected the page.

## Workload structure versus guest defect

The frozen `.wsg` header says the graph is undirected (`directed=0`) with
4,194,304 vertices and 134,217,158 directed adjacency entries.  Unlike the
small routed-path graph, which is deliberately directed and gives each active
middle vertex distinct non-active destinations, a general undirected RMAT
SSSP frontier naturally permits edges back into the current active set and
high-fan-in destinations reached from multiple frontier chunks.  Falling back
for an aggregation containing either hazard is intended correctness behavior.

What is not intended is propagating one such hazard through a single Boolean
to unrelated chunks.  The code comments say destinations must not cross
“chunk owners,” but the implementation records only the owner identity and
one iteration-wide result.  There is no per-owner admission state.  Therefore
the current guest cannot express “unsafe chunk A, safe chunk B” even though
the runner explicitly allows `routed <= eligible` and labels a partial route
as `eligible_subset_routed_fallbacks_preserved`.

This mismatch was latent in the existing small gate: its input construction
eliminates every hazard across the entire iteration, so it proves the all-safe
case (four of four windows) but not mixed safe/unsafe admission.  The full S22
run exposes the missing mixed case.  Changing gem5 knobs, value-cache knobs,
old-result credits, Row/Offset capacity, or the coherent fallback helper
cannot make the Boolean true.

## Timing consequence versus frozen native16

The external reference is:
`/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/repair3-validation/gapbs/sssp_s22_t16384_m2GB_gem5.opt.ovl_base_sha256_1ff4a396b98d6c838f695c4cbd631ca16e7ed12407365f17707bcf6df93e1343`.
Its `run.log` and `stats.txt` hashes are respectively
`20012684fa3cd2a4d6e6d75ecdb05f82ad818a3315e69afdd18b6c4a6f6798b7`
and `8bbef41c6ce03ec98ba11d9261469e9030f5d5b4128e3cf7a23f9a4ec85a94ad`.
It uses the same graph/options, four O3 CPUs, cache hierarchy, two Ramulator
channels, 3.2 GHz clocks, one MAA, 16K native tiles, and 32 row-table slices;
its exact fingerprint matches.

| Arm | First ROI `simTicks` | Relative result |
|---|---:|---:|
| frozen native16 | 758,524,789,379 | 1.000000x |
| full candidate, routed zero | 10,819,081,747,253 | 14.263319932x native16 time |

The routed-zero candidate consumes 10,060,556,957,874 more ticks and is
1,326.331993% slower than native16; equivalently native16 takes only
7.010990% of the candidate time (a 92.989010% reduction).

This is a measured end-to-end comparison, not a causal decomposition.  The
candidate also changes native16 physical storage into 4K physical pages and
adds response-bearing coherent fallback publication.  With no routed
candidate window and no matched repaired arm, the evidence cannot assign all
10.061e12 excess ticks specifically to the Boolean or predict the speedup of a
repair.  It does establish that the completed candidate exercised none of the
treatment under evaluation and is therefore unusable as hybrid promotion
evidence.

## Smallest discriminating micro gate

Add one candidate-only, exact-fingerprint graph beside the existing small
runner; do not begin with full S22.  Its second iteration should contain
exactly 4,096 active middle vertices so the MAA path chooses four 1,024-source
chunks:

1. chunk 0 produces exactly 16,384 edges to distinct, non-active leaves (one
   safe, size-eligible logical window);
2. chunk 1 contains one deliberate edge to an active middle vertex (one local
   active-source hazard); and
3. chunks 2 and 3 contain no destination shared with chunk 0.  A companion
   variant should replace the active-source edge with one destination shared
   by chunks 1 and 2 to isolate cross-owner poisoning.

The current code must report one eligible, zero routed and a closed fallback.
The repaired code must report one eligible, one routed for the active-source
variant, with exactly four index pages, four value pages, 16,384 old-result
words, one SoA/JIT instruction/terminal, an exact fingerprint, and fallback
only for the deliberately unsafe work.  The cross-owner variant must reject
both conflicting owners while retaining the independent safe owner.  In both
variants require zero host-SPD reads/illegal lines, exact publication
issue/response closure, exact old-result response closure, and `counts_close=1`.

Before any gem5 micro run, extract the admission calculation into a pure C++
helper and unit-test the same two owner maps.  That is the cheapest test that
fails the present global-Boolean implementation and proves the intended
mixed-admission semantics without simulator noise.

## Smallest source repair

The repair belongs only in the guest admission preflight and its tests.  Do
not change the simulator, the coherent fallback helper, geometry, or fallback
semantics.

1. Keep the iteration-wide immutable active-source snapshot and global
   domain/bounds validation.
2. Allocate/reset a bounded admission bit per frontier chunk owner for the
   current iteration, initially true.
3. When an edge names an active source, clear only its source chunk's bit.
4. Track each destination's first owner as today.  On a different owner, clear
   both the first and current owners; retain a conflict marker so later owners
   are also cleared.
5. Replace `hybrid_iteration_safe` in `route_page` with the bit for the current
   chunk owner.  Preserve the complete-window bound and every existing
   fallback, cursor, response, duplicate-order, and terminal closure check.
6. Add terminal counts for chunks/windows rejected by bounds, active-source,
   and cross-owner reasons.  A chunk with multiple reasons may either use a
   reason bitmask or explicitly documented nonexclusive counts.

This is the minimal safe granularity relaxation.  A still finer per-logical-
window proof would require mapping the ordered edge stream before range-loop
execution and is not necessary to fix global poisoning.  The mixed micro gate
must pass before another full S22 run.  A fresh full run may still route few or
zero windows if every individual chunk is genuinely unsafe; that would then be
measured workload structure rather than an admission-scoping defect.

## Handoff

- Preserve the frozen root as correctness/fallback evidence; do not promote
  its timing as hybrid treatment evidence.
- Do not weaken or delete the active-source/cross-owner safety rules.
- First add reason counters and the two mixed-owner micro cases, then make
  admission per chunk.
- Only compare repaired full S22 timing after exact fingerprint, full
  mechanism closure, matched configuration, and a nonzero independently
  evidenced routed count.
