# Strict line-combined promotion checkpoint (2026-08-27)

## Current decision

Keep strict line-combined retirement **default-off and CG-specific**.  It is an
exact, measured optimization for the page-fed CG P-result edge, but it is not
yet a full-workload, native4, iso-area, or cross-application promotion.

The accepted bounded NA1024 arm reduces `simTicks` from 2,386,167,394 to
2,213,855,573 (7.2213% lower) while preserving the exact CG fingerprint,
deterministic reductions, all 65 P/Q/whole-window ledgers, and all 260 product
pages.  It converts 1,064,960 4-byte P writes into 358,114 masked 64-byte
writes.  This is 66.3730% fewer write transactions, but 22,919,296 transport
bytes for 4,259,840 semantic bytes; it is not a byte-volume reduction.

## What is now bounded

Commit `9d33ad1d` replaces the virtual retirement `std::set` plus
`std::map<vector<...>>` bookkeeping with one fixed-capacity scoreboard.  Each
configured entry retains a physical write key, generation, backing line, word
mask, and at most two `(page, word-count)` records until the matching
WriteResp.  The accepted strict configuration has 32 entries.  A 64-entry
compile-time ceiling preserves existing 64-credit experiment configurations;
the modeled charge uses the configured capacity, not that ceiling.

The packed metadata floor is 36 B/entry, or 1,152 B per indirect unit at 32
credits.  This makes transaction ownership finite and explicitly charged, but
does not make the complete design synthesized or hardware-timed.  Tag lookup,
mask generation, staging muxes, port pressure, byte-enabled coherence, and
cycle time remain implementation obligations.

Focused validation completed before the workload replay:

- optimized and ASan/UBSan scoreboard tests;
- duplicate insert, full-capacity, missing/stale take, invalid mask, and busy
  reset rejection;
- 24 storage/configuration contract tests;
- gem5 style and diff checks; and
- a clean 16-way gem5 build, binary SHA-256
  `4c07d55ffb8528483f1b7cfe629301b23ac23c4c4679a15bfc7b1972c54f2ccd`.

The same-checkpoint NA1024 replay with this binary is running at
`/data1/nier/dx100-runs/2026-08-27-lead-fixed-scoreboard-na1024-r1`.  Promotion
requires exact output and reduction equality, 65 complete windows, 358,114
64-byte P writes, no fatal/fallback path, terminal `m5_exit`, and exactly
2,213,855,573 `simTicks`.  Any timing change means the replacement was not
semantically neutral and must be explained before acceptance.

## Cross-application boundary

The production-source audit rejects opcode-name generalization:

| Family | Decision | Reason |
|---|---|---|
| CG page-fed P result | Applicable | A real virtual result backing is retired by logical ordinal; masked line writes replace word writes. |
| NAS IS | Non-applicable | B is dead after Row/Offset admission, but there is no virtual result backing or retirement edge. |
| HashJoin PRO/PRH | Non-applicable | Histogram A is updated directly; there is no CG-like result producer to combine. |
| GAPBS SSSP | Separate mechanism | Index/value/old-result backing is semantically reread; its masked old-result publisher cannot be replaced by the CG combiner. |

The reusable rule is therefore not “all indirect operations.”  A workload must
have all four properties: dead private B after admission, strict full-window
descriptor closure, a real virtual result backing indexed by logical ordinal,
and legal masked retirement to that backing.

## Replacement-policy decision

Exact replay of all 1,064,960 CG insertions reproduces 358,114 round-robin
writes.  LRU and tree-PLRU are identical.  The already implemented
most-filled policy predicts 349,673 writes (2.357% fewer), while a
set-constrained offline optimum predicts 313,895.  Even the offline result is
only about 0.49% of current runtime under a deliberately non-promotional
linear estimate.  Tree-PLRU, set hashing, and new lookahead hardware are
rejected; only the zero-new-state most-filled policy merits a live test.

## Remaining promotion gates

1. Accept or reject the fixed-scoreboard NA1024 replay against the exact
   frozen result.
2. Accept or reject the existing most-filled live test on measured
   `simTicks`; transaction count alone is insufficient.
3. Accept or reject the separately authorized full-CG candidate with exact
   official output and complete mechanism ledgers.
4. Keep IS/HashJoin out of this optimization and keep SSSP on its distinct
   old-result path unless a new producer/consumer proof changes the matrix.
5. Before hardware or iso-area claims, add transaction identity for delayed or
   duplicate ACKs, directed coherence tests, calibrated lookup/mask/port
   timing, and synthesis-based area/Fmax evidence.

Primary evidence remains in
`strict_two_phase_cg_reference_2026-08-27.md`,
`strict_linecombined_crossapp_2026-08-27.md`,
`lead_combiner_reuse_2026-08-27.md`, and
`../reviews/2026-08-27_strict_linecombine_hardware_review.md`.
