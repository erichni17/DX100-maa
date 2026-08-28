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

## Handoff

Prelaunch implementation is complete; durable execution and the final
accepted/rejected handoff are pending the committed clean-tree gate.
