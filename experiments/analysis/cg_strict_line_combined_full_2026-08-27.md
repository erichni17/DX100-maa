# Strict line-combined full-CG candidate (2026-08-27)

## Prelaunch decision boundary

This gate authorizes exactly one candidate restore at `CG_NA=150000` for the
non-fused `page_fed_product_soa_jit` path: p16, four response-bearing coherent
product pages, then q16. The sole restore enables
`--maa_virtual_strict_two_phase`, `--maa_virtual_masked_writes`, retained
SoA/JIT value lines, and four apply lanes. Native, direct4, fused, control, and
additional candidate runs are forbidden. No performance conclusion may be
drawn before the candidate reaches a zero wrapper exit, one m5 exit, one ROI
close, nonempty final stats, and every numerical/mechanism gate.

Runner:
`experiments/scripts/run_cg_strict_line_combined_full.py`.

Planned raw root:
`/data1/nier/dx100-runs/2026-08-27-cg-strict-line-combined-full-r1`.

Planned durable dispatcher:
`cg-strict-line-combined-full-r1`.

## Frozen authorities

- Tolerant full-CG numerical certificate:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-full-reclassification-r1`;
  verdict `PASS_NUMERICAL_MECHANISM_CORRECT`, with exact pinned hashes and the
  declared scalar relative bounds.
- Exact lane-4 selection certificate:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-apply-lanes-na1024-r1`.
- Exact strict line-combined selection root:
  `cg-strict-nonfused-na1024-line-combined-r2` in coordination session
  `strict-two-phase-cg-reference-20260827-20260827-182028-096a7ac2`.
  Its pinned gem5 SHA-256 is
  `a78ad432b958b39fe008e496c709a7df4b2cbc4633fda2fad731260b6560148e`;
  its exact result and trace prove strict non-fused p16/q16 ordering and
  358,114 64-byte P backing writes at `CG_NA=1024`.
- Frozen full input header: 992,830,458 bytes, SHA-256
  `f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131`.

## Checkpoint reuse gate

The accepted prior full lane-4 checkpoint is treatment-neutral and its
before/after 13-entry checkpoint ledgers agree. Reuse is nevertheless rejected
for two independent compatibility failures:

- frozen checkpoint gem5 SHA-256 `606eb920...` differs from required strict
  gem5 SHA-256 `a78ad432...`; and
- frozen guest page-fed ABI SHA-256 `e20a64b3...` differs from the current
  strict packet-fence ABI SHA-256 `5d21cbb9...`.

Only the frozen full input header may be copied after its hash and size pass.
The runner compiles one fresh guest and creates exactly one new deferred,
treatment-neutral checkpoint before its sole candidate restore.

## Fail-closed terminal gate

Acceptance requires:

- exactly 10,960 p timing records, 10,960 q timing records, and 10,960 linked
  whole-window records;
- exactly 43,840 ordered product-page responses, grouped 0/1/2/3 with unique
  response generations and consumed once by their non-fused p/q window;
- `A_FIRST_ISSUE >= ROW_OFFSET_LAST_INSERT` independently for every p and q
  record, exact p/q generation ownership, and zero direct4, drains, fallbacks,
  replay, raw-B retention, or descriptor backing;
- every P backing-write event is exactly 64 bytes, its count equals the sum of
  p timing `backing_issues`, and the count is below word-granular retirement;
- exact full p/product/q/page-fed work, retained-line delivery identity, four
  active apply lanes, zero value/lookahead/context stalls, and zero fused
  counters;
- exact mechanism counts plus the accepted tolerant full-CG numerical bounds;
  and
- immutable before/after checkpoint, artifact, source-status, and source-commit
  ledgers, followed by a sealed result/gate and nonempty terminal stats.

The strict trace is validated as a stream so full-run evidence is never loaded
wholesale into memory. The durable runner atomically publishes progress and a
terminal callback record. A rejected run emits no performance arithmetic.

## Handoff: terminal decision (2026-08-28)

**ACCEPT the one candidate-only full-CG observation through its read-only
successor certificate.** This is a correctness/mechanism observation, not a
native, direct4, or performance-comparison claim.

Raw root:
`/data1/nier/dx100-runs/2026-08-27-cg-strict-line-combined-full-r1`.

Successor certificate root:
`/data1/nier/dx100-runs/2026-08-28-cg-strict-line-combined-full-certificate-r1`.

The raw runner created one new treatment-neutral checkpoint and launched one
candidate restore. The checkpoint and restore wrappers exited zero; the guest
reached one ROI close, one passing full-CG fingerprint, one passing page-fed
terminal, and one m5 exit; final stats and config are nonempty. It launched no
native, direct4, fused, control, or second candidate.

The raw wrapper later exited one in its post-run trace gate because the first
runner revision keyed p generations globally. Full CG legally reuses
`generation=1` under distinct token/core lifetimes; the trace shows tokens 22,
6, 14, and 30 under cores 2, 0, 1, and 3. The committed successor corrects the
p key to `(token, generation)` and the q key to `(unit, generation)`. It
launches zero gem5 processes, leaves the raw root unchanged, reconstructs the
run-time source files from commit
`b3ce3d2a04866cf946b4c990ad330d1f76ac9cbe`, and streams/hashes the immutable
raw trace once.

## Accepted closure

The successor reports `PASS_NUMERICAL_MECHANISM_CORRECT` with:

- exactly 10,960 p timing, 10,960 q timing, and 10,960 linked whole-window
  records;
- exactly 43,840 response-bearing product pages;
- 147,554,350 P backing-write events, exactly equal to summed p timing
  `backing_issues`, with every write exactly 64 bytes;
- 21,920 strict operations, 87,680 strict pages ready, and 147,611,841 total
  strict backing issues, equal to P writes plus 57,491 q backing issues;
- 10,960 SoA/JIT instructions and terminal completions, 43,840 active apply
  lanes, and apply-lane high water 43,526;
- zero offset-table drains, SoA/JIT epoch drains, bounded-global-merge
  fallbacks, value stalls, lookahead stalls, context stalls, and fused-P16
  counters;
- exact per-record p/q ordering, non-fused ownership, response-bearing page
  consumption, retained-line closure, and zero direct4; and
- a 250,750,286,313-byte trace with 1,543,177,333 lines and SHA-256
  `3b7a299facd454e2a65da3cbc4efe26c2c77825d33a71a89bcf225265fa652ca`.

All six full-CG numerical deltas pass the frozen tolerant authority. The
largest reported relative delta is `rnorm=0.00022465720418710547`, below its
declared `1e-3` bound. The terminal first-ROI observation is
`160,746,544,242 simTicks`. No baseline ratio or speedup is computed because
the accepted prior full checkpoint/guest used a different gem5 hash and guest
ABI, and no native or direct4 arm was run here.

## Seal

The read-only successor seal is:

- manifest SHA-256:
  `b8a443dc6d780b57d88e145fc76d29c8b1587e1e226f733ef3dba9f4793f5e12`;
- certificate SHA-256:
  `ecb1d411d65d5b5ac9ab9c0e66b98c9c23ae3ec61e612e7b3f4c24c557948e5d`;
- input-ledger SHA-256:
  `dc5ef292ae5550b717cec0afea073f63c5c15ae43ff56c6f39a98a11fcc4074d`;
  and
- gate SHA-256:
  `572389df42caaaa1cd3f91252612291a3f0ff158ccab5e00f1655ef5fc2613b7`.

Explicit `--validate-seal` passes, and the raw root remains unchanged. This
handoff accepts one strict line-combined full-CG candidate observation only;
it does not claim official NAS verification, native speedup, direct4 speedup,
iso-area advantage, variability, or synthesis cost.
