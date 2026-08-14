# GZP hybrid remaining-gap attribution — 2026-08-14

## Decision

The next GZP optimization target should be the two page-local indirect RMWs,
not more gather-payload retention. The measured hybrid/native16 gap is
4,870,516 MAA cycles. The `cycles_INDRMW` delta is 4,028,146 cycles, or
**82.7047%** of that gap. The hybrid still issues 490 RMWs and 981 streams,
versus native16's 124 RMWs and 310 streams, because only the gather producer
has a 16K logical window; its condition/map/RMW/ALU consumers remain 4K.

A best-case replacement of the hybrid RMW-cycle total by the observed native16
RMW-cycle total projects `6,090,411,905` ticks: at most **1.2070x** over the
current hybrid, still **4.5250%** slower than native16. This is a ceiling, not a
performance result: it assumes all 4,028,146 excess RMW cycles are removable
from the critical path and charges no virtual-operand staging or control cost.

The exact 4,096-line masked-retention arm eliminates all 62,464 materializer
backing-read fallbacks but changes ticks by only +0.0552% (slower). It leaves
the 490 RMWs and 981 streams unchanged. Gather-only retention therefore cannot
close the remaining gap.

## Evidence boundary and provenance

The source audit is at `47f4260ccba0cf68b084d47b027d0ec2f0a60310`
(`maa: spread masked retention token partitions`). The frozen matched matrix is:

`/data1/nier/dx100-runs/2026-08-14-general-hybrid-gzp-tailfix-lean-r1152p2304-c512w16-b8-wpc4-901daab8-r1`

- Matrix source commit: `901daab89c0f63348d7f416c2535e145103fce6f`, an
  ancestor of `47f4260c`. The intervening changes add payload-retention and
  analysis machinery; they do not change `benchmarks/UME/gradzatp.cpp`, the
  RMW API, `IF.cc`, or `IndirectAccess.cc`.
- gem5 SHA-256: `16cfe7d364de99955bf65a98dd1a07bae53b11e2ccbe669c125e7a8479410d5b`.
  Guest SHA-256 values: native16
  `5a693f674e6d89814fa83b4bb5baa8987200fdab468ae5299dae9cf30d340cab`,
  native4
  `2458728bedf990767fbd83400b82eaeea2dee29a2af6f68674b6981932880176`,
  hybrid
  `4a7c7aeb3dd1fb20366d2fbcc83425ba54c7cb31d2849e2960bf37081c848d0b`.
- Workload is fixed-input GZP with `n=1,000,000`, four O3 cores, two memory
  channels, four L3 ports, and a 3.2 GHz MAA clock represented by exactly 313
  ticks/cycle. Native16 is 16K logical/16K physical; native4 is 4K/4K; hybrid
  is 16K Row/Offset metadata with a 4K physical SPD. Hybrid knobs are response
  tags 1,152, response words 2,304, combine tags 512, combine payload words
  4,096, 16 ways, eight banks, and four retirement words/cycle.
- All selected arm restore wrappers are zero and logs contain `m5_exit`.
  Each reports output hash `11225737641199706160`, `nonfinite=0`, zero point
  volume/gradient errors, and 1,180,000 checked elements. There is one replica.
  Checkpoints are profile-specific rather than bit-identical.
- The top-level `campaign.exit` remains `1`: the original analyzer rejected a
  legal pre-registered/reused materializer context. The successor analyzer
  report says PASS, but the campaign wrapper was not rerun. This blocks formal
  promotion even though the individual arms are terminal and correct.

The exact-commit retention A/B is:

`/data1/nier/dx100-runs/2026-08-14-general-hybrid-gzp-masked-grayhash-47f4260c-r1`

Its manifest records source `47f4260c`, gem5 SHA-256
`a8617956b3ffd65804931a0bd88b8bc3c5c0dfde0663bc05fabf16d85db5446c`,
and pre-run checkpoint SHA-256
`25a6f0e98ff4a92d54a26530f0e79f49b3d136a6b6487cd2de5a7a8cf703efd7`.
The three commands differ only in retention capacity 0/2,048/4,096. The
campaign and successor report pass. Every arm has `restore.exit=0`, exactly
one `m5_exit`, nonempty ROI statistics, output hash
`11225737641199706160`, `nonfinite=0`, zero point-volume and
point-gradient errors, and 1,180,000 checked elements. The frozen checkpoint
is immutable. These are completed, exact correctness-valid mechanism results.

## Exact GZP cycle and instruction table

These are the first, ROI statistics windows. For all three arms,
`simTicks = cycles_TOTAL * 313` exactly. Instruction-latency categories can
overlap one another; they must not be summed into `cycles_TOTAL`.

| arm | `simTicks` | `cycles_TOTAL` | busy | idle | INDRD cycles / inst | INDRMW cycles / inst | STRRD cycles / inst | ALUS cycles / inst | ALUV cycles / inst |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| native16 | 5,826,750,095 | 18,615,815 | 14,265,052 | 4,350,763 | 2,257,039 / 62 | 11,124,864 / 124 | 13,991,412 / 310 | 3,989,494 / 62 | 3,372,932 / 62 |
| native4 | 7,636,382,131 | 24,397,387 | 20,047,388 | 4,349,999 | 3,592,107 / 245 | 15,258,781 / 490 | 19,704,668 / 1,225 | 3,932,806 / 245 | 5,131,744 / 245 |
| hybrid token materializer | 7,351,221,603 | 23,486,331 | 18,859,547 | 4,626,784 | 3,093,373 / 62 | 15,153,010 / 490 | 17,246,288 / 981 | 4,971,467 / 245 | 2,756,002 / 245 |

Overall, hybrid is 26.1633% slower than native16 and 1.03879x faster than
native4; it recovers 15.7579% of the native4-to-native16 tick opportunity.
Relative to the ordinary hybrid stream-control arm (`7,382,403,915` ticks),
the token materializer saves only 31,182,312 ticks, 0.4224% or 1.00424x.

The instruction counts follow directly from the guest:

```text
ceil(1,000,000 / 16,384) = 62 logical chunks
ceil(1,000,000 /  4,096) = 245 physical pages

native16 RMWs  = 2 * 62  = 124
hybrid RMWs    = 2 * 245 = 490
native16 streams = 5 * 62 = 310
hybrid streams   = 4 * 245 + 1 tail backing load = 981
```

`gradzatp.cpp:331-341` starts one 16K virtual gather producer but explicitly
falls back for the partial tail. Lines 343-404 page the consumer at 4K; lines
352-354 state that the condition, maps, RMWs, and ALUs remain ordinary 4K
operations. The two target sites are `point_volume` at lines 363-365 and
`point_gradient` at lines 400-402. The normalization barrier at lines 465-468
requires all RMW effects to be globally complete first.

## Attribution and two-RMW ceiling

The total gap and RMW pressure alignment are exact:

```text
total gap cycles = 23,486,331 - 18,615,815 = 4,870,516
total gap ticks  = 4,870,516 * 313 = 1,524,471,508

RMW delta cycles = 15,153,010 - 11,124,864 = 4,028,146
RMW fraction     = 4,028,146 / 4,870,516 = 82.7047%

stream delta     = 17,246,288 - 13,991,412 = 3,254,876 cycles
stream alignment = 3,254,876 / 4,870,516 = 66.8282%
```

The fractions exceed 100% when combined because the indirect, stream, and ALU
units overlap. They are pressure alignments, not an additive decomposition.
The exact total-cycle equality nevertheless makes the following explicit
upper-ceiling model useful:

```text
projected cycles = 23,486,331 - 4,028,146 = 19,458,185
projected ticks  = 19,458,185 * 313       = 6,090,411,905
hybrid speedup   = 7,351,221,603 / 6,090,411,905 = 1.2070155x
residual gap     = 6,090,411,905 - 5,826,750,095 = 263,661,810 ticks
residual/native16 = 4.5250%
```

This assumes the two virtual RMWs achieve native16's aggregate RMW latency,
all excess RMW latency is critical, and staging is free. Real hardware must be
slower than this ceiling. It also leaves 671 excess stream instructions; only
a broader logical consumer design can remove those.

## Why zero backing reads barely changes ticks

| retention lines | `simTicks` | delta vs off | `cycles_TOTAL` | hits | misses / backing fallbacks | forwarded | reconstructed | tag conflicts | payload + control |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 7,355,659,004 | baseline | 23,500,508 | 0 | 0 / 62,464 | 0 | 0 | 0 | 0 B |
| 2,048 | 7,353,171,280 | -2,487,724 (-0.0338%) | 23,492,560 | 31,232 | 31,232 / 31,232 | 31,232 | 31,268 | 31,232 | 211,470 B |
| 4,096 | 7,359,720,179 | +4,061,175 (+0.0552%) | 23,513,483 | 62,464 | 0 / 0 | 62,464 | 62,500 | 0 | 420,623 B |

Capacity 4,096 proves the mechanism: one million FP32 words reconstruct
62,500 complete lines; 62,464 lines feed the 61 full materializer lifetimes,
and no coherent backing fallback remains. Yet the application structure is
identical (`INDRMW=490`, `STRRD=981`). A hit still spends the retention RAM
lookup and schedules the same physical-SPD line commit (`MAA.cc:2625-2679,
2730-2796`). Accordingly, stream SPD-write-access cycles rise from 674,876
with retention off to 984,560 at 2,048 entries and 1,583,493 at 4,096 entries.
The removed fetches were overlapped/noncritical; lookup and SPD-fill work
replace them. The non-monotonic tick result is therefore consistent with the
dominant page-local RMW/stream schedule and rejects more gather retention as
the next speed target.

## Legal bounded virtual-RMW design

The smallest credible design is a **paired, sequentially replayed RMW
context**, not an opcode alias:

1. Begin a context containing exact owner/generation, logical count, completion
   token, `point_volume` and `point_gradient` base addresses, and two ADD
   operations.
2. For each 4K page, append the shared index and condition plus the volume
   source before `tile0` is overwritten by the gather; append the gradient
   product after lines 398-402. An append ACK means the operands have been
   copied into bounded hardware, so the physical tiles may be reused.
3. Seal after four pages. Use the existing single 16K Row/Offset organization
   first for volume, drain every RMW write response, reset it, then rebuild and
   drain gradient. Complete one token only after both write ranges are globally
   complete. Handle the 576-element final tail with the existing ordinary path.
4. Before the normalization barrier, wait for the paired completion token.

For FP32 and `N=16,384`, packed operand payload per active context is:

```text
index              = N * 4 B =  65,536 B
volume source      = N * 4 B =  65,536 B
gradient source    = N * 4 B =  65,536 B
condition mask     = N * 1 bit = 2,048 B
total/context                  = 198,656 B = 194 KiB
four contexts                  = 794,624 B = 776 KiB
```

One context is functionally bounded but can block the other three OpenMP
threads while a logical window is assembled. Four contexts are the realistic
minimum for preserving current four-thread admission. This payload lower bound
excludes context tags, page-present/seal bits, two-range hazard records,
transaction IDs, retry latches, ports, arbitration, and SRAM periphery.

Physical-SPD ingress per full window is also bounded and unavoidable unless a
new producer bypass is added:

```text
index + two sources + current 32-bit condition tile
= 16,384 * (4 + 4 + 4 + 4) = 262,144 B = 256 KiB/window
= 15.25 MiB across the 61 full logical windows
```

Sequential descriptor replay reads the packed spool twice because the two base
addresses can map an identical index to different DRAM rows/banks:

```text
2 * (index 64 KiB + one source 64 KiB + mask 2 KiB)
= 260 KiB/window = 15.4883 MiB across 61 full windows.
```

The existing 16K FP32 descriptor lower bound per indirect unit is 243,968 B:
40,960 B Offset Table (`16,384 * (15-bit iteration + 4-bit word + valid)`),
194,560 B Row entries (`16,384 * 95 bits`), and 8,448 B row headers. Sequential
replay reuses it. Building both RMWs concurrently would require at least a
second 243,968 B descriptor set and still needs retained source values; it is
not the bounded low-storage choice.

### Code blockers at `47f4260c`

- `MAA_gem5.hpp:646-662` encodes one base address and three physical source
  tile IDs. There is no begin/append/seal ABI, generation, page ordinal, or
  completion token for a virtual RMW.
- `IF.cc:350-495` treats ordinary RMW as one indirect instruction, rejects a
  completion-only tile as data, applies whole-tile hazards, and owns one
  `addrRangeID`. A paired operation needs exact append read hazards and two
  write-range scoreboard/permit records.
- `IndirectAccess.cc:3650-3729` reads index/source/condition directly from the
  current SPD tiles and selects one Row Table from one base address. It has one
  indirect unit and one live Row/Offset state. It cannot keep two partially
  assembled 16K RMWs or replay staged operands.
- `IndirectAccess.cc:5200-5238` marks an instruction finished and charges RMW
  cycles only after its packets drain. A virtual pair must preserve that rule
  across both internal phases; early append or source-tile release cannot be
  exposed as architectural completion.
- Cross-core duplicate indices require the existing atomic RMW path and
  address-region permits. A direct writeback shortcut is illegal. Context
  admission, abort, retry, stale generation, partial tail, and all write ACKs
  need fail-closed tests before any performance run.

## Blockers and promotion gate

- The primary matrix has one replica, profile-specific checkpoints, source
  `901daab8`, and a stale nonzero top-level campaign exit.
- The exact-`47f4260c` retention campaign is terminal and correctness-valid.
  It promotes the negative result: eliminating all gather backing fallbacks
  does not improve GZP performance.
- The 1.2070x RMW result is an analytic ceiling. No virtual-RMW ABI, bounded
  context store, dual-range hazard model, storage/control ledger, or live gem5
  implementation exists at this commit.
- Promotion requires focused functional and sanitizer tests, exact FP32
  duplicate-index/order tests across four owners, storage/port accounting,
  then a fresh same-commit matched native16/native4/hybrid matrix with terminal
  correctness and repetitions. The decisive mechanism signature is RMW count
  490 -> 124 with unchanged output and no increase in stream count unless the
  scope explicitly expands beyond the two RMWs.
