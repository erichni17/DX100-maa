# GZP live publisher integration handoff — 2026-08-14

## Implemented slice

Starting from lead commit `6fd9c3ef`, the production `soa_jit` GZP path now
publishes each completed 4K predicate page and FP32 product page into its
registered per-core logical-16K arrays with the response-bearing SPD publisher.
The old CPU tile-pointer loop, conditional host copy, poison fill, and host
fence are gone.

For every full 16K window, the guest submits eight bounded publications (four
predicate and four gradient pages), waits for both page completion tokens, and
then submits the existing full-window SoA/JIT RMWs.  A completion token becomes
ready only after all 256 unique `WriteResp`s for that page have returned.  The
source tile and exact copied line payload remain owned through retry/response,
and the existing publisher permits at most eight outstanding lines.  No
operation-sized payload was added.

The existing SoA/JIT consumer is unchanged: it retains the full 16K RowTable
and OffsetTable epoch while physical FP-capable SPD storage remains 4K.  Its
predicate, stable value snapshots, duplicate-index insertion order, and
disjoint backing/A range checks continue to enforce exact selection, FP32, and
alias behavior.  The 576-element tail remains on the existing 4K path.

## Mechanism evidence

The existing per-stream publisher counters cover issue, transport accept,
retry, authenticated `WriteResp`, eight-credit high-water, credit stalls, and
terminal completion.  This integration adds `STR_PublishOverlapIssues`, which
counts publisher line issues while the same MAA's ALU, range, or indirect unit
is active; the matching trace issue records carry `overlap=0|1`.

The uncapped runner
`experiments/scripts/run_gzp_live_publisher_correctness.sh` uses no timeout
command.  Its single-active-owner exact gate runs four consecutive full
windows over 65,536 corners so the immutable value and index vectors follow
the production mmap allocation regime; the smaller 16,384-corner malloc
layout fails the unchanged SoA/JIT contiguous physical-routing guard before
consumption.  The gate requires:

- the scalar reference and GZP terminal markers to pass with zero errors;
- exactly 32 publications and 8,192 issue/accept/response events;
- exactly eight credits at high-water with a nonzero credit-stall count;
- the configured logical 16K / physical 4K geometry, including 32 RowTable
  slices (16,384 bounded entries); and
- a clean source worktree so its manifest commit identifies the tested code.

The full-corpus analyzer also fails closed unless the 61 full windows close
488 publications and 124,928 issue/accept/response events at an eight-credit
high-water.

Four active owners are not yet safe.  A 65,536-corner diagnostic run cleared
the span guard and overlapped a live SoA/JIT RMW with another owner's
publication, but gem5 aborted in `SnoopFilter::lookupSnoop` at its invariant
for coherent snoop packets.  At that boundary the trace had 1,976 publisher
issues, 1,975 accepts, 1,968 responses, and seven complete page terminals;
the failure was not a publisher terminal or identity check.  Fixing that
shared coherent-crossbar interaction would require work outside this slice
and outside the files authorized for this task.  The committed exact runner
therefore activates one OpenMP owner and does not claim multi-owner safety.

## Validation and evidence boundary

The guest compiles with `-Wall -Wextra -Werror`, the response-bearing
publisher unit test passes for FP32 and FP64, the focused Python contracts and
syntax checks pass, and `build/X86/gem5.opt` builds successfully.  The exact
one-window gem5 smoke must run from the clean coordination checkpoint; its
artifact path and measured retry/stall/overlap counts will be appended here.

This is correctness evidence only.  `performance_promotable=0` and
`speedup_claim=0` remain explicit.  No speedup may be claimed without a matched
same-binary run.
