# Independent fused-p16 product review (2026-08-26)

## Decision

**REJECT the `014b8461` acceptance package as integration authority.**  Do not
promote it beyond the already stated bounded prototype.  The exact exercised
micro and `CG_NA=256` candidate are numerically correct, the CG performance
result reproduces from immutable raw evidence, and the inspected live path
does not expose a wrong-product or early-completion bug.  Acceptance is still
blocked by two evidence defects and incomplete hardware-state accounting.

Scope was the inclusive commit range `4be95adb^..014b8461`; no source was
changed and no simulation was launched.

## Findings

### [Medium] Zero-valued mechanism gates pass when their stats are absent

`stat_sum_or_zero()` converts every missing statistic into zero
(`experiments/scripts/run_cg_fused_p16_product_q16.py:210-215`).  The candidate
then accepts zero for drains, fallbacks, publisher traffic, virtual-p bytes,
and stream-publisher activity (`:319-329`); the control similarly accepts all
missing fused counters (`:331-335`).  The micro runner has the same behavior:
`stat_zero()` never requires a match (`experiments/scripts/run_fused_p16_product_micro.sh:129-135`),
then uses it for every forbidden mechanism (`:149-152`).

The cited raw files currently contain the positive ledgers and the source path
structurally forbids the advertised escapes, so this does not falsify the
recorded run.  It does mean the runners do not implement the claimed
fail-closed mechanism gate: a removed or renamed forbidden counter is
indistinguishable from a measured zero.

### [Medium] The micro results are not covered by an authoritative raw-root ledger

The micro runner records artifact hashes before/after and hashes only the
checkpoint files (`experiments/scripts/run_fused_p16_product_micro.sh:73-83`,
`:205-211`).  It does not persist the checkpoint/restore return codes and does
not hash `run/restore.log`, `run/stats.txt`, `run/fused_p16_trace.log`, or
`result.txt` into a terminal root ledger.  In contrast, the CG runner hashes
every result file except the ledger/terminal marker and records the resulting
ledger digest (`experiments/scripts/run_cg_fused_p16_product_q16.py:627-643`).

The present micro files show the expected `m5_exit`, exact hashes, zero
sentinels, and closed ledgers, and its checkpoint plus five artifacts rehash.
There is nevertheless no frozen expected digest with which to prove that the
current micro logs/stats/trace are the files originally accepted.  Therefore
the micro may be used as inspectable supporting evidence, not immutable
promotion evidence.

### [Medium] The byte/state ledger omits required fused control state

The analysis charges 8 B/unit of response substate, an 8 B ALU identity, and
zero modeled descriptor payload (`experiments/analysis/fused_p16_product_2026-08-26.md:135-143`).
The response charge itself is supported by the one-byte owner assertion
(`src/mem/MAA/FusedP16ProductState.hh:178-184`), and the ALU-specific new
unit/slot/offset fields are a bounded 40-bit delta
(`src/mem/MAA/ALU.hh:101-108`).  But the implementation also adds a 64-bit
generation counter, a 64-bit current generation, and an active bit per
indirect unit (`src/mem/MAA/IndirectAccess.hh:603-620`), plus descriptor-closure
state in every IF instruction (`src/mem/MAA/IF.hh:166-171`).  The generation
and active state are functional ownership state, not optional statistics, and
are absent from the report's candidate state delta.

No multiplier-cost defect was found: admission checks the shared ALU idle
state, the direct pair makes that ALU non-idle, and it is released only after
combiner acceptance (`src/mem/MAA/MAA.cc:2328-2355`, `:2376-2391`).  This is a
conservative one-pair use of the existing 16-lane ALU, not a hidden extra
multiplier.  The zero **external** port delta is also supported; the new
internal pair wiring is already disclosed.  The rejection is for omitted
control storage, not multiplier or external-port fabrication.

### [Low] The documented p-span guard is stronger than the implemented guard

The analysis says p, product, colidx, and coefficient are all aligned,
registered 65,536-byte spans
(`experiments/analysis/fused_p16_product_2026-08-26.md:19-24`).  Decode applies
the 64-byte/full-65,536-byte check only to product, colidx, and coefficient.
For p it requires only 4-byte alignment and one registered word
(`src/mem/MAA/CpuSidePort.cc:885-909`), then treats p's entire registered
region as the disjoint/hazard span (`:910-937`).  This is conservative for
hazards and is consistent with the small `CG_NA=256` p array, but the report's
stated four-span contract is inaccurate.

### [Low] The unit-test coverage statement overstates behavioral coverage

The acceptance report says optimized/sanitized state tests cover guarded
decode, registration/alias/capacity rejection, combiner pressure, reordered
WriteResp, and terminal state
(`experiments/analysis/fused_p16_product_2026-08-26.md:42-50`).  The focused
Python contract test verifies source-string presence for decode/hazards and
geometry (`experiments/tests/test_fused_p16_product_contract.py:19-65`).  The
C++ state test exercises the one-byte owner and coalescer/product arithmetic,
but models WriteResp completion with a local counter rather than instantiating
the gem5 combiner/retirement path
(`tests/maa/fused_p16_product_state_test.cc:59-80`, `:221-245`).  The positive
micro exercises the live path, but negative decoder/hazard/WriteResp cases are
not behavioral tests as claimed.

## Verified implementation behavior

- Guarded decode is limited to `INDIR_LD_VIRTUAL_INDEX` FP32/MUL and becomes a
  live fused instruction only after word five is received
  (`src/mem/MAA/IF.hh:298-317`).  Its IF hazard set is p READ, colidx READ,
  coefficient READ, and product WRITE (`src/mem/MAA/IF.cc:223-263`).
- Decode requires exact `0:16384:1`, a 16K logical tile/epoch, 32 reordered Row
  slices, one partition, no range-pass/spool/global merge, 8 response slots,
  the finite 16x4-way/4-bank combiner, one word attempt/cycle, 32 writes, and a
  32-line/no-prefetch coefficient pool
  (`src/mem/MAA/IndirectAccess.cc:6195-6263`).  Row/Offset pressure panics
  instead of draining (`:6615-6625`, `:7322-7337`).
- Each p response retains its Row/Offset head.  Coefficient waiters are the
  injective response-slot IDs; ready cache lines with live waiters cannot be
  evicted (`src/mem/MAA/IndirectAccess.cc:9830-9978`,
  `src/mem/MAA/SoaJitOverlapState.hh:616-639`).  The callback checks generation,
  indirect unit, response slot, and Offset slot (`src/mem/MAA/IndirectAccess.cc:10170-10199`).
- The ALU stores the original p bits before overwriting the retained word with
  the product, and restores p only after successful combiner insertion
  (`src/mem/MAA/ALU.cc:117-158`, `:1044-1064`;
  `src/mem/MAA/IndirectAccess.cc:9994-10040`).  Duplicate p/colidx aliases
  therefore reuse the original source word rather than the preceding product.
- The combiner rejects a duplicate resident logical word and cannot evict a
  victim until its payload is accepted by bounded retirement
  (`src/mem/MAA/IndirectAccess.cc:10224-10385`).  The direct-index ordinal
  feeder supplies each logical destination once; the live micro closed all
  16,384 products despite duplicate source aliases and 5,680 coefficient
  evictions.
- Retirement creates acknowledged `WriteReq`s with exact-address exclusion and
  per-write page/word metadata (`src/mem/MAA/IndirectAccess.cc:9606-9780`).
  Only the matching `WriteResp` advances completed words and line/page
  visibility (`:9667-9692`, `:10928-11056`).  Response state is reached only
  after sources, combiner payload, and outstanding writes are empty
  (`:7207-7239`, `:10606-10623`), after which fused terminal accounting is
  checked (`:5627-5744`).  Thus q submission after the producer completion
  observes all product writes, not ALU completion or write issue.

## Evidence rehash and performance recomputation

The CG root
`/data1/nier/worktrees/codex-coordination/sessions/hybrid-fused-p16-product-prototype-20260826-20260826-132434-13cbd7df/cg-fused-p16-q16-9d8b8810-na256-r2`
passes `sha256sum -c raw_root.sha256` for all 54 ledgered files.  Recomputed
digests are:

- raw-root ledger: `1b970ba6e72dbbbec28d22405071d4ec88a1ccba7af1c3c39d77e3301f459be3`
- checkpoint before/after ledger: `b6e5d8e88d1d197e58f64289730287cd87acaaa789e15f3424f98ce4fc79f085`
- artifact before/after ledger: `f28e921fce66c1d64bbc5362de4d5f5486b9a30a20624fed7995ba6101485f48`

Both restore exit files contain zero; both logs have exactly one terminal
`m5_exit`; normalized resolved configs are identical.  Re-running the current
gate parser on the frozen arms reproduces ten fused epochs, 163,840 source /
coefficient / MUL / product / q completions, 13,418 closed coefficient reads,
10,305 closed q reads, and zero recorded drains/fallbacks/publisher/virtual-p
activity.  Fingerprints and all eleven deterministic reductions are identical.

The artifact paths are intentionally mutable: a present-day direct check of
the old `artifact_sha256.before` differs only for the runner subsequently
tightened by `e20ab3ba`.  The frozen before/after ledgers and raw-root ledger
still match, and the implementation/binary exercised at source `9d8b8810`
predates only runner tests and the acceptance report.

Using first-ROI `simTicks`:

```
control        419,423,756
candidate      397,150,050
delta           22,273,706 ticks
lower              5.310549457766%
control/candidate   1.056083855460x
```

This confirms the reported bounded performance observation.  It does not
repair the review findings, supply repetition/full-workload evidence, or make
the prototype promotion-ready.
