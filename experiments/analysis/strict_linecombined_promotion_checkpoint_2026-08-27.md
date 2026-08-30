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

The fixed direct-index storage ledger charges 26,276 additional bounded bytes
across four units at 64 lines versus one line; this remains a lower bound, not
a synthesis result. Eight lines is the first cost knee, 64 is the selected
speed point and is now confirmed by a full-CG pair, and 128 is rejected as a
cost-driven sweep stop rather than a measured NA1024 optimum. Full evidence
and scope are in
`strict_feeder_sweep_2026-08-28.md`.

The dynamic host maps are no longer part of the selected feeder. A fixed
128-slot line store implements configured depths 1 through 128, carries exact
physical response tags and logical-word ownership, and limits request
generation to one line per cycle by default. Widths one, two, and four are
tick-identical at the accepted NA256 point; an independent replay with the
integrated binary also reproduces 246,463,712 ticks and exact correctness.
This closes the zero-cycle host-container concern for the measured micro, but
does not substitute for CAM/mux timing or synthesis. See
`fixed_direct_index_feeder_2026-08-28.md`.

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

### Current cost/performance point

| Design | Equal-work micro `simTicks` | Storage interpretation |
|---|---:|---|
| Native16, feeder64 | 48,487,143 | 3,176,448-B native comparable lower bound |
| Hybrid logical16/physical4, feeder64 | 57,330,645 | 1,596,712-B comparable lower bound |
| Native4x4, feeder64 | 77,011,459 | timing comparator only; shared logical16 aperture is not a true native4 cost point |

Relative to native16, the hybrid trades 18.239% performance for a 49.733%
comparable-storage reduction. Relative to native4x4, it improves performance
25.556%. These are deterministic API-micro observations, not synthesized area
or suite-wide averages. The performance observations predate the fixed-feeder
source replacement; the revised storage number uses its packed semantic
ledger, while the integrated NA256 replay establishes timing equivalence only
for that smaller gate.

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

## Complete-line successor (2026-08-29)

The matched bottleneck audit found that the 16-line hybrid's first partial
write fetches every old backing line.  An experimental unmasked-placeholder
shortcut removed those misses and measured a 16.89% micro gain, but an
independent review correctly rejected it because zero placeholders could
become visible in coherent memory.

The legal replacement retains fragments privately and publishes only complete
lines.  The final source adds a fail-closed `virtual_complete_line_only` mode:
explicit response/combiner pools must fit the physical word bound, partial
victim or final drain panics, and terminal partial writes must be zero.

Final equal-work micro point:

| Arm | `simTicks` |
|---|---:|
| Native16, feeder64 | 48,491,838 |
| Native4x4, feeder64 | 77,068,112 |
| 16-line hybrid control | 56,868,031 |
| Complete-line hybrid, 512 tags x 16 ways / 1,600 words | 47,241,090 |

The safe hybrid is 16.929% faster than the matched hybrid control, 2.579%
faster than native16, and exact.  It emits 2,048 full lines and zero partial
lines, remains within 1,664 result words, and retains a 65.683% comparable
storage reduction in the one-unit micro ledger.  Without page overlap, the
hybrid remains slower than native16; the crossing is attributable to both
complete-line retirement and measured page-level overlap.

The same mechanism produces the first positive real-application successor on
XRAGE gather0 64K.  A selected 1,536-tag x 16-way / 2,560-word combiner plus
1,024 response words takes 37,268,284 ticks versus 42,312,279 for same-binary
native16 and 56,159,086 for the bounded hybrid control.  It closes 8,192 full
producer lines, zero partial lines, exact direct-consumer work, and exact
output while retaining a 63.211% comparable-storage reduction.  See
`hybrid_safe_combiner_results_2026-08-29.md` and
`xrage_complete_line_hybrid_results_2026-08-29.md`.

All 14 recovered LANL FLAG gathers now close exactly at the fixed 2,048-tag,
3,072-word combiner plus 1,024 response words.  Same-binary geometric means
are 7.476% lower latency than fused16, effectively tied with compact16, and
33.478% lower than a small bounded direct4 control.  The same-capacity
guard-disabled arm is tick-identical in every case.  See
`flag_complete_line_results_2026-08-29.md`.

A same-binary successor halves combiner associativity from 16 to 8 using XOR
shift 7. It is timing-equivalent across all 14 FLAG gathers (-0.004%
geometric-mean latency), while the final selected arm remains 7.463% below
fused16 and tied with compact16 (-0.026%). See
`flag_xor8_results_2026-08-29.md`.

The selected XOR8 organization also closes a three-cycle pipelined lookup on
all 14 FLAG gathers at +0.155% geometric-mean latency and on XRAGE at +0.134%.
The pipeline is metadata-only, shares the existing response payload, and
limits starts/completions to four each per MAA cycle. See
`flag_lookup_latency_results_2026-08-30.md` and
`xrage_lookup_latency_results_2026-08-29.md`.

XRAGE also closes the existing fixed 16-page ready queue: all 8,192 full lines
are selected without a 1,536-slot scan, at 37,291,759 ticks and exact output.
See `xrage_page_ready_drain_results_2026-08-30.md`.

The same queue closes all 14 FLAG gathers with -0.0002% geometric-mean timing
change and exact full-line/tail work. The concise current mechanism is in
`selected_complete_line_hybrid_2026-08-30.md`.

CG NA256 is tick-identical under the related dense treatment, so no full CG
run is justified.  Remaining gates are current finite drain-width results,
finite tag/reference/payload lookup timing and ports, delayed/competing
coherence stress, and synthesis/calibrated area-energy-Fmax evidence.
