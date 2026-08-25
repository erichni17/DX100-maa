# SSSP coherent fallback consumer (2026-08-25)

## Decision

The fixed-aperture SSSP successor replaces every host read of `tilev`,
`tile1`, and `tilei` in the hybrid build. Exact 4,096-word non-routed pages
are response-published into the already charged per-thread coherent index,
value, and predicate arrays, then consumed from those arrays. A final partial
page is reconstructed in range-loop cursor order from coherent graph/frontier
state before any partial MAA tile is issued.

This is a correctness gate, not a performance promotion. Final archived-gem5
evidence and its immutable root are recorded below after execution.

## Failure and repair boundary

Frozen failure provenance is
`/data1/nier/dx100-runs/2026-08-24-sssp-aperture-full-s22-r1/run/restore.log`.
The O3 CPU issued a non-speculative/task-unknown line at `0x801c4000`, physical
tile 28, element 4,096, immediately after the 4,132-frontier marker. That is
the first line beyond the 4,096-element physical tile and must remain rejected.
No aperture, padding, guard SRAM, or prefetch-drop policy changed.

The response-bearing publisher accepts exactly one complete 4,096-element
source tile. The successor therefore has two explicit routes:

- remaining words at least 4,096: issue the normal ordered MIN/reload/winner
  chain for one full physical page, publish index/final-value/predicate with
  three complete response-bearing publications, wait for every completion,
  consume ordinary coherent backing, and restore that predicate page to ones;
- remaining words from 1 through 4,095: reconstruct candidates through the
  exact active-edge cursor and apply ordered MIN plus the batch-final winner
  test entirely in coherent memory.

An observed-greater-than-preflight guard executes before unsigned remaining
work subtraction. Backing pages cycle 0, 1, 2, 3 only after immediate
consumption. The dead EQ (`tileu`) and GT (`tile2`) tiles serve as completion
tokens; the shared publisher helper waits before returning, so sequential
reuse and the next range iteration cannot begin early. The routed path remains
four physical pages per logical 16K window.

## Correctness and accounting

The terminal `counts_close` value is the acceptance authority. It requires,
per thread and in aggregate:

- measured host-SPD reads and illegal line starts are zero;
- fallback publication issue pages equal response-complete pages and equal
  three times the number of full fallback pages;
- publication words equal `pages * 3 * 4096`;
- predicate restore words equal `pages * 4096`;
- consumed fallback words equal full-page words plus coherent-tail words;
- legacy words equal measured fallback consumption;
- routed index/value pages and old-result words retain their prior 16K closure.

Hardware accounting remains unchanged: `NUM_TILES_PER_CORE=8`, no new
dedicated payload bytes, no hidden logical SPD bytes, and no new backing array.
The three fallback payloads reuse `sssp_hybrid_indices`,
`sssp_hybrid_values`, and `sssp_hybrid_predicates`; the existing old-result
array remains the routed result backing and coherent-tail scratch.

## Deterministic reproducer and evidence

The immutable runner is
`experiments/scripts/run_sssp_coherent_fallback_reproducer.sh`. It fixes gem5
to SHA-256 `703c1e1d756ada75306e7ed941f3dad967370cd4f224c092430b5b2b5fb0f1a5`
and Ramulator to SHA-256
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
It constructs vertex 0 -> 4,096 middle vertices -> four unique leaves per
middle vertex. At the 4,096-entry frontier, four 1,024-vertex chunks each
produce one exact 4,096-edge non-routed fallback page.

Accepted correctness evidence is
`/data1/nier/worktrees/codex-coordination/sessions/sssp-coherent-fallback-consumer-20260825-011336-3b0f1952/evidence/sssp-coherent-fallback-761dd0f-r1`.
It was run from clean source commit `761dd0f335e7b55a864819a3dc18d96f9c127a04`.
The guest SHA-256 is
`b000b82dc43f59a390e3a68761539a3edc5aed70c9fcf660da51f82791cabe34`;
the graph SHA-256 is
`2fd29eff77359d6e5297164769d9885ae176bf3ad146e309e9026f627e3b2175`.
The before/after artifact manifests match and revalidate.

The restore exited through the `m5_exit` instruction and produced nonempty
final stats. The exact fingerprint is 20,481 reached vertices, distance sum
36,864, maximum distance 2, hashes `145bdeed9b3787df` and
`bc382214847f7b99`, with zero triangle, predecessor, weight, or distance
violations. The terminal record reports:

- 0 eligible/routed logical windows and 16,384 legacy fallback words;
- 4 fallback pages and 0 coherent partial-tail batches;
- 12 publication issue pages, 12 response-complete pages, 49,152 published
  words, and 196,608 published bytes;
- 16,384 consumed fallback words and 16,384 predicate restore words;
- 0 host-SPD reads, maximum host-SPD element `-1`, and 0 illegal host-SPD line
  starts;
- `response_closure=1` and `counts_close=1`.

Hardware statistics independently close 3,072 publisher WriteReq issues,
3,072 cache accepts, and 3,072 WriteResps across 12 terminal publications.
Both `cpu_spd_boundary_prefetch_drops` and
`cpu_spd_out_of_range_rejections` are zero. `simTicks=616849132` is retained
as provenance for this small correctness reproducer only; it is not a
performance or full-S22 claim.

## Full-cache routed-path successor

The corrected full-cache small gate is
`/data1/nier/dx100-runs/2026-08-25-sssp-coherent-small-fullcache-r2`, launched
from lead commit `e152d692`. It uses the same cache/prefetch and MAA geometry as
the full S22 runner, the archived aperture-capable gem5 SHA-256
`1e079112...a9863`, and the frozen Ramulator SHA-256 above. The runner exits
zero and writes `gate.complete` after matching its before/after artifact
ledgers.

The exact graph fingerprint passes with 69,633 reached vertices, distance sum
135,168, maximum distance 2, and hashes `a0531a7ddb9387df` and
`39f1ea63bc8817e8`. All four eligible logical windows route through the 16K
path; 16 index and 16 value pages publish, 65,536 old-result words close, and
the coherent fallback remains dormant. The terminal reports zero fallback
pages/tails, zero host-SPD reads, maximum host-SPD element `-1`, zero illegal
line starts, response closure, and count closure. Hardware counters report
65,536 captures and 18,173 balanced old-result write issues/responses. Both
aperture counters are measured as zero. `simTicks=8,102,572,469` is routed-path
correctness provenance, not a promoted performance result.

The preceding `r1` simulation produced the same exact fingerprint and terminal
mechanism record, but its shell gate exited 2 because gem5 omitted two
zero-valued aperture counters from `stats.txt`. Commit `e152d692` makes only
those optional zero-valued instrumentation counters default to measured zero;
all functional and traffic counters remain mandatory. The successful `r2`
fresh run validates that correction rather than retroactively promoting `r1`.

The candidate-only full S22 successor is active at
`/data1/nier/dx100-runs/2026-08-25-sssp-coherent-full-s22-r2`. No full-S22
correctness or timing claim is made until its independent exact fingerprint,
fallback-publication closure, artifact ledger, and wrapper gate pass.
