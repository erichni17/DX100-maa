# CG physical-page-product pre-A ablation (2026-08-24)

## Verdict

**REJECT promotion as a CG optimization.** The matched, trace-free small-CG
pair is correctness-closed, but the measured effect is near-flat. Enabling
`--maa_soa_jit_pre_a_value_lookahead` exercised the intended mechanism and
reduced the observed ROI `simTicks` from 6,344,668,065 to 6,341,118,332:
1.000559796x, or exactly 0.055948% lower. The final `simTicks` were
6,423,134,661 off and 6,420,415,630 on (0.042332% lower). Retain the
already-existing default-off pre-A option because prior full GZP evidence
benefits from it, not because of this CG result.

## Provenance and comparability

- Source worktree: clean `baf142f7254581cb56de1c9e7458e1af1ae8f7ba`.
- Raw root:
  `/data1/nier/dx100-runs/2026-08-24-cg-page-product-pre-a-pair-baf142f7-r1`.
- Archived gem5 SHA-256:
  `ef070d16bb1b25668fe80468693dade4eeaf1776a72fbc51d7a9ce070e5af483`.
- Frozen 8-lane guest SHA-256:
  `6e5261d0f4d0e41ae8349c4571644f3671f908f1089a56749b8d4450356ab361`.
- Frozen checkpoint-file-list SHA-256:
  `36c63dc1d9da6f91e0ce22e0bf64dc09b93d0391c3cf9b1648a1fd63a3a35993`.
- Both arms restored the same checkpoint and guest in parallel, without a
  timeout, native arm, or debug trace. Resolved `config.ini` files differ only
  in the pre-A Boolean and the required per-arm redirected `/proc`, `/sys`, and
  `/tmp` host paths.
- Both arms retained 16K logical elements, 4K physical elements, eight
  tiles/core, and `logical_tile_page_scheduler=false`.
- The raw root is frozen with `manifest.txt`, `decision.txt`,
  `hashes.sha256`, and `gate.complete`; `hashes.sha256` covers the report and
  every input used for this decision.

## Correctness and mechanism closure

Both arms emitted one passing fingerprint, one passing
`CG_LOGICAL16_RMW_TERMINAL`, one `ROI End!!!`, and one final `m5_exit`. Their
fingerprint lines are byte-identical to each other and to the frozen reference:
`x_q5=6438e193ca03f10a`, `x_q6=9a5b269688cb4313`,
`z_q5=38c02e8ec15b7aa8`, and `z_q6=1caf0b6809305531`; every scalar fingerprint
field is also identical. No panic, fatal, assertion, abort, segmentation fault,
or error marker occurred.

The first statistics window closed identically except for the intended pre-A
counters:

| Counter | Off | On |
|---|---:|---:|
| SoA/JIT instructions / terminals | 65 / 65 | 65 / 65 |
| Selected / aliases applied | 1,064,960 / 1,064,960 | 1,064,960 / 1,064,960 |
| A read issues / responses | 375 / 375 | 375 / 375 |
| A write issues / responses | 375 / 375 | 375 / 375 |
| Publisher issues / accepts / responses | 133,120 / 133,120 / 133,120 | 133,120 / 133,120 / 133,120 |
| Publisher terminals | 520 | 520 |
| Bounded-global-merge fallbacks | 0 | 0 |
| Pre-A issues / ready-at-A / uses | 0 / 0 / 0 | 375 / 65 / 375 |

The guest terminal is identical across arms: 65 full and physical-product
windows, 260 physical ALU vectors, zero logical ALU vectors/windows, Q routing
52/52, residual routing 13/13, 1,064,960 staged index and product words, and
260 index plus 260 product publisher pages. Physical SPD payload remains
524,288 bytes with zero scheduler-reserved lanes and zero reserved payload.
The corrected fail-closed shell audit passes these exact invariants against the
frozen files; no simulator arm was rerun during parser correction.

## Raw hashes

- Off restore/stats/config:
  `468e40136b0d344ef6c97480bab7ad678b64b6ebfa9a037858b9794c9e4c8c69`,
  `782d59ec9c4e39d5e0d0476de814a09f687736d3dfb0b2f504b34ed35bd8cbf3`,
  `60fa2e2e9c00cd72b88e37a14a4036262347e89f0b50c1cba71ff1530a2cd256`.
- On restore/stats/config:
  `18e67a162a9087a65838e9a877a01f21023fb6f9834b5e1710d050b484d083f7`,
  `0f61d8b3b8536dc218f346365921306466de212c69de877fadce172e7e8296e4`,
  `739e7b329c1a9c40703aaec31ddfbef013e211b0289d57ed654141c9c5d9f38c`.
- The off/on stderr files are identical benign gem5 warnings, SHA-256
  `dfb3275a86a613f56140a33e2a3edcfa92967ebe24da1e3cbd22d9085462ba91`.
