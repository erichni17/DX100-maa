# CG cache-on direct4/q16 apply lanes (2026-08-26)

## Decision

**ACCEPT four active apply lanes as the bounded cache-on direct4/q16 winner
for CG_NA=256 and CG_NA=1024.** Two lanes are also exactly faster than one,
but are dominated by four lanes at both sizes. Retain one lane as the default
outside this exact cache-on direct4/q16 regime.

This result does not authorize a native or full-CG launch, does not restore
p-side 16K ordering, and does not reverse the 2026-08-14 cache-poor
microbenchmark rejection. There were zero native runs and zero full-CG runs.

The treatment is `--maa_soa_jit_apply_lanes={1,2,4}`. Within each size, all
arms use one guest, one deferred checkpoint, the same
`direct4_product_page_fed_q16` guest treatment, and the value cache enabled
with 32 active owners. Normalized restore commands and resolved configs differ
only in the active-lane treatment value and arm-local redirect paths.

## Runner and validation milestone

Commit `6213ed927233dbaadfc0cc2c3c8756d50775b501` added the fail-closed
NA=256 lane sweep. Commit `9725d6b9fee67f60cabda991c0f84c9bb520cfed`
added the conditional NA=1024 gate: it revalidates the complete NA=256 raw
ledger, permits only arms named exact-faster there, and restores lane 1 plus
lanes 2 and 4 from one new guest/checkpoint.

The runner refuses a performance result unless all arms have:

- byte-identical raw and quantized fingerprints;
- all 11 byte-identical deterministic-reduction records;
- an identical direct4/q16 terminal mechanism dictionary;
- identical selected, delivery, A-read/write, publisher, page-fed response,
  and provenance ledgers;
- zero bounded-global-merge fallback and zero epoch drain;
- exactly one value-cache enable, exactly 32 active value owners, and exactly
  one active-lane option;
- a normalized command/config delta containing no treatment other than
  `maa_soa_jit_apply_lanes`;
- unchanged source, guest, simulator, Ramulator, checkpoint, and compile-input
  ledgers; and
- for lane 2 or 4, aggregate apply high water greater than the instruction
  count. Because per-instruction high water cannot exceed the active-lane
  count, the observed sums prove every instruction reached same-cycle high
  water 2 or 4, respectively.

The fixed apply-lane C++ state sizes were independently compiled as 32 B per
owner and 144 B per four-lane pool. The optimized and ASan/UBSan overlap-state
unit runs passed 2/2. Focused runner, full-run import, inherited reduction
order, and inherited small-application contracts passed 55/55. All source and
style hooks passed. The commit-message checker alone was skipped because this
checkout lacks the `MAINTAINERS.yaml` file that the hook unconditionally
opens; the separate Gerrit message hook passed.

## Accepted evidence roots

### CG_NA=256 screen

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-q16-retained-apply-lanes-20260826-20260826-131017-d5eb8ebb/evidence/direct4-q16-apply-lanes-na256-r1`

- source commit: `6213ed927233dbaadfc0cc2c3c8756d50775b501`;
- guest SHA-256:
  `b9630c796c03902d78a659369a63aed92a126017787465a02cff1ab237c9c8b7`;
- checkpoint ledger SHA-256:
  `47cc6707ffb38dbde29c95780def6806482e4c5f1254bc597de6b27ce80dbb9f`;
- raw-root ledger SHA-256:
  `8921e2dccc553dcb432d239560e97c9174c4d5d0fe77f6b7b81ca4a2d89a5a69`;
- checkpoint/lane-1/lane-2/lane-4 child exits: `0/0/0/0`;
- all 70 raw-ledger entries independently revalidated.

The `dx-runtime` dispatcher did not retain the outer Python return code after
the process was reparented. This is an operational-record caveat, not an
artifact ambiguity: all four child exit records are zero, the parent emitted
the terminal JSON after writing the immutable after-ledgers and result, the
process-exit callback fired, and the complete raw root rehashes exactly.

### CG_NA=1024 confirmation

Raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-q16-retained-apply-lanes-20260826-20260826-131017-d5eb8ebb/evidence/direct4-q16-apply-lanes-na1024-r1`

- service: `dx100-cg-direct4-q16-apply-lanes-na1024-r1.service`, result
  `success`, outer status `0`;
- source commit: `9725d6b9fee67f60cabda991c0f84c9bb520cfed`;
- guest SHA-256:
  `283ddf1c8abb3633668fcb030f663fc82d5d942936a32762e57ba25ebd6162f4`;
- checkpoint ledger SHA-256:
  `6375277e220f8b6aaed2280ba78ae4cdf5aa54840b83b42c1ef5348c7ec9e259`;
- raw-root ledger SHA-256:
  `a6834f858bca1b1db0c22a341c6eda180b6549d9075039e802ddd51a26b901c4`;
- checkpoint/lane-1/lane-2/lane-4 child exits: `0/0/0/0`;
- all 70 raw-ledger entries independently revalidated;
- the confirmation record binds the NA=256 raw-root hash above and names only
  lanes 2 and 4 as authorized candidates.

Both sizes use frozen gem5
`606eb920d2e33d1ad3948ae026057b2b74a12f2f5a94e202165c57dbf15f0427`
and frozen Ramulator
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.

## Performance and mechanism result

`simTicks` is the performance metric. `INDRMW` and request columns are the
first ROI's `system.maa.cycles_INDRMW` and
`system.maa.I0_IND_CyclesRequest`. Apply high water is reported as the sum
over terminal instructions and as sum/instruction count.

| CG_NA | active lanes | `simTicks` | lane-1 / arm | `INDRMW` cycles | request cycles | value issues | value hits | cache RD/WR packets | apply HWM |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 256 | 1 | 184,150,107 | 1.000000000x | 203,622 | 178,128 | 10,305 | 153,535 | 36,486 / 10,336 | 10 / 10 = 1 |
| 256 | 2 | 163,498,054 | 1.126313754x | 139,360 | 113,869 | 10,305 | 153,535 | 36,486 / 10,336 | 20 / 10 = 2 |
| 256 | 4 | 162,687,384 | 1.131926167x | 136,531 | 110,977 | 10,305 | 153,535 | 36,486 / 10,336 | 40 / 10 = 4 |
| 1024 | 1 | 836,613,005 | 1.000000000x | 1,354,218 | 1,213,311 | 66,862 | 998,098 | 222,884 / 66,951 | 65 / 65 = 1 |
| 1024 | 2 | 758,814,038 | 1.102527053x | 1,107,639 | 966,729 | 66,862 | 998,098 | 222,884 / 66,951 | 130 / 65 = 2 |
| 1024 | 4 | 750,520,164 | 1.114710897x | 1,081,403 | 940,494 | 66,862 | 998,098 | 222,884 / 66,951 | 260 / 65 = 4 |

Relative to lane 1:

- at NA=256, lane 2 lowers `simTicks` by 11.2148%, INDRMW cycles by
  31.5595%, and request cycles by 36.0746%;
- at NA=256, lane 4 lowers `simTicks` by 11.6550%, INDRMW cycles by
  32.9488%, and request cycles by 37.6982%;
- at NA=1024, lane 2 lowers `simTicks` by 9.2993%, INDRMW cycles by
  18.2082%, and request cycles by 20.3231%; and
- at NA=1024, lane 4 lowers `simTicks` by 10.2906%, INDRMW cycles by
  20.1456%, and request cycles by 22.4853%.

Lane 4 is 0.4958% lower in `simTicks` than lane 2 at NA=256 and 1.0930%
lower at NA=1024. Because all four lanes are physically present in every arm,
this selects the active setting rather than adding treatment-specific storage.

## Correctness and conserved ledgers

All three arms at NA=256 have the exact fingerprint set
`x_raw=d942be57c8fbc635`, `z_raw=f0b4138d16c12153`,
`x_q5=13d88d9190ab637a`, `x_q6=c9a998d28bb9d093`,
`z_q5=1490d5344f558db4`, and `z_q6=cc5468b8319a4711`.

All three arms at NA=1024 have the exact fingerprint set
`x_raw=8513a33e8cad9f9e`, `z_raw=59417f9f91294e19`,
`x_q5=6438e193ca03f10a`, `x_q6=9a5b269688cb4313`,
`z_q5=38c02e8ec15b7aa8`, and `z_q6=1caf0b6809305531`.

Every arm also has the same 11 deterministic-reduction bit records for its
size. The conserved ledgers are identical across lanes:

| CG_NA | instructions | selected/delivered | A reads/writes | publisher issues/accepts/WriteResps | publisher terminals | page-fed operations/admits/closes/responses | page-fed admitted/SPD-read/row-write words | fallback/drain |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 256 | 10 | 163,840 / 163,840 | 75 / 75 | 10,240 / 10,240 / 10,240 | 40 | 10 / 40 / 10 / 50 | 163,840 / 163,840 / 163,840 | 0 / 0 |
| 1024 | 65 | 1,064,960 / 1,064,960 | 375 / 375 | 66,560 / 66,560 / 66,560 | 260 | 65 / 260 / 65 / 325 | 1,064,960 / 1,064,960 / 1,064,960 | 0 / 0 |

A read responses equal read issues and A WriteResps equal write issues in
every arm. Coherent page-fed index read/write lines, coherent q-index backing,
host payload accesses, epoch drains, and bounded-global-merge fallbacks are
all zero.

## Fixed allocation and ordering closure

Every arm preserves the same architecture point:

- `p16_reorder_preserved=0` and `q16_reorder_preserved=1`;
- eight tiles/core, four cores, 4,096 words/tile, and 524,288 B physical SPD;
- 262,144 B external product backing, zero virtual-p backing, and zero
  coherent q-index backing;
- value cache enabled, 32 active owner lines per indirect unit, and a fixed
  128-line physical owner pool per indirect unit;
- 32,768 B fixed value-owner payload per MAA and an 8,192 B active prefix;
- four fixed apply lanes per indirect unit, four indirect units per MAA, and
  therefore 16 fixed lane owners per MAA;
- 32 B C++ state per lane owner, a 144 B fixed pool per indirect unit, and
  576 B fixed apply-lane pool state per MAA; and
- zero incremental payload bytes, control bytes, ports, or lane-pool bytes
  between lane 1, lane 2, and lane 4.

The 576 B value is the simulator's compiled C++ state-layout accounting, not a
post-layout SRAM or logic-area estimate. A synthesis result would be required
for physical area or timing claims.

## Reconciliation with the 2026-08-14 rejection

The 2026-08-14 `soa_jit_apply_lanes` result remains a valid rejection for its
cache-poor microbenchmark. There, lane 1/2/4 produced 52,211,843 / 54,581,566 /
54,522,722 `simTicks`: lanes 2 and 4 were 4.538% and 4.426% slower. Value-read
issues also changed with the lane setting (22,280 / 22,965 / 22,624), and the
report attributed the loss to request-timing changes that reduced merging and
increased cache-line reads.

The present result changes the workload and cache regime, not the lane
mechanism. In retained cache-on direct4/q16 CG, value issues, hits, and cache
packets are exactly invariant across lanes at both sizes. The intended apply
concurrency is fully exercised and is accompanied by lower INDRMW and request
cycles. This supports a bounded cache-regime-specific attribution: once
retained values remove the old traffic penalty, the remaining ordered apply
path benefits from the already provisioned lanes.

It does **not** show that more lanes universally improve SoA/JIT RMW, nor does
it invalidate the original cache-poor rejection. The correct selection is:

- cache-poor 2026-08-14 microbenchmark: retain one active lane; rejected;
- cache-on direct4/q16 bounded CG: select four active lanes; accepted;
- two active lanes: exact improvement, but dominated by four; and
- native, full CG, other workloads, or p16-preserving treatments: untested.

## Excluded operational artifact and handoff

The partial root
`.../evidence/direct4-q16-apply-lanes-na256-r2` is excluded. It was a duplicate
service started after a sandboxed PID view falsely reported r1 absent; the
host-visible ownership audit stopped it before any restore arm. It has no
terminal gate and is not evidence.

Retain commits `6213ed92` and `9725d6b9` plus the two accepted raw roots as
the bounded milestone. Select four active lanes only when the exact cache-on
direct4/q16 configuration is selected. Preserve the lane-1 default elsewhere.
No native or full launch is authorized by this result.
