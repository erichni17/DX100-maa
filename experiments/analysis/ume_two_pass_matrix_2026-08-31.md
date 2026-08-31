# UME strict two-pass applicability and bounded smoke contract (2026-08-31)

## Pre-execution decision

GZZ is the only UME path selected for a fresh strict two-pass smoke. This is a
source-level applicability decision, not yet an accepted simulation result.
The production GZZ gradient phase issues an unpredicated
`INDIR_LD_VIRTUAL_INDEX` over `c_to_p_map`, writes FP32 results by logical
ordinal to a 64-byte-aligned coherent backing array, and then consumes that
backing in four 4K pages before the existing zone-gradient RMW. The strict
mode therefore targets the same generic direct-result producer used by the
API/XRAGE path; it does not fuse GZZ arithmetic or replace its RMW.

GZP is not selected. Although its source retains a direct virtual gather, the
currently selected production treatment continues through response-bearing
page publication and masked SoA/JIT RMW for two destinations. That
published-source/RMW path is distinct and cannot be used as positive evidence
for direct virtual-result backing.

## Small fresh matrix

The runner uses deterministic fixed input `n=16384`, exactly one complete
logical GZZ gather window, and four fresh checkpoint/restores:

| arm | logical / physical | guest path | treatment |
|---|---:|---|---|
| native16 | 16K / 16K | native GZZ T16K | one native 16K window |
| native4 | 4K / 4K | native GZZ T4K | four equal-work 4K windows |
| original hybrid | 16K / 4K | shared hybrid GZZ T16K | original `stream_control` schedule, strict off |
| strict bounded hybrid | 16K / 4K | same hybrid guest | `token_stream_ld`, strict and complete-line modes on |

All arms use simulator SHA-256
`aa5c70b140b6fb66bfb9f4a28b34f009f025cf639eb288c01dbb91b0d2f609bb`
from source commit `19b648687c3ca16411b5942d0760c4c07a5e17de` and the
same frozen Ramulator library. The original and strict hybrids share one guest
binary hash. Response plus combiner payload is exactly 4,096 FP32 words in
both hybrid arms; this normalizes the old stream-control schedule to the
physical result bound instead of carrying forward the historical 4,576-word
configuration.

## Fail-closed acceptance

Every arm must terminate through one `m5_exit`, produce output hash
`7602200327591349891`, report zero nonfinite values, and pass the scalar
reference for all 196,384 zone entries. Native instruction counts must match
the predicted equal work; both hybrid arms must match each other for direct B
words and GZZ instruction work.

The candidate is accepted only if one fresh trace contains all of the
following:

- exactly one `strict_two_phase_timing schema=2` terminal record;
- 16,384 B words and 16,384 Row/Offset descriptors admitted exactly once;
- admission closure with `raw_b_buffered_words=0` and `a_issues=0`;
- `A_FIRST_ISSUE >= ROW_OFFSET_LAST_INSERT`;
- four ready consumer pages and exact A-response closure;
- 65,536 semantic backing bytes with exact issue/ACK closure;
- 1,024 complete FP32 backing-line writes, zero partial writes; and
- 3,072 combiner plus 1,024 response words, with exact finite payload-port
  word closure.

If the trace is absent, the opcode resolves to masked/published RMW, any
reference/work/ACK ledger is open, or storage exceeds the bound, the runner
returns nonzero and this report becomes a precise non-applicability/rejection
record. No full application run is authorized by this contract.

## Execution record

Pending the committed contract. Raw evidence will be written outside Git and
this section will be replaced with the accepted one-window measurements or the
exact rejection reason.
