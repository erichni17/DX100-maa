# GZP shared-index dual-destination SoA/JIT RMW

## Decision scope

This candidate is the 32-context dual-line alternative to the accepted
context64 single-destination design.  It is not directly composable with
context64.  Each context owns exactly two response-bearing 64-byte A/result
lines, so the fixed A/result payload is exactly 32 x 2 x 64 = 4,096 bytes.
There is no second result line hidden in metadata and no logical16
operand/result array.

The implementation remains a functional gem5 timing experiment.  Its shared
value-owner response lookup scans unbanked associative state, and no bounded
banked scheduler or lookup latency has been modeled.  Therefore this work does
not claim 3.2 GHz physical realizability or authorize architecture promotion,
even if the functional one-window timing gate accepts it.

## Mechanism and exactness

One eight-word instruction names primary A, primary values, the uint32 index
stream, a reserved masked/shared-index tag, secondary A, and secondary values.
Decode fails closed unless all spans are registered, typed words are aligned,
the two A bases have identical cache-line word geometry, and every virtual and
translated physical span is disjoint.  The invalid UINT32_MAX index remains
the predicate sentinel.

The fill stage builds the existing 16K Row/Offset structure once from
`c_to_p_map`.  In dual mode, the existing Offset `wid` bits retain the full
uint32 index; no new descriptor array is allocated.  Claiming a primary row
replays that index to identify the congruent secondary A line.  Each Offset
alias reserves one word from each bounded value stream.  Both FP32 additions
are applied before that one Offset entry is consumed.  Thus each destination
observes the original per-index insertion order, including repeated indices,
while the index/reorder build and chain retirement occur exactly once.

Both A reads use ReadExReq and both writes retain explicit WriteResp ownership.
Per-context pending masks identify each destination.  Value-line delivery also
returns its physical address so two streams sharing a waiter cannot be
misrouted.  A partially reserved dual-value slot may drain its first response
without advancing the Offset chain, preventing an odd-owner-capacity deadlock.
The fused path multiplexes the existing MAA memory port; external ports added
are zero.  It reuses the existing strict response-bearing gradient publisher
and does not add a second publisher worker.

## Fixed byte and port accounting

| Item | Provisioning | Bytes |
| --- | ---: | ---: |
| A/result payload | 32 contexts x 2 lines x 64 B | 4,096 |
| Lookahead operand words | 32 x 8 slots x 2 streams x 8 B | 4,096 |
| Value-owner operand lines | 128 owners x 64 B | 8,192 |
| Total auxiliary operand payload | lookahead + value owners | 12,288 |
| Maximum live write packets | 32 contexts x 2 destinations | 64 |
| Transient WriteReq transport payload | 64 x 64 B | 4,096 |
| Existing publisher credit payload | 8 x 64 B | 512 |
| Added external ports | shared existing port | 0 |

The compile-time budget assertion is deliberately named and scoped only to
A/result payload.  Auxiliary operand payload and transient packet copies are
reported separately rather than being mislabeled as metadata.

## Fail-closed gate

The default GZP selector remains `Legacy4K`; `dual_shared_index` is opt-in.
The exact one-window runner compares the existing masked-index volume-only
control against one fused shared-index instruction.  It requires the exact
FP32 fingerprint and reference arrays; one shared index build; two value
streams; paired A/value/WriteResp ledgers; identical index-line reads; exact
publisher WriteResp closure; <=4,096 A/result bytes; zero hidden logical16
payload; and zero added ports.  Acceptance additionally requires fewer
simulated ticks.  A full `n=1000000` run is rejected unless given the accepted
one-window manifest from the same source commit and gem5 binary.

## Validation evidence

- Static fail-closed source contracts: PASS.
- Optimized and ASan/UBSan dual-owner unit: PASS.
- Changed gem5 MAA objects: PASS.
- GZP guest `-Wall -Wextra -Werror` compile: PASS.
- Full `gem5.opt` build: NOT COMPLETED.  The fresh worktree initially had
  empty `argparse`, `spdlog`, and `yaml-cpp` dependency gitlinks, so the first
  attempt stopped at `spdlog/spdlog.h`.  Those exact dependency trees and the
  existing `libramulator.so` were hydrated from the validated sibling GZP
  worktree; compilation then passed `src/mem/ramulator2.cc` and continued into
  unrelated Ruby objects.  The user requested that compilation monitoring
  stop, so the active build was interrupted with exit 130.  The hydrated
  dependency/build files are not candidate source and are not committed.
- Exact one-window paired restore: pending.
- Full GZP: prohibited unless the one-window decision is ACCEPT and mechanism
  closure is true.
