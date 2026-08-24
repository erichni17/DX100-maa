# Hybrid full-application checkpoint (2026-08-24)

## Architecture

The primary design remains a 16K logical Row/Offset reorder scope with 4K
physical SPD tiles. Existing native tile-sweep endpoints are reused; none of
the campaigns below reruns a native arm.

## Accepted today

- CG now computes each 4K page product before publishing into one 16K SoA/JIT
  ADD. This reduced the preceding small hybrid from `6,566,455,483` to
  `6,348,682,603 simTicks` (3.32%) with exact output.
- A page-product-only CG target removes the unused logical scheduler and two
  tiles/core. Physical SPD payload falls from 655,360 to 524,288 bytes.
  Exact small-CG latency is effectively unchanged: `6,344,668,065 simTicks`,
  0.0633% below its immediate predecessor.
- Full-CG and HashJoin runners now disable per-event tracing. The small CG
  trace exceeded 1 GB, so full tracing would have distorted long runs.
- CG pre-A lookahead is a valid near-flat result, not a promoted optimization:
  exact first-ROI ticks changed from `6,344,668,065` to `6,341,118,332`
  (0.055948% lower). The option remains default-off and is retained for its
  prior full-GZP benefit.

Raw CG reports:

- `experiments/analysis/cg_page_product_fusion_live_2026-08-24.md`
- `experiments/analysis/cg_page_product_lane_removal_live_2026-08-24.md`
- `experiments/analysis/cg_page_product_pre_a_ablation_2026-08-24.md`

## Full gate status

| Workload | Unit | Raw root | Phase |
|---|---|---|---|
| NAS CG | `dx100-cg-page-product-full-baf142f7-r1` | `/data1/nier/dx100-runs/2026-08-24-cg-page-product-full-baf142f7-r1` | trace-free full checkpoint |
| NAS IS | `dx100-is-scalar-soa-full-a44aaa60-r5` | `/data1/nier/dx100-runs/2026-08-24-is-scalar-soa-full-a44aaa60-r5` | full O3 ROI |
| HashJoin PRH | `dx100-hashjoin-prh-full-recovery-20260824-061147` | `/data1/nier/dx100-runs/hashjoin-hybrid-prh-full-d7d29bf5-20260824-061147` | correct raw output; shifted pass is tail-only and pre-hardening evidence remains incomplete |
| HashJoin PRO, hardened | `dx100-hashjoin-pro-hardened-20260824-r1` | `/data1/nier/dx100-runs/2026-08-24-hashjoin-pro-hardened-r1` | active candidate-only full gate with frozen mechanism-status and hash contracts |
| HashJoin PRH, hardened | `dx100-hashjoin-prh-hardened-20260824-r1` | `/data1/nier/dx100-runs/2026-08-24-hashjoin-prh-hardened-r1` | active candidate-only full gate with frozen mechanism-status and hash contracts |
| GAPBS SSSP S22, original | `dx100-sssp-old-result-full-e690867f-r1` | `/data1/nier/dx100-runs/2026-08-24-sssp-old-result-full-e690867f-r1` | failed closed on unvirtualized 4,133-element tail |
| GAPBS SSSP S22, reviewed repair | `dx100-sssp-tail-repair-7b6f9c21-full-r1` | `/data1/nier/worktrees/codex-coordination/sessions/sssp-tail-repair-successor-20260824-155812-7c1e3190/evidence/sssp-tail-repair-7b6f9c21-r1` | rejected: L1 stride prefetch crossed the 4K physical SPD aperture at element 4,096 |

CG, IS, and the hardened PRO/PRH recoveries remain active with infinite runtime.
Both SSSP full candidates and the pre-hardening PRH recovery have exited.
Existing `dx-runtime` watch records are stale:
their worker PIDs are dead even where the record still says `watching`.
Acceptance therefore relies on each runner's internal fail-closed gate plus a
one-shot artifact audit after exit. In particular, SSSP writes `gate.complete`
only after its exact output, configuration, checkpoint identity, two stats
windows, and issue/response ledgers validate. An exit observation is not
success.

## Active optimization probes

The unified lead binary built from source commit `382f4fef` is archived at
`/data1/nier/dx100-binaries/gem5-39e1b45ec73521b2575b4f9674a7036f47de5d6f6c0923078ce1a1290f1c7d93.opt`.
It contains the currently integrated generic mechanisms, but is not yet a
performance result. Future candidate-only gates must set and verify the frozen
Ramulator SHA-256 `76ea3a9c...a15753`; the binary's default loader resolution
still points at the lead worktree library.

- Generic old-result write coalescing commits `f153dfaa`, `c0cb4414`, and
  `21e1a7ac` retain the existing eight-line, 1,128-byte per-unit buffer. The
  exact binary is archived as SHA-256 `36ed7d5c...a3ec9f`.
- The one-partial-write policy is rejected. On the frozen sparse old-result
  checkpoint, writes fell from 11,399 to 10,165 (10.83%) and packing rose from
  2.225 to 2.496 useful words/write, but first-ROI latency regressed from
  `687,827,203` to `733,637,257 simTicks` (6.66%). SSSP was not launched.
- The matched pressure sweep selected dense/four: `686,432,788 simTicks` and
  9,491 writes versus the exact oldest/eight reproduction at `687,827,203`
  ticks and 11,399 writes. This is 0.202728% lower latency and 16.7383% fewer
  writes with unchanged 512-byte payload and 1,128-byte buffer.
- Dense/four alone reduced small-SSSP writes 33.4061% but was 0.046838% slower.
  Composing the existing value cache, 64 active owners, and pre-A produced a
  replicated exact endpoint at `9,976,182,331 simTicks`, 0.262468% below the
  accepted small candidate, with 52.0055% fewer result writes. No new payload
  is provisioned; contexts remain eight.

Report:
`experiments/analysis/soa_jit_old_result_write_coalescing_2026-08-24.md`.

### Rejected shared-context expansion

The exact context8/context64 small A/B rejects 64 active contexts as a shared
default. HashJoin PRO improves only 0.1322%, while SSSP regresses 1.6964%.
SSSP old-result writes rise from 17,805 to 52,747 because wider A-line
concurrency fragments the fixed eight-line dense4 publisher. The option also
costs 30,464 modeled bytes per indirect unit relative to context8, or 121,856
bytes across four units, and its 64-way searches are not timing-qualified.

The next shared candidate therefore retains eight active contexts and moves
only completed A writes into an eight-credit compact `WriteResp` retirement
tracker. Its gate must preserve exact terminal response ownership and reject
the mechanism if the extra overlap again harms old-result coalescing.

The first implementation, worker commit `f35f9111`, is not integrated. Its
compact SSSP arm deterministically panics because the terminal checker is
called twice but mutates the tracker to finished on the first call. The review
also rejects region-attributing credits by credit number and identifies
incomplete installed-capacity/source-binary accounting. A successor must fix
all four issues and use a fresh evidence root; the failed `r2` root is not
performance evidence.

Report:
`experiments/analysis/hybrid_general_hotpath_worker_2026-08-24.md`.

Review:
`experiments/analysis/hybrid_compact_write_retirement_review_2026-08-24.md`.

The active-bit-free, regionless, certificate-bound successor is worker commit
`0d88fb41`. It passes focused tests and has a certified binary, but its fresh
SSSP/HashJoin gate was stopped in SSSP ROI for the professor-meeting pause.
It has no admissible performance result and remains default-off outside the
lead branch. Exact restart instructions are recorded in
`experiments/analysis/hybrid_compact_write_retirement_2026-08-24.md`.

### Rejected strict sequencing and page-aware ordering

The literal all-B-before-A reference cannot run at the current RowTable
geometry: it exposes 8,192 physical line slots, while the matched logical-16K
operation needs 9,668 A-line requests and completes through 852 capacity
drains. Doubling RowTable rows makes 16,384 slots available and makes strict
admission exact, but current and strict schedules then tie at `46,449,200`
ticks. The row64 control is faster at `45,316,140` ticks. Capacity therefore
regresses 2.5004%, strict scheduling adds no benefit, and the active packed
metadata cost is 105,728 bytes. Both strict and expanded-capacity candidates
are rejected.

Page-aware A-source ordering was screened offline within all 104 finite
RowTable epochs while preserving 9,954 request instances and 431 reissues.
Page-major moves page 0's last contributor only 78 request positions earlier
while increasing the bank-local activation proxy 26.3%. The two lighter
policies provide negligible or negative page movement and also increase the
proxy. No page-aware source policy is selected for gem5 implementation.

Reports:

- `experiments/analysis/hybrid_strict_two_phase_2026-08-24.md`
- `experiments/analysis/hybrid_strict_rowtable_capacity_2026-08-24.md`
- `experiments/analysis/hybrid_page_aware_source_schedule_2026-08-24.md`

## HashJoin partial result

The hardened one-shot classifier now recovers full PRO as a terminal-valid
arm directly from its raw log, config, and first-window ledgers even though
the failed two-kernel wrapper never appended its TSV row. PRO is correct at
`28,586,786,731` first-ROI ticks with
2,000,000 matches, 240/240 first-pass windows, zero shifted-pass windows,
240/240 SoA terminals, and closed A ledgers. The runner incorrectly required a
nonzero shifted pass for every full kernel and exited before PRH, leaving no
top-level gate. PRO is therefore partial evidence, not a complete HashJoin
result. Relative to frozen native16/native4 endpoints it is 18.5442%/16.4022%
slower and is rejected for performance. This is end-to-end context, not causal
virtualization attribution.

PRH recovery also reaches a correct raw terminal result: 2,000,000 matches and
`46,706,090,681` first-ROI ticks. It routes 240/240 first-pass windows but zero
shifted-pass windows because all 1,024 shifted radix partitions are smaller
than one logical 16K window and use the existing physical 4K tail path. This
is expected tail-only coverage, not a routing bug. The pre-hardening root lacks
the new frozen mechanism-status/hash gate, so its timing remains an observation
rather than a promotable result.

## Full SSSP failure

The full S22 candidate reaches tick `238,542,266,088` and then issues an SPD
access for element 4,096 while the physical SPD range is 0-4,095. The guest's
current frontier contains 4,133 elements, so a tail/fallback path that was not
exercised by the small exact graph still uses logical SPD indexing directly.
The simulator correctly aborts; no SSSP correctness or performance result is
claimed. The next SSSP gate must virtualize this tail or route it through a
legal bounded fallback.

The reviewed successor at worker commit `7b6f9c21` preserves four-page 16K
hybrid windows, routes batches up to 4K through bounded SPD, and uses exact
ordered CPU MIN for irregular 4K+1 through 16K-1 batches. Its small exact gate
passes total = produced = consumed = 69,632 words, 65,536 accelerated plus
4,096 CPU words, zero measured illegal SPD attempts, exact output, and closed
old-result responses. The full S22 gate nevertheless rejected the source at
tick `239,082,572,292` on SPD element 4,096. The printed 4,132 is the frontier
size, not a generated range-tile size: RangeFuser is already capped at 4,096
physical words. The full command enables an L1 stride prefetcher, while the
small gate does not; after a full physical-page host scan, the speculative next
cache line begins at element 4,096 and the CPU-side aperture currently treats
it as an architectural demand. This explains both the exact boundary and the
small/full discrepancy. The wrapper and explicit validator both fail, so no
SSSP full result is claimed.

Worker commit `2040dfd9` is rejected. It preflights the aggregate frontier
chunk and diverts every chunk above 4K to CPU, including valid 16K logical
windows; larger chunks can also exceed its fixed 16K fallback arrays. The
repair belongs at the CPU-side SPD aperture: a non-binding speculative request
outside the physical payload may be dropped/responded without touching SPD,
but a real demand outside 0--4,095 must remain fail-closed. That policy needs a
stride-prefetch reproduction before a fresh small and full SSSP gate.

## Resume order

1. Validate CG and IS after their services exit; do not read timing before
   their correctness and terminal gates close.
2. Repair the SSSP speculative-prefetch boundary without weakening demand
   bounds, require a stride-prefetch reproduction and fresh small exact gate,
   then relaunch candidate-only full S22; native baselines remain reusable.
3. Allow the hardened PRH candidate gate to exit and classify its frozen
   mechanism-status and hash evidence. Its shifted phase is tail-only for this
   input and must never be misreported as routed coverage.
4. Resume compact-retirement only from the certified `0d88fb41` fresh-root
   instructions, and reject it unless both kernels are nonregressing with one
   improving by at least the predeclared 0.5% threshold.
