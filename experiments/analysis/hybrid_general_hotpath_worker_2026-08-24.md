# Shared SoA/JIT context64 audit and rejection (2026-08-24)

## Decision

Reject activating 64 SoA/JIT A-line contexts as the shared default.  The
candidate is exact and improves the small HashJoin-PRO scalar-ADD kernel by
0.1322%, but it regresses the selected dense4/cache64/pre-A small-SSSP MIN
old-result kernel by 1.6964%.  The SSSP treatment also fragments the bounded
old-result publisher: writes rise from 17,805 to 52,747 despite unchanged
semantic work.  The default therefore remains eight contexts.

No native arm or full workload was launched.  No active full-run root was
modified.

## Why this was the one candidate

After dense4 old-result selection, four partial credits, the retained value
cache with 64 active owners, and pre-A lookahead, the selected small SSSP run
still reports 239,267 context-admission stalls.  The accepted small HashJoin
PRO/PRH evidence reports 112,920/112,992 aggregate context stalls, while value
and lookahead stalls are zero.  Earlier exact API and GZP comparisons also
showed context32-to64 gains.  Context capacity was therefore the strongest
remaining benchmark-independent measured pressure signal that did not require
a second speculative mechanism.

The treatment changes only the active context prefix from 8 to 64.  Both arms
keep the same 16K logical Row/Offset reorder window, 4K physical SPD, dense4
old-result controls where applicable, value-cache enable, 64 active value
owners, pre-A lookahead, one apply lane, address ranges, coherent request
paths, and terminal/fallback rules.

## Exact small A/B

Raw root:
`/data1/nier/worktrees/codex-coordination/sessions/hybrid-shared-hotpath-20260824-063057-b94e79a6/evidence/context64-small-ab-r2`

The SSSP pair restores the same frozen checkpoint and runs four full logical
windows over the deterministic 69,633-vertex graph.  The HashJoin pair restores
the same frozen PRO checkpoint and runs eight full scalar-broadcast histogram
windows for 65,536-tuple R/S inputs.  Within each pair, only
`maa_soa_jit_active_contexts` differs.

| Kernel | Contexts | First `simTicks` | Context stalls | A reads/writes | Old-result writes |
| --- | ---: | ---: | ---: | ---: | ---: |
| SSSP FP32 MIN + old result | 8 | 9,976,182,331 | 239,267 | 4,100 / 4,100 | 17,805 |
| SSSP FP32 MIN + old result | 64 | 10,145,419,240 | 792,762 | 4,100 / 4,100 | 52,747 |
| HashJoin PRO scalar ADD | 8 | 6,531,120,600 | 113,660 | 70 / 70 | 0 |
| HashJoin PRO scalar ADD | 64 | 6,522,489,312 | 0 | 70 / 70 | 0 |

SSSP context64 is 0.983318885x as fast as context8, or 1.696410% higher
latency.  HashJoin context64 is 1.001323312x as fast, or 0.132156% lower
latency.  The SSSP regression alone fails the shared promotion gate.  The
increased context-stall event count is a retry observation, not an additive
cycle budget; the conserved A work and 2.9625x old-result write increase give
the relevant mechanism signature.  Wider A-line concurrency spreads captures
over more logical result lines and defeats the dense4 publisher's useful-word
coalescing.

## Correctness and fallback closure

All four restores have gem5 return code zero, exactly one clean `m5_exit`, a
complete first statistics window, the requested 16K/4K resolved geometry, and
no panic/fatal/assert/abort/segmentation/error signature.

- Both SSSP arms reproduce the exact certificate: 69,633 reached vertices,
  distance sum 135,168, maximum distance two, hashes
  `a0531a7ddb9387df` / `39f1ea63bc8817e8`, zero triangle violations, and zero
  missing predecessors.  Each closes 4/4 SoA instructions/terminals, 65,536
  selected aliases and old-result captures, exact A read/write response
  ledgers, exact old-result issue/response ledgers, and pre-A issue/use
  equality.  The guest reports 16K logical reorder words, 4K physical SPD
  words, zero legacy words, zero host SPD reads, and zero hidden result
  payload bytes.
- Both HashJoin arms return the exact cardinality 65,536, route 8/8 eligible
  windows, close 8/8 SoA instructions/terminals and 131,072 selected/applied
  aliases, balance all A read/write responses, and report zero bounded-global-
  merge fallbacks.

The harness reached its post-run validator after every simulator terminated,
then an initial `grep -c` invocation rejected filename-prefixed counts from two
input files.  The raw simulations are complete and immutable; the corrected
validator separates stdout/stderr counts.  A subsequent fail-closed audit of
the completed r2 files passed every condition above without rerunning either
kernel.  The raw file ledger SHA-256 is
`0afd84d696782558466167a44e2a3ead8c4abe1aa69defa574a90dc5fdfd3944`.

## Correct context8-to64 accounting

This candidate is neither fixed-cost nor payload-free.  The active-prefix
increment must be charged from the selected context8 baseline:

| Increment | Per indirect unit | Four units |
| --- | ---: | ---: |
| 56 additional 416-byte context records | 23,296 B | 93,184 B |
| Waiter-mask identities for 56 contexts x 8 slots x 128 owners | 7,168 B | 28,672 B |
| **Total modeled state** | **30,464 B** | **121,856 B** |
| A-line plus lookahead payload within the context records | 7,168 B | 28,672 B |

The 121,856-byte total is 13.6268% of the existing 873.28-KiB hybrid lower
bound.  The payload row is a subset of total modeled state, not an additional
charge.  It comprises 56 extra 64-byte A lines and 56 extra eight-by-eight-byte
lookahead arrays per unit.

The current admission and response routing scan as many as 64 contexts, and
the waiter ownership is not banked.  Neither search is synthesized nor timing-
qualified at 3.2 GHz.  The gem5 result must not be used as evidence that this
capacity meets the target clock.

## Provenance and hashes

- Simulator source: `cf3d9fc00705bd260788dc16b2b1d5a1e41c5d85`
- gem5 SHA-256:
  `1e079112469892681d661925db09ccfbc845d1a2ce45c79e1d9a4902c19a9863`
- SSSP guest SHA-256:
  `b92252492af0fbae8b3a27d2e57d403cbbc2f03b830090ae767f50cac8904c3c`
- SSSP graph SHA-256:
  `3fc71246c10bb765d1f67ac15e9fb30561ca70a89a95f8104f85c91fd2954d23`
- HashJoin guest SHA-256:
  `9137ca242beb2b5a451ca592021047dfdf6da5f35efc53f34844c7d87de9f299`
- Frozen Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`
- SSSP context8/context64 stats SHA-256:
  `74f39aab8b889e781a47e34d8573e186bdf25b321a0830ac41b2b9f921792469` /
  `022f015601dc3ec37a187e73ecd57e7e83d99ce2a1b77d84c1d466d7826a4c9a`
- HashJoin context8/context64 stats SHA-256:
  `8eaa8574d107afebd53b7e8804aa5db619032f7caae1c5ade9e0d45d3a04164a` /
  `613c38916f1e76210fe63d2fbc0c7070c2cb1395c40b682ca35393bc8cf6f5be`

## Handoff

Do not cherry-pick a context64 default or promote this candidate.  The useful
deliverable is the reusable correctness-gated runner, its source contract test,
and this negative result.  Future shared-hot-path work should preserve the
eight-context/dense4 composition and seek a scheduling change that does not
trade A-line concurrency against result-line coalescing.
