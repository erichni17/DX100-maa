# Masked payload multi-line pipeline (2026-08-30)

## Question

The finite 32-byte payload port initially retains one line identity. Sparse CG
masks may use only a fraction of the eight 4-byte word slots per cycle. This
experiment allows 1/2/4/8/16 line identities to share the same total
eight-word port; it does not add payload capacity or per-line bandwidth.

Source: `73ad41c9fe1d0e183d2f9e27f99aff25136a2fdf`.
Binary SHA-256:
`3c65e8ef13725d553bd1e62a4d84b2f545e434c1f8e9892fbc79e623cd817f6d`.

## Exact CG_NA=256 sweep

Every arm preserves the exact CG fingerprint, all 11 reductions, 26,672
masked write issues/completions, and exact payload start/completion closure.

| Active identities | `simTicks` | Versus ideal | Versus one identity | Read cycles | Blocked cycles | Peak sum |
|---:|---:|---:|---:|---:|---:|---:|
| ideal | 246,463,712 | 0.000% | -4.451% | 0 | 0 | 0 |
| 1 | 257,943,613 | +4.658% | 0.000% | 33,644 | 319 | 10 |
| 2 | 257,687,266 | +4.554% | -0.099% | 33,585 | 124 | 20 |
| 4 | 256,799,285 | +4.194% | -0.444% | 33,562 | 90 | 40 |
| 8 | 256,799,285 | +4.194% | -0.444% | 33,562 | 74 | 80 |
| 16 | 257,538,591 | +4.494% | -0.157% | 33,562 | 0 | 160 |

Four identities are the local knee, but the absolute gain is only 0.444%
relative to one identity. Eight is timing-identical to four, and sixteen
regresses despite eliminating identity-capacity stalls.

## CG_NA=1024 promotion

The same-checkpoint larger pair confirms the gain does not scale:

| Arm | `simTicks` | Versus ideal | Versus one identity | Read cycles | Blocked cycles | Peak sum |
|---|---:|---:|---:|---:|---:|---:|
| ideal | 1,247,488,418 | 0.000% | -8.707% | 0 | 0 | 0 |
| 1 identity | 1,366,470,047 | +9.538% | 0.000% | 358,810 | 975 | 65 |
| 4 identities | 1,365,605,854 | +9.468% | -0.063% | 358,186 | 311 | 260 |

Four identities reach their bound in every logical operation, yet save only
624 aggregate read cycles and 0.063% latency. Most masked lines become ready
at roughly one line per cycle, so there is little same-cycle work to pack.

## Decision

Retain **one active line identity**. Multi-line staging is correct and bounded
but not worth its extra identity/control state at the larger target. The
remaining 9.468-9.538% CG cost is exposed finite payload-read latency, not
identity-capacity backpressure.

The accepted direct-gather path is unaffected: XRAGE with one identity exactly
reproduces 37,409,134 ticks and its output hash.

Raw roots:

- `/data1/nier/dx100-runs/2026-08-30-cg-na256-payload-pipeline-*`;
- `/data1/nier/dx100-runs/2026-08-30-cg-na1024-payload-pipeline-*`;
- `/data1/nier/dx100-runs/2026-08-30-xrage-payload-pipeline-nonreg-r1`.

Artifact ledger: `masked_payload_pipeline_artifacts_2026-08-30.sha256`.

## Independent review closure

Independent review found two accounting defects, both fixed:

1. The host helper originally embedded 16 identity entries even when the
   selected capacity was one. It now allocates exactly the configured count;
   optimized and ASan/UBSan tests assert one and eight physical entries.
2. The masked CG gate proved line starts/completions but not word-level port
   work. Final counters independently close scheduled words, read words,
   shared-port cycles, and isolated serial-cycle demand.

Final-source (`f90a18204b29792d225e66e2a5852e63f19809bd`) reruns use binary
SHA-256
`dcc6daf70558a4b8d418dc380438754097b80f2fb505ac791ee39c6d592403a1`:

- CG selected: 26,672 starts/completions, 163,840 scheduled/read words,
  33,644 shared-port and serial-demand cycles, exact output;
- XRAGE selected: 8,192 starts/completions, 65,536 scheduled/read words,
  16,384 shared-port and serial-demand cycles, exact 37,409,134 ticks.

Final raw roots:

- `/data1/nier/dx100-runs/2026-08-30-cg-na256-payload-accounting-*`;
- `/data1/nier/dx100-runs/2026-08-30-xrage-payload-accounting-final-r1`.
