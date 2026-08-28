# Strict CG feeder-depth optimization (2026-08-28)

## Decision

**Accept 64 direct-index cache lines as the CG performance point; retain eight
lines as the first cost/performance knee. Reject 128 lines.**

This is a bounded optimization of the strict 16K-logical/4K-physical CG path.
It does not change the 16K Row/Offset reorder window, result combiner, write
credits, request width, or application work. It lets up to 64 sequential
`B[i]` cache lines be outstanding/retained instead of serializing behind one
line. Every arm uses gem5 SHA-256
`4c07d55ffb8528483f1b7cfe629301b23ac23c4c4679a15bfc7b1972c54f2ccd`
and the same frozen checkpoint/guest at its problem size.

At `CG_NA=1024`, the selected 64-line plus masked-line design takes
**1,249,282,534 `simTicks`**. That is 43.5698% lower latency than the exact
one-line masked arm (2,213,855,573) and 47.6448% lower than the exact one-line
word-retirement strict control (2,386,167,394). It preserves the exact CG
fingerprint, all 11 reductions, 65 P/Q/whole windows, 260 product pages, and
358,114 issued/completed P line writes. No native baseline was run or inferred.

## Capacity sweep

All NA256 arms preserve 163,840 B words/descriptors, 168 A issues/responses,
26,672 backing issues/ACKs, and 655,360 semantic backing bytes. The A,
backing, and consumer phase times are unchanged; only B fetch and overlapping
Row/Offset insertion shrink.

| Feeder lines | Payload/unit | NA256 `simTicks` | Lower vs. 1 | NA1024 `simTicks` | Lower vs. 1 |
|---:|---:|---:|---:|---:|---:|
| 1 | 64 B | 395,548,742 | baseline | 2,213,855,573 | baseline |
| 2 | 128 B | 308,636,780 | 21.9725% | not run | - |
| 4 | 256 B | 267,179,304 | 32.4535% | 1,387,035,399 | 37.3475% |
| 8 | 512 B | 251,554,970 | 36.4035% | 1,289,047,306 | 41.7736% |
| 16 | 1 KiB | 251,025,687 | 36.5374% | 1,282,365,382 | 42.0755% |
| 32 | 2 KiB | 250,163,059 | 36.7554% | 1,267,190,829 | 42.7609% |
| 64 | 4 KiB | 246,463,712 | 37.6907% | **1,249,282,534** | **43.5698%** |
| 128 | 8 KiB | 246,214,877 | 37.7536% | not run | - |

Eight lines is the first knee. Sixty-four improves NA1024 another 3.0848%
over eight and is the selected speed point. The NA256 128-line arm improves
only 0.1010% over 64 while doubling payload, so 128 is rejected without an
NA1024 run.

## Independent attribution

The same-binary NA1024 factorial separates feeder overlap from retirement
combining:

| Feeder | P retirement | P writes | `simTicks` | Change from matching control |
|---:|---|---:|---:|---:|
| 1 line | 4-byte word | 1,064,960 | 2,386,167,394 | strict control |
| 1 line | masked 64-byte line | 358,114 | 2,213,855,573 | 7.2213% lower than 1-line word |
| 64 lines | 4-byte word | 1,064,960 | 1,417,918,170 | 40.5776% lower than 1-line word |
| 64 lines | masked 64-byte line | 358,114 | **1,249,282,534** | 11.8932% lower than 64-line word |

Thus feeder depth is the larger optimization, and line combining remains a
separate measured gain after feeder optimization. Combined, they are 1.9100x
faster than the strict one-line word control at this size. This is still a
strict-CG attribution result, not a native4 or full-application speedup.

## Why it works

With one feeder line, NA256 B fetch totals 170,465,121 ticks. It falls to
84,542,865/43,103,543/26,981,226 ticks at 2/4/8 lines and 21,364,441 at
64 lines. Row/Offset timing tracks B fetch because descriptors are inserted as
those sequential lines return. The selected strict barrier still waits for all
16K descriptors before issuing any A request, so global 16K A reordering is
unchanged.

This does **not** model four or 64 words arriving magically in one cycle. Each
line remains a normal 64-byte cache request using existing cache/memory ports.
The added state supplies independent line credits and retention so memory
latency can overlap.

## Hardware cost and limits

The semantic B payload is `lines * 64 B` per indirect unit. With four
configured units, 64 lines require 16 KiB total, 16,128 B more than the
one-line point and 14 KiB more than the eight-line knee. The current
`report_maa_storage.py --mechanism direct-index` lower bound also charges line
tag/control state:

| Lines | Feeder payload | Physical SPD + active virtual payload/control |
|---:|---:|---:|
| 1 | 256 B | 544,260 B |
| 8 | 2,048 B | 546,392 B |
| 64 | 16,384 B | 563,444 B |

The full one-to-64 bounded-state delta is therefore 19,184 B: 16,128 B
payload plus a 3,056-B control lower bound. It is 1.2197% of the 1.5 MiB
visible SPD payload saved by shrinking 32 tile IDs from 16K to 4K 32-bit
words. The selected bounded payload/control total remains 73.1329% below the
native SPD payload alone.

This is payload accounting, not synthesis. gem5 currently represents each
live word with a 32-byte `DirectIndexWord` inside dynamic maps; a real feeder
should instead store 64-byte B lines plus bounded line tag/state and derive
sequential logical positions. Comparator, credit, MSHR, mux, SRAM/periphery,
port, Fmax, and power costs remain unmeasured. Therefore neither 64 lines nor
the complete hybrid is yet an iso-area hardware result.

## Scope and next gate

The cross-application audit shows that IS and HashJoin do not execute this
virtual-result edge, while SSSP has a different required old-result publisher.
This feeder result is therefore CG evidence, not a claimed suite-wide default.

The next performance gate is one full-CG 64-line candidate against the
same-checkpoint full strict one-line candidate. It must preserve official CG
correctness, exact mechanism work, zero drains/fallbacks, and complete write
ACK closure. A provenance-matched native4 run is still required before making
any native4 comparison.

Raw roots are under `/data1/nier/dx100-runs`; the fixed-scoreboard one-line
masked root is dated August 27 and the new sweep roots August 28. The 80-entry
sealed ledger is `strict_feeder_sweep_artifacts_2026-08-28.sha256`, SHA-256
`374bf24911c37cbeeb06c713f243b8011a3c1a0d6d137b8785e32e356f8cf5cd`.
