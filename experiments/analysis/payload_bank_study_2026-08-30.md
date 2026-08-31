# Finite payload RAM banking (2026-08-30)

## Mechanism

The payload word budget and aggregate read width remain fixed. Each retained
word reference maps to `physical_pool_index % banks`; each bank serves at most
one word per MAA cycle. A busiest-bank-first arbiter avoids artificial
priority conflicts, while the global width still caps total words read per
cycle. No payload words, line identities, or read bandwidth are added.

The bank-aware source is `9393ef52e47357d9192050e539e013b6ce64df23`.
Binary SHA-256:
`aa5c70b140b6fb66bfb9f4a28b34f009f025cf639eb288c01dbb91b0d2f609bb`.

An independent review found that the first version of this report mixed early
bank points from binary `89fd21c5...` with the final binary above. The affected
CG banks 1/2/4 and XRAGE banks 0/2/4/8/16 were rerun on `aa5c70b1...`; every
reported tick count and mechanism counter reproduced exactly. The tables below
now use only final-binary runs. The original ledger remains frozen; the
same-binary successor is
`payload_bank_study_same_binary_artifacts_2026-08-30.sha256`.

## CG_NA=256

All arms retain one line identity, an eight-word/32-byte aggregate port, 256
payload words per indirect unit, and exact CG output/mechanism work.

| Banks/unit | `simTicks` | Versus conflict-free | Versus ideal copy | Read cycles | Bank-limited cycles |
|---:|---:|---:|---:|---:|---:|
| conflict-free | 257,943,613 | 0.000% | +4.658% | 33,644 | 0 |
| 1 | 298,239,233 | +15.622% | +21.007% | 163,840 | 137,168 |
| 2 | 280,853,335 | +8.882% | +13.953% | 105,980 | 79,308 |
| 4 | 268,743,052 | +4.187% | +9.040% | 73,844 | 47,172 |
| 8 | 263,109,365 | +2.003% | +6.754% | 55,731 | 28,553 |
| 16 | 260,888,630 | +1.142% | +5.853% | 44,264 | 13,004 |
| 32 | 258,733,938 | +0.306% | +4.979% | 38,498 | 5,425 |
| 64 | 257,697,282 | -0.095% | +4.558% | 35,482 | 1,915 |

## CG_NA=1024

The same-checkpoint larger promotion preserves exact output and all 358,114
masked write starts/completions.

| Banks/unit | `simTicks` | Versus conflict-free | Versus ideal copy | Read cycles | Bank-limited cycles |
|---:|---:|---:|---:|---:|---:|
| conflict-free | 1,366,470,047 | 0.000% | +9.538% | 358,810 | 0 |
| 32 | 1,369,240,097 | +0.203% | +9.760% | 384,950 | 26,199 |
| 64 | 1,366,322,624 | -0.011% | +9.526% | 362,534 | 3,726 |

Thirty-two banks recover the sub-10% target. Sixty-four save only another
0.234% versus ideal while doubling the bank count.

## XRAGE

All arms preserve the exact output hash and 65,536 scheduled/read words.

| Banks | `simTicks` | Versus conflict-free | Read cycles | Bank-limited cycles |
|---:|---:|---:|---:|---:|
| conflict-free | 37,409,134 | 0.000% | 16,384 | 0 |
| 2 | 39,078,676 | +4.463% | 41,925 | 33,733 |
| 4 | 37,526,509 | +0.314% | 28,596 | 15,073 |
| 8 | 37,412,264 | +0.008% | 20,685 | 4,413 |
| 16 | 37,412,890 | +0.010% | 17,552 | 1,169 |
| 32 | 37,401,309 | -0.021% | 16,690 | 306 |

XRAGE reaches its knee at eight banks, but the unified 32-bank CG choice is
also timing-equivalent to conflict-free XRAGE.

## Decision

Select **32 payload banks per indirect unit** as the unified cost/performance
knee. With 256 CG payload words, each bank holds eight words; with the 2,560
word XRAGE combiner pool, each bank holds 80 words. The aggregate port remains
32 bytes/cycle, so the banks provide conflict avoidance rather than extra
bandwidth.

This is bounded behavioral evidence, not SRAM synthesis. Decoder/periphery,
muxing, area, energy, and Fmax remain unmeasured.

Raw roots:

- CG NA256: final-binary `r3` roots for banks 0/8/16/32/64 and
  `samebin-r1` roots for banks 1/2/4;
- CG NA1024: final-binary `r2` roots for banks 0/32/64;
- XRAGE: final-binary `samebin-r1` roots for banks 0/2/4/8/16 and the
  final-binary `banks32-r1` root.

Machine-readable table: `payload_bank_study_same_binary_2026-08-30.tsv`.
Successor artifact ledger:
`payload_bank_study_same_binary_artifacts_2026-08-30.sha256`.
