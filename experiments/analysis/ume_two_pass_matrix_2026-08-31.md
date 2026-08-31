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
from simulator source commit
`9393ef52e47357d9192050e539e013b6ce64df23`. The experiment runner was based
on repository commit `19b648687c3ca16411b5942d0760c4c07a5e17de`. The raw
rejected manifest used that runner commit as its `simulator_commit` label; the
binary hash and frozen provenance identify `9393ef52...` as the actual
simulator source, and all simulator/runtime files are unchanged between those
commits. The arms use the same frozen Ramulator library. The original and
strict hybrids share one guest
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

**Decision: REJECT the GZZ four-arm matrix; do not launch a full run.** Raw
evidence is preserved at
`/tmp/ume-gzz-two-pass-20260831-39080929`. The terminal rejection record is
`failure.json` SHA-256
`3f755c0fe00d7deec3b7c21694af5c46828268004b7cd243010f9be47077de38`;
`campaign.exit` is `1`, `strict_activation_accepted=false`, and
`full_run_authorized=false`.

The fresh native controls completed and passed the exact oracle:

| arm | `simTicks` | `numInst_INDRD` | `numInst_INDRMW` | result |
|---|---:|---:|---:|---|
| native16 | 52,625,003 | 2 | 2 | exact hash/reference, terminal `m5_exit` |
| equal-work native4 | 34,594,638 | 8 | 8 | exact hash/reference, terminal `m5_exit` |

Both produced hash `7602200327591349891`, zero nonfinite values, and zero
volume/gradient reference errors over 196,384 entries. Native16 `stats.txt`
SHA-256 is
`c523d290a0b3b95932a2305c160de5b99e09c9f5106b213773b0a63fc517326a`;
native4 is
`75ed4cb178c276b8b7e18de64dc209afdff4dfe70f7fdccb42ba1795d55db65c`.
These are fresh functional controls, not a valid performance matrix without
the hybrid arms.

Both hybrid restores failed before ROI at the same production ABI edge:

```text
GZZ virtual consumer selector: virtual consumer selector must contain exactly one mode
Simulated exit code not 0! Exit code is 2
```

The original selector is exactly `stream_control\n` (SHA-256
`a2bf9f95ce5fb2619c5a7f91b30f4a65d4133d4de4e3e3e03cdd787e3a270cfc`),
and the candidate selector is exactly `token_stream_ld\n` (SHA-256
`e0057a11bddb77040674671fbbe847e0f1b0eb4d853abc3c53f11bf6b7bd7d55`).
Thus malformed multi-token content is ruled out. The current
`maa_read_virtual_consumer_mode` ABI collapses inability to open/read the
checkpoint-restored host path and malformed token count into the same error,
so the log cannot distinguish those two conditions further. The wrapper
process returned zero, but the guest exit code was 2; neither hybrid emitted a
terminal `m5_exit`, ROI stats, a virtual trace byte, or any
`strict_two_phase_begin`/terminal record.

The final simulator identity is the required
`aa5c70b140b6fb66bfb9f4a28b34f009f025cf639eb288c01dbb91b0d2f609bb`.
The shared hybrid guest SHA-256 is
`d1706682386fa047463ad6460fd88a34077cddc89c9fc586406f5f368d41d086`.
Therefore the missing evidence is not explained by mixed simulator or hybrid
guest binaries.

Source inspection still shows a potentially applicable direct-result GZZ
edge, but the existing production guest/checkpoint selector ABI cannot
positively activate it in this fresh matched smoke. Repairing selector
transport or adding an inline runtime selector would change the production
guest ABI and require a new matched checkpoint/matrix. That bridge is not
implemented here because the bounded task explicitly required proof from the
existing production guest before proceeding. No speedup, bounded-hybrid
correctness, or UME promotion claim is supported.
