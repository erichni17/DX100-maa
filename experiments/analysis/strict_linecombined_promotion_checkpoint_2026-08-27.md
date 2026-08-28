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

The current exact-identity packed metadata floor is 44 B/entry plus one 8-B
allocator, or 1,416 B per indirect unit at 32 credits. This makes transaction
ownership finite and explicitly charged, but does not make the complete design
synthesized or hardware-timed. Tag lookup, mask generation, staging muxes,
port pressure, byte-enabled coherence, and cycle time remain implementation
obligations.

Focused validation completed before the workload replay:

- optimized and ASan/UBSan scoreboard tests;
- duplicate insert, full-capacity, missing/stale take, invalid mask, and busy
  reset rejection;
- 37 storage/configuration/source contract tests;
- gem5 style and diff checks; and
- clean predecessor and exact-identity gem5 builds. The identity-hardened
  binary SHA-256 is
  `f1aeb6d52eadc9888653a083558073087dc745bef0499d7c7d5ccd8a80f8c510`.

The same-checkpoint NA1024 replay with this binary is **accepted** at
`/data1/nier/dx100-runs/2026-08-27-lead-fixed-scoreboard-na1024-r1`.  It closes
with wrapper exit 0 and terminal `m5_exit`; preserves the exact output,
11 deterministic reductions, 65 complete P/Q/whole windows, and 260 product
pages; issues and completes 358,114 64-byte P writes; takes exactly
2,213,855,573 `simTicks`; and reports zero Row/Offset drains, SoA/JIT drains,
or bounded-merge fallbacks.  Replacing dynamic bookkeeping with the fixed
scoreboard is therefore timing- and behavior-neutral for the accepted arm.

The sealed artifact hashes are:

- `result.json`:
  `e30fdcc732f1854c7fe4983b05bba5e46e22f3e4e6eeb00689050916b1cd00e3`;
- `stats.txt`:
  `117ee90fb528967853f2ca2f3194e4f31937db72704da7c34218720f80954547`;
- `restore.log`:
  `ce0e40c756ff540754f3f704bcb34e11ecf32aae871bc9184aa7a8ff0808077f`;
  and
- `strict_trace.log`:
  `77bdcd044c462529d79f3a9b829271beeaa8c77cdb6b48a3e01b8f70cfe95704`.

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

## Feeder-depth successor

The selected CG speed point now retains 64 sequential B cache lines instead
of one while preserving the same full 16K Row/Offset reorder window. At
NA1024, 64-line masked retirement takes 1,249,282,534 ticks, 43.5698% below
the exact one-line masked arm and 47.6448% below the one-line word-retirement
strict control. A same-binary factorial independently attributes 40.5776% to
feeder depth under word retirement and 11.8932% to line combining at 64
lines. Exact output and all work ledgers remain unchanged.

The current direct-index storage ledger charges 19,184 additional bounded
bytes across four units versus one line; this remains a lower bound, not a
synthesis result. Eight lines is the first cost knee, 64 the selected speed
point pending full-CG confirmation, and 128 is rejected as a cost-driven sweep
stop rather than a measured NA1024 optimum. Full evidence and scope are in
`strict_feeder_sweep_2026-08-28.md`.

Exact response ownership is also closed. The selected replay carries
`{address, generation, transaction}` on every retirement request/response,
matches all 358,114 issue/completion identities exactly, and reproduces
1,249,282,534 ticks. The selected 32-credit scoreboard costs a packed
1,416 B/unit and is charged once in generic virtual control. See
`strict_retirement_ack_identity_2026-08-28.md`.

## Replacement-policy decision

Exact replay of all 1,064,960 CG insertions reproduces 358,114 round-robin
writes.  LRU and tree-PLRU are identical.  The already implemented
most-filled policy predicts 349,673 writes (2.357% fewer), while a
set-constrained offline optimum predicts 313,895.  Even the offline result is
only about 0.49% of current runtime under a deliberately non-promotional
linear estimate.  Tree-PLRU, set hashing, and new lookahead hardware are
rejected.

The live gate is now closed. In the complete fixed-16-line NA1024 sweep,
most-filled reduces P writes from 358,114 to 349,595 but regresses
`simTicks` from 2,213,855,573 to 2,226,080,727 (0.5522% slower). The minimum
measured point, 2-way/2-bank/fewest-filled, takes 2,213,832,098 ticks: only
23,475 ticks (0.00106%) below baseline while changing arbitration and
replacement. Retain the 4-way/4-bank round-robin baseline and reject all
fixed-combiner retuning as a selected optimization. The validated matrix is
recorded in `cg_fixed_storage_combiner_sweep_2026-08-27.md`.

## Remaining promotion gates

The one-line full-CG candidate is accepted as one correctness/mechanism
observation at 160,746,544,242 ticks: 10,960 P/Q/whole windows, 43,840 product
pages, 147,554,350 masked P writes, frozen numerical tolerances, and zero
drains/fallbacks. It has no native or feeder-speed comparison. See
`cg_strict_line_combined_full_2026-08-27.md`.

A trace-free same-binary, same-checkpoint full pair accepts feeder64 at
141,810,448,012 ticks versus 160,746,544,242 for feeder1: 11.7801% lower
latency. Exact numerical and semantic-work gates pass. Feeder64 creates 1,022
additional masked P transactions (0.000693%) with exact ACK closure. See
`cg_strict_feeder_full_pair_2026-08-28.md`.

The equal-work API micro confirms the expected ordering at matched one-line
feeder depth: native16 63,325,847 ticks, hybrid1 71,866,678, and native4x4
91,978,180. Hybrid64 takes 57,330,645 ticks, but feeder-matched native controls
are pending before interpreting that point as a virtualization advantage over
native16. Feeder-matched controls close the stronger comparison: native16_f64
48,487,143 ticks, hybrid64 57,330,645, and native4x4_f64 77,011,459. Thus the
hybrid is 25.556% faster than native4x4 but 18.239% slower than native16, as
expected for a cost/performance middle point. Feeder64 is a generally useful
optimization, not a virtualization-only gain. See
`hybrid_feeder_matched_native_controls_2026-08-28.md`.

1. Keep IS/HashJoin out of this optimization and keep SSSP on its distinct
   old-result path unless a new producer/consumer proof changes the matrix.
2. Before hardware or iso-area claims, add competing-agent coherence and retry
   tests, calibrated lookup/mask/port timing, and synthesis-based area/Fmax
   evidence.

Primary evidence remains in
`strict_two_phase_cg_reference_2026-08-27.md`,
`strict_linecombined_crossapp_2026-08-27.md`,
`lead_combiner_reuse_2026-08-27.md`, and
`../reviews/2026-08-27_strict_linecombine_hardware_review.md`.
