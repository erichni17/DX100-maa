# Guarded fused-p16 product successor (2026-08-26)

## Decision

**ACCEPT the repaired implementation and fresh evidence as the bounded
successor authority for the guarded FP32/MUL fused-p16 product micro and the
exact `CG_NA=256` comparison.** Do not promote a native, full-CG,
general-workload, iso-area, or variability claim. `native_runs=0` and
`full_cg_runs=0` remain explicit.

This report supersedes the rejected `014b8461` package and applies the mandatory
checklist in
`experiments/reviews/2026-08-26_fused_p16_product_independent_review.md`.
The final source is `4a4d91b8f176c33779804fbd163014593d89e737`; the optimized
gem5 SHA-256 is
`271836b58d02d9d50a658cd5c7628e15559ca22d3a04477ab15475e3744dfd2e`.

## Review-blocker closure

| Independent-review finding | Successor disposition |
|---|---|
| Missing zero stats passed as zero | Repaired. The gate uses a 43-field required first-ROI schema. Fused and bounded-global counters already emit zero; `IND_NumOTEpochDrain`, `STR_PublishIssues`, and `STR_PublishWriteResponses` no longer use `statistics::nozero`. Missing or renamed fields raise/fail. The shell zero comparison first captures a successful parse and cannot reinterpret an empty substitution as zero. Removed/renamed-stat tests exercise both Python and shell gates. |
| Micro lacked authoritative terminal/root evidence | Repaired. The accepted micro persists wrapper, checkpoint, and restore exits; checkpoint and `m5_exit` terminal files; configurations, logs, stats, trace, result, manifest, binary, and checkpoint in a 39-file raw ledger; and a success-only gate binding that ledger's digest. |
| Functional control state was omitted | Repaired in the accounting below and mechanically bounded in `FusedP16ProductState.hh`. The generation counter, current generation, active bit, and every IF descriptor-closure bit are charged. |
| p-span report overstated decode | Corrected to the implemented conservative contract: p needs one 4-byte-aligned word in a registered region, and the whole registered region owns its hazard/disjointness span. Product, colidx, and coefficient remain aligned full 65,536-byte spans. The accepted small-CG p array remains legal. |
| Tests overstated live behavioral coverage | Corrected below. Pure state/helper tests, source-contract checks, parser negatives, and the positive live micro are distinguished explicitly; no live negative decoder/hazard/WriteResp claim is made. |

Two intermediate roots are evidence of the fail-closed repair, not authorities:

- `fused-p16-product-micro-5948c3b3-r1` is **REJECTED**. Its gate was produced
  before the empty-command-substitution defect was found; its stats omit
  `IND_NumOTEpochDrain` and `STR_PublishIssues`.
- `cg-fused-p16-q16-5948c3b3-na256-r1` is **REJECTED**. The candidate completed,
  but the runner stopped on the absent zero `STR_PublishIssues`; it has no final
  result/raw-root gate.

Neither root was reused.

## Validation

The clean X86 optimized target rebuilt and linked with `scons
build/X86/gem5.opt -j16`. The worktree's empty Ramulator gitlinks were supplied
at build time from a clean sibling materialization of the same indexed spdlog
and yaml-cpp commits; runtime resolution was pinned to the frozen Ramulator
SHA-256
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.

The optimized and ASan/UBSan 16K state binaries both pass. The two runner
`unittest` modules pass 5/5 each. The five plain Python contract functions pass
through a direct harness because the base Python installation has no `pytest`.
All content/style hooks pass. The repository's commit-message hook was skipped
only because this checkout lacks `MAINTAINERS.yaml`; it otherwise crashes before
validating any tag.

### Fresh exact-16K micro

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-fused-p16-product-evidence-repair-2026082-20260826-160656-c4f154c5/fused-p16-product-micro-successor-r2`

- 39/39 files pass `sha256sum -c raw_root.sha256`.
- Raw-root ledger SHA-256:
  `05505d7a88ff45811dfa5c201d5da06dcfd9eb86cc55bd049cae1cdbf3a16395`.
- The gate binds that digest; wrapper/checkpoint/restore exits are `0/0/0`, and
  the explicit terminal files are `checkpoint` and `m5_exit`.
- Guest SHA-256:
  `4f34c706d6e34192d42ea43a13c6be4f7d5dee1d52a1732a3fa49ad09668c768`.
- Exact hashes agree:
  `reference=product=q=6939999077410828482`; sentinels and errors are zero.
- One fused operation and one complete 16K epoch close 16,384 source ordinals,
  coefficient deliveries, MUL accepts/completions, product insertions, and
  semantic WriteResp completions. Coefficient issue/response/fill is
  `5712/5712/5712`; hits/merged/evictions are `8211/2461/5680`.
- q closes one operation, four page admissions, one close, five command
  responses, and 16,384 value deliveries.
- `IND_NumOTEpochDrain`, fused drains/fallbacks/publisher/virtual-p,
  bounded-global fallback, and `STR_PublishIssues` are all present and zero.
- `simTicks=4107055166` is correctness/provenance only.

### Fresh same-checkpoint `CG_NA=256` pair

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-fused-p16-product-evidence-repair-2026082-20260826-160656-c4f154c5/cg-fused-p16-q16-successor-na256-r2`

- 54/54 files pass `sha256sum -c raw_root.sha256`.
- Raw-root ledger SHA-256:
  `693f43edf3c5ea41a53d6d7dcf3bd0dfeb5ff5a86672453d79e81e3883d690e9`.
- Checkpoint ledger SHA-256:
  `9fe33895c63195338b984ea0565cb503af551bd1e22339fc25c7f9e92c26fc57`.
- Artifact-ledger SHA-256:
  `b298b362da6f89355e508d07a841f6dde0984c10979e8e004a0c6e2758d19aab`;
  artifact and checkpoint before/after ledgers compare byte-for-byte equal.
- Checkpoint/control/candidate exits are `0/0/0`; each restore has exactly one
  `m5_exit`. Resolved configurations normalize identically outside the guest
  selector.
- Raw and quantized fingerprints are exactly equal, including
  `x_raw=d942be57c8fbc635` and `z_raw=f0b4138d16c12153`; all eleven deterministic
  reduction lines are byte-for-byte equal.
- Both arms close exactly ten p16 and ten q16 windows, 163,840 product/q words,
  40 page admissions, ten closes, and 50 command responses.
- Candidate fused ledgers close `10/10` operations/epochs, 163,840 source,
  coefficient-delivery, MUL, insertion, and semantic-write words. Coefficient
  issue/response/fill is `13385/13385/13385`. q value
  issue/response/fill/hit/delivery is
  `10305/10305/10305/153535/163840` in both arms.
- Every required zero field is schema-present: generic/SoA/fused drains,
  bounded/fused fallbacks, fused publisher/virtual-p, candidate publisher
  issues/responses, coherent page-fed index traffic, and hidden/host spill
  terminal fields are zero.

First-ROI performance independently recomputes as:

| Arm | simTicks |
|---|---:|
| Cache-on page-fed p16/q16 control | 419,398,090 |
| Fused-p16 product + cache-on page-fed q16 candidate | 396,154,397 |
| Candidate reduction | 23,243,693 (5.542155187211%) |
| Control / candidate | 1.058673318221x |

This is one deterministic bounded observation, not a variability estimate.

## Hardware accounting

The matched system has four indirect units, one MAA, four cores, and eight IF
instruction slots/core (32 IF slots total).

| Item | Semantic charge | Conservative C++/selected-system bound |
|---|---:|---:|
| Fused response substate | 8 B/indirect unit | 32 B total; one asserted byte on each of eight existing response slots |
| Generation counter + current generation + active bit | 17 B/indirect unit | at most 24 B/unit, 96 B total; compile-time bounded layout |
| IF coefficient-closure bit | 1 B/IF slot | at most 8 B/slot, 256 B total; alignment-conservative compile-time bound |
| Timed ALU identity | 8 B for the one in-flight pair | byte-rounded existing-lane sideband; one pair at a time |

Candidate-specific functional control state is therefore 140 semantic bytes in
the selected four-unit/32-slot system, with a conservative 392-byte aggregate
C++/byte-rounded bound for the listed state. The newly disclosed lifecycle and
IF closure portion is 100 semantic bytes, bounded by 352 C++ bytes.

Descriptor **payload** remains zero modeled bytes: word five and existing
decoded address fields are reused, while the closure bit is now separately
charged as control state. Row/Offset, coefficient-owner, response-line, and
combiner payloads reuse provisioned arrays. External cache/memory port delta is
zero. The internal path is one 32-bit p input, one 32-bit coefficient input, an
in-place 32-bit product, and valid/backpressure/identity wiring. Admission
holds the existing 16-lane ALU's ordinary one-cycle FP32 MUL lane until
combiner acceptance; no new multiplier or payload queue is claimed.

The candidate retains 524,288 B physical SPD payload and 262,144 B product
backing, removes 262,144 B virtual-p backing, and removes 65,536 B virtual-p
writes plus 65,536 B reads per 16K window. Product retirement remains charged
and closes by exact WriteResp.

## Exact test-coverage statement

- The optimized/sanitized C++ state test behaviorally covers the one-byte
  response-owner transitions, stale/wrong tags, a full 16K collision pattern,
  reversed coefficient delivery, coalescer pressure, exact products, pure
  registered-p span helpers, and a local reversed semantic-completion model.
- Behavioral negative tests cover removed and renamed stats, the former
  missing-as-zero shell path, p misalignment/out-of-region, registered-p
  overlap, and overlap among exact 16K spans.
- Python contract tests verify ABI/decode/hazard/geometry source structure and
  runner configuration. They do not instantiate gem5 decode or IF hazards.
- The fresh positive micro exercises the live decoder, p/coefficient response
  reorder, multiplier, bounded combiner, acknowledged product retirement, and
  q handoff. It is positive integration evidence, not a live negative decoder,
  hazard, or arbitrary WriteResp-injection test.

No checklist blocker remains within this bounded scope.
