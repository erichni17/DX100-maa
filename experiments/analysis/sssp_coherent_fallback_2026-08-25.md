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

Evidence status: pending clean-tree execution.
