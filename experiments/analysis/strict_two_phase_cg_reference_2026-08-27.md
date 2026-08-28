# Strict two-phase CG reference handoff (2026-08-27)

## Decision

**Accept the default-off strict mechanism and its bounded non-fused CG
ordering evidence. Do not claim a native4 win or full-CG promotion.**

The production selector is the non-fused page-fed path: p16 virtual gather,
four response-bearing coherent product pages, then q16 page-fed SoA/JIT RMW.
It is not the fused treatment-matched diagnostic and it is not direct4. The
strict packet fence actively rejects A issue until the q descriptor generation
is closed; the terminal invariant independently requires
`A_FIRST_ISSUE >= ROW_OFFSET_LAST_INSERT`.

At `CG_NA=1024`, the accepted strict/control pair ties exactly at
`2,386,167,394 simTicks`. A same-checkpoint strict arm that additionally
combines P retirement into cache-line writes takes `2,213,855,573 simTicks`,
or 7.2213% fewer ticks (1.077833x). Both results preserve exact output and
deterministic reductions. No native, direct4, or full arm was launched in this
session.

This exact reference has **not** been shown to beat equal-work native4. The
preserved native4 number (`77,075,327,902 simTicks`) is a historical full-CG
endpoint, not a provenance- and scale-matched `CG_NA=1024` arm. The archived
direct4 result is q16-only and cannot stand in for native4 or the p16 claim.
Consequently, native4 arithmetic is intentionally not computed here.

## Mechanism closure

- `--maa_virtual_strict_two_phase` defaults off.
- Every logical 16K B/index stream is admitted once through the existing 4K
  physical feeder. Raw B retention and replay both remain zero.
- All 16K derived Row/Offset and destination-routing descriptors remain in the
  logical metadata structures. Returned-value/result capacity stays bounded
  by the 4K physical configuration, and backing completion is response-bearing.
- `PageFedSoaJitState::authorizeAIssue` guards the actual q packet issue edge.
  A dedicated negative test asks for A issue before close and requires
  `EarlyExecution`; the direct strict reference has the analogous
  `EarlyAIssue` test.
- Each terminal whole-window record links one p16 generation, four product-page
  responses, one q16 generation, and the CG numerical terminal. Terminal
  processing is idempotently guarded around the exact block containing
  `completeStrictP16Q16Window`.

## Focused lifecycle audit

Every `strict_page_fed_*` timing field is initialized in the class definition,
reset after the current instruction is decoded and before mode dispatch, and
read only under the current instruction's strict p16/q16 predicate. The
terminal latch is reset at decode, checked before whole-window completion, and
set only after completion returns.

The stale-state findings and closures were:

| State | Identity and first use | Terminal/reuse behavior |
|---|---|---|
| q timing fields and terminal latch | Current decoded instruction; explicit zero/false initialization and decode reset | Exact-order checks precede one ledger emission; latch makes repeated terminal polling idempotent |
| `strictTwoPhaseReferences` | `(core, backing token)` plus current generation; same-core live-producer interleaving fails closed | P remains until q completion, then the exact P owner is erased and absence asserted |
| `strictTwoPhasePendingConsumerBegins` | Same strict key; permits consumer notification before the producer record arrives | Folded into the producer record and erased; terminal asserts no residue |
| `strictProductPageResponses` | Product backing plus the unique unconsumed P owner on the same core; four response generations form one lifetime | Reused backing is split into successive four-page lifetimes; the consumed lifetime is erased at q completion |
| `strictP16ByQ16` | Current q key links to exactly one completed P and its four pages | Erased with the P/page records; postconditions reject stale linkage |
| backing range selection | `my_instruction->backingAddrRangeID`, never a unit member left by an unrelated instruction | Copied into the unit only after strict pre-decode validation |

Targeted regressions anchor initialization/reset ordering, current-instruction
identity, exact terminal guard ordering, map erasure, unique producer ownership,
backing reuse, and unrelated-instruction isolation. The optimized and
ASan/UBSan C++ reference tests also exercise both negative early-A fences.
The strict-only r6 restore closes all ten `CG_NA=256` windows and the numerical
terminal after these lifecycle changes.

## Accepted evidence

The accepted bounded roots are under the coordination session evidence tree:

- `cg-strict-nonfused-na256-strict-only-r6`: strict-only lifecycle replay,
  10 complete windows.
- `cg-strict-nonfused-na256-r7-matched`: same-checkpoint strict/control pair,
  both `418,934,850 simTicks`.
- `cg-strict-nonfused-na1024-r8-matched`: same-checkpoint strict/control pair,
  both `2,386,167,394 simTicks`; 65 p, q, and whole-window terminals and 260
  product responses.
- `cg-strict-nonfused-na1024-line-combined-r2`: same r8 checkpoint and guest,
  strict plus `--maa_virtual_masked_writes`; exact result, 65 p, 65 q, 65
  whole-window records, 260 product responses, and no drains or fallbacks.

The NA1024 line-combined result is an attribution arm, not an independently
promotable architecture result. Its result file reports source
`54486dc00898a5367691c3e016aa0683b6c8757b`, gem5 SHA-256
`a78ad432b958b39fe008e496c709a7df4b2cbc4633fda2fad731260b6560148e`,
and guest SHA-256
`20335fcdb7cd89ef7d1ec3a2bc7da88327233bd66cc091b9d23b67af19904349`.

## NA1024 retirement attribution

All 1,064,960 baseline P backing issues are 4-byte writes. All 358,114 P
backing issues in the matched line-combined arm are 64-byte writes. This is
706,846 fewer P write transactions (66.3730%). Including the unchanged 375 q
writes, total strict backing issues fall from 1,065,335 to 358,489
(66.3496%). The larger byte granularity is expected: this comparison prices
transaction combination, not payload-byte reduction.

The compact strict counters are overlapping phase durations and therefore
must not be added to reconstruct `simTicks`:

| Counter | Strict 4-byte P | Strict 64-byte P | Delta | Change |
|---|---:|---:|---:|---:|
| B_FETCH | 4,199,686 | 4,191,669 | -8,017 | -0.1909% |
| ROW_OFFSET | 4,307,212 | 4,298,831 | -8,381 | -0.1946% |
| A_ISSUE | 1,559,688 | 1,041,427 | -518,261 | -33.2285% |
| BACKING | 2,409,434 | 1,868,960 | -540,474 | -22.4316% |
| PAGE | 682,281 | 673,403 | -8,878 | -1.3012% |
| CONSUMER | 2,333,426 | 2,333,426 | 0 | 0% |
| `simTicks` | 2,386,167,394 | 2,213,855,573 | -172,311,821 | -7.2213% |

The strict barrier itself costs no measurable end-to-end time in either
matched size: strict/control is 1.0 at NA256 and NA1024. The large backing
transaction reduction, lower BACKING and A_ISSUE counters, unchanged consumer
time, and 7.22% end-to-end gain make word-granular coherent P retirement the
primary measured bottleneck candidate in this bounded reference.

## Validation and limits

Focused Python contracts, optimized and sanitized C++ negative tests, source
style hooks, and a clean incremental gem5 build pass. The final source build
hash above is the binary used by all accepted strict evidence. Failed r1-r5
roots are retained as fail-closed lifecycle diagnostics and are not performance
evidence.

Promotion beyond bounded ordering evidence still requires a separately
authorized full-CG run and a provenance-matched equal-work native4 arm if a
native4 performance claim is desired. The line-combined result supports the
retirement attribution only; it does not turn the reference into a native4
comparison.
