# Hybrid post-pre-A exposed-stall budget — 2026-08-14

## Decision

After the accepted pre-A overlap, the largest **exposed** general budget is
still the logical-16K RMW consumer organization; within the existing SoA/JIT
consumer, the next budget is bounded value-owner/context pressure.  The two
highest-ROI mechanisms are therefore:

1. complete a logical-16K paired RMW consumer (two RMWs per 16K window rather
   than two per physical 4K page), with a bounded replay/operand contract; and
2. extend the already measured exact row-directed pre-A value lookahead, then
   tune only its existing owner/context capacity if its falsifiable signature
   shows that pressure remains exposed.

Neither statement is a causal speedup claim.  Item 1 has an analytic ceiling;
item 2 has one exact matched GZP pair.  No sum of instruction-category cycles,
cache latency, DRAM latency, or stall counters is used as a cycle decomposition.

## Evidence and first-window rule

The accepted pre-A pair is
`/data1/nier/dx100-runs/2026-08-14-gzp-pre-a-pair-f2865321-r2` (same binary,
guest, selector, checkpoint, and resolved configuration apart from the knob).
Both arms have exact hash `11225737641199706160`, the 1,180,000-element
reference, 61/61 terminal SoA/JIT completions, and closed request/response
ledgers.  Values below are from the **first** `Begin Simulation Statistics`
window in each `stats.txt`; `extract_hybrid_first_stats.py` makes a missing,
duplicate, nonnumeric, or later-dump substitution fail closed.

| accepted arm | simTicks | MAA total cycles | delta from control |
|---|---:|---:|---:|
| control | 7,293,533,199 | 23,302,023 | — |
| pre-A | 7,115,533,855 | 22,733,335 | -177,999,344 ticks / -568,688 cycles |

`simTicks = cycles_TOTAL * 313` in both windows.  This is the only whole-run
cycle budget: pre-A exposes a measured 568,688-cycle reduction (2.4405%) while
the fixed logical-16K/physical-4K configuration and work counts remain equal
(`INDRD=62`, `INDRMW=307`, `STRRD=737`, selected=949,411, A reads/writes
=509,830, and 61 terminals).

## Reconciled pressure, not additive time

| counter family | control | pre-A | interpretation boundary |
|---|---:|---:|---|
| Context stalls | 3,864,092 | 3,503,414 | -360,678 (-9.33%): aligns with earlier value availability, but is not a disjoint cycle saving. |
| Value/lookahead stalls | 1,925,211 | 3,511,097 | +1,585,886: early issuance exposes owner pressure; it cannot be added to context stalls. |
| value read issues | 881,146 | 875,572 | -5,574 (-0.63%); traffic changed slightly, so speed cannot be assigned to traffic alone. |
| pre-A issue / ready-at-A / use | 0 / 0 / 0 | 933,759 / 488,290 / 933,759 | exact work, 52.29% ready at A response; all early slots are eventually used. |
| A read/write responses | 509,830 / 509,830 | 509,830 / 509,830 | result publication/consumer completion is conserved, not removed. |
| predicate reads/responses | 62,525 / 62,525 | 62,525 / 62,525 | cache-side predicate traffic is unchanged. |

The record contains no accepted paired publisher-overlap performance result.
The publisher handoff is correctness-only; its eight-credit high-water and
credit stalls establish a dependency/ownership constraint, not an exposed
cycle budget.  Likewise cache miss latency and DRAM request latency accumulate
across requests and overlap core, MAA, and each other; they are traffic
diagnostics, not denominators for the 568,688 cycles.

The older `fab420af` live-context volume-only statistics are useful only to
rank mechanism pressure (about 509,830 A read/write pairs, 880,458 value reads,
and millions of context/lookahead stall events).  They use profile-specific
checkpoints and a campaign with a separate failed arm, so they are not merged
numerically with the accepted pre-A pair.

## Remaining ranking and falsifiable predictions

### 1. Logical-16K paired RMW replay — largest structural budget

The accepted prior native16/current-hybrid accounting reports a 4,870,516-cycle
hybrid/native16 gap, of which the overlapping `INDRMW` category differs by
4,028,146 cycles (82.70% pressure alignment).  The hybrid issues 490 RMWs
versus native16's 124 because its two consumers remain page-local.  Replacing
the whole hybrid RMW total with native16's gives only an **optimistic ceiling**
of 6,090,411,905 ticks (still 4.525% behind); it is not a prediction of an
implementation.

Exact locations: `benchmarks/UME/gradzatp.cpp:343-404` creates the 4K consumer
cadence; the required bounded begin/append/seal and two-range completion work
is blocked at `benchmarks/API/MAA_gem5.hpp:646-662`, `src/mem/MAA/IF.cc:350-495`,
`src/mem/MAA/IndirectAccess.cc:3650-3729`, and `:5200-5238`.

Falsifier: an exact same-commit/checkpoint matrix must reduce completed RMWs
from 490 toward 124 while preserving output, final WriteResp closure, and the
existing predicate/A hazard rules.  If RMW count falls but median `simTicks`
does not improve, or bounded operand staging/extra traffic consumes the gain,
this is not ROI and should be rejected.

### 2. Exact pre-A scheduling plus existing owner/context capacity — next bounded budget

The mechanism already produced the 2.4405% same-pair result with no new payload
RAM, owners, ports, or A transactions.  Its residual signature is explicit:
3,503,414 context stalls and 3,511,097 value/lookahead stalls after overlap.
Do not infer that one exceeds the other in wall-clock time; they can describe
the same blocked interval.  First preserve the exact row-directed scheduling;
only then sweep existing owner/context capacity under a fixed resource ledger.

Exact locations: issue the exact chain at
`src/mem/MAA/IndirectAccess.cc:4265-4322`, allow `AwaitARead` eligibility at
`:4329-4458`, retain Active-only apply at `:4473-4580`, and preserve the
authenticated A-response transition at `:4659-4697`.  Owner/waiter residency
is in `src/mem/MAA/SoaJitOverlapState.hh:270-315`.

Falsifier: with all work counters and pre-A issue/use equality preserved,
additional existing capacity is justified only if repeated same-checkpoint
medians improve *and* context/value-pressure counters fall without higher
physical value-read traffic.  A traffic increase, broken `issues == uses`, or
flat ticks despite lower counters rejects the mechanism as noncritical.

## Explicitly not ranked as exposed budget

- More gather-result retention: eliminating all 62,464 backing fallbacks was
  0.0552% slower and leaves the consumer cadence unchanged.
- Wider apply lanes, sequential value prefetch, extra combiner banks/tags, and
  late completed-output retirement: accepted evidence gives regressions,
  discarded speculative traffic, or flat `simTicks`.
- Publisher overlap: correctness and resource ownership are established, but
  no accepted matched performance pair isolates it.

This report intentionally keeps API A/B (3.04–7.42% hybrid gap), the older GZP
RMW ceiling, and the accepted pre-A pair separate: they answer different
questions and cannot be summed or used to claim a composed speedup.
