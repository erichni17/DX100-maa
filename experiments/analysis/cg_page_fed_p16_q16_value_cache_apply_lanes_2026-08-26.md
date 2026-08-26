# Cache-on page-fed p16/q16 apply-lane A/B — 2026-08-26

## Scope and gate

This is a bounded, fail-closed A/B between `--maa_soa_jit_apply_lanes=1` and
`=4`, using one `page_fed_product_soa_jit` guest and one deferred checkpoint
per size. Both arms retain p16 and q16 reordering, use four cores/eight
tiles per core, 524,288 B SPD, 524,288 B external backing (262,144 B each of
virtual-p and product), zero q-index backing/host payload/fallback/drain, and
the cache-on 32-owner active prefix of a fixed 128-owner pool.

The required first size is `CG_NA=256`. `CG_NA=1024` is forbidden unless the
terminal screen has byte-identical raw/quantized fingerprints and all 11
deterministic-reduction records, exact p-gather/product/q/A/value/publisher/
page-fed ledgers, lane-4 high-water of exactly four per instruction, and lane
4 is strictly lower in `simTicks`. The only normalized command/config delta is
`maa_soa_jit_apply_lanes`; the fixed four-owner lane pool therefore adds zero
incremental bytes, control bytes, or ports.

No native or full-CG simulation is permitted.

## Direct4 reconciliation boundary

The runner independently rehashes the accepted direct4 cache-on lane screen at
`/data1/nier/worktrees/codex-coordination/sessions/hybrid-q16-retained-apply-lanes-20260826-20260826-131017-d5eb8ebb/evidence/direct4-q16-apply-lanes-na256-r1`.
That evidence confirms the fixed-pool accounting and apply-lane mechanism, but
its p16 reorder is false. It must not be used to attribute p-stage timing in
this p16-preserving experiment.

## Result — rejected at CG_NA=256

The sole completed screen is
`/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-apply-lanes-na256-r2`.
It used one ordinary guest, one deferred checkpoint, and exactly the two
restores. The first r1 root is excluded: it failed Ramulator preflight before
the checkpoint. A duplicate r3 service was stopped during its checkpoint
before it could create a restore artifact. Neither is evidence.

Both r2 restores exited zero and emitted byte-identical raw/quantized
fingerprints (`x_raw=d942be57c8fbc635`, `z_raw=f0b4138d16c12153`) and all 11
deterministic reduction records. The exact p16/q16 terminal dictionaries
match: ten windows; 163,840 selected/delivered/page-fed-admitted/SPD-read/
row-write words; 40 admissions/product pages/publisher terminals; 75 A reads
and writes; 10,240 publisher issues/accepts/write responses; 524,288 B SPD
and external backing; and zero q-index backing, host payload, fallback, and
drain. Each arm also has cache-on 32 active owners and no value stalls.

| arm | `simTicks` | INDRMW cycles | request cycles | lane high-water | context stalls |
|---|---:|---:|---:|---:|---:|
| lane 1 | 420,805,651 | 321,121 | 422,695 | 10 / 10 = 1 | 5,098 |
| lane 4 | 399,433,385 | 254,883 | 355,694 | 40 / 10 = 4 | 1,608 |

Lane 4 is 1.053506× faster (5.08% fewer ticks) and reaches the required
four-lane high-water. It is nevertheless **rejected** because
`IND_SoaJitContextStalls` is a retained-value ledger and differs across arms.
The policy requires every p-gather/product/q/A/value/publisher/page-fed ledger
to be exact; a faster result cannot override that violation. The runner’s
first post-run attempt also exposed that this counter was not returned by the
inherited parser. The committed correction explicitly parses every required
conserved counter, making this mismatch fail closed rather than silently
omitting it.

No `CG_NA=1024` confirmation was launched: the required exact-faster gate is
not satisfied. The r2 raw artifacts are retained at the root above; primary
restore-log SHA-256 values are `f2763734a4a1fff24e05bf60fbfbbbed7b9015a749f0364da958585aee7466db`
(lane 1) and `ac55b6a668753336bdbd592de6abd78cac7a6b652dcd8eea75decab72a38f94c`
(lane 4). This is a bounded rejected screen, not a performance, native, full,
or p-stage timing claim.
