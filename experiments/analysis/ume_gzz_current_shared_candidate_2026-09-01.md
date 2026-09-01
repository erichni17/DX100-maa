# UME GZZ current shared-payload candidate

## Decision and scope

Accept this run as candidate-only correctness and mechanism evidence. Do not
promote it as a performance result: the current candidate takes 42,346,396
`simTicks`, 66.257% more than the sealed r6 strict observation. No current
native baseline was simulated, so comparisons to r6 are historical
cross-binary comparisons or orientation only, not fresh-baseline attribution.

This is the deterministic reduced GZZ input (`n=16,384`, 196,384 padded output
elements), not the separate full-scale application. The campaign built the
same non-ping-pong seven-tile MAA DIV/MUL page consumer as the matched-consumer
matrix and simulated exactly one `strict_bounded_hybrid` arm with no timeout.
It did not build or run a native arm.

Evidence root:
`/data1/nier/dx100-runs/2026-09-01-ume-gzz-current-shared-candidate-r1`

## Exact closure

- Output fingerprint: `7602200327591349891`; nonfinite count zero.
- Scalar reference: zero volume errors and zero gradient errors across all
  196,384 output elements.
- Guest marker: `mode=maa_div_mul physical_tiles_per_core=7 pingpong=0
  cpu_spd_payload_reads=0`.
- Semantic mechanism: 5 indirect reads, 8 indirect RMWs, 16,384 virtual index
  words, one strict operation, 16,384 strict descriptors, and four pages ready.
- Admission closed with 16,384 B words and descriptors and zero early A issues;
  all 1,025 A issues received responses.
- Backing closure: 1,024 issues = 1,024 write completions = 1,024 trace ACKs.
  All were full-line writes; partial writes were zero. Semantic and transport
  bytes both equal 65,536.
- Payload closure: 1,024 starts = 1,024 completions; 16,384 scheduled words =
  16,384 read words.
- Shared-payload terminal event: capacity 4,096 words, high water 3,484,
  16,384 transfers, zero rollbacks, and `line_shadow_bytes=0`.
- Wrapper return codes are zero, both recorded PIDs have matching start-time
  identities and are absent, the restore has one terminal `m5_exit`, final
  stats are nonempty, and the checkpoint identity remained unchanged.

## Timing

| Reference | `simTicks` | Relationship to current candidate |
|---|---:|---|
| frozen r6 matched native16 | 20,546,885 | orientation: current is 2.06096x its latency |
| sealed r6 strict | 25,470,375 | historical strict: current is 1.66257x its latency (+66.257%) |
| frozen r6 matched native4 | 29,755,345 | orientation: current is 1.42315x its latency |
| current strict candidate | 42,346,396 | one current-source observation |

Equivalently, the frozen native16/current and native4/current orientation
ratios are 0.485210x and 0.702665x. The r6-strict/current ratio is 0.601477x.
These ratios do not isolate a treatment: the candidate uses a different gem5
binary and source state, and no same-binary current native or r6-strict replay
was requested or run. The evidence therefore supports exact current-source
functionality and exposes an adverse timing orientation, but it cannot assign
the delta to fanout scan deferral, unified payload storage, or any other
individual change.

## Provenance

- Current gem5 SHA-256:
  `45206b3433449e10b26bbd8ff32281c06e533c101213097a27d50c364ca3c267`.
- Simulator treatment source commit: `dffa5573`; the build worktree later
  advanced to unrelated SSSP commit `545baa0b`, while every GZZ/MAA treatment
  file remained byte-identical to `dffa5573`.
- Guest SHA-256:
  `289954b0668d1b15274dda1944a7de2ba169508251a9d47b1c67bb90d0198647`.
- Ramulator library/config SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753` /
  `aca6e27b58afdfbfd80b7ec41c3f0e7e574a1fc7355a3512981ead823f68731b`.
- Sealed r6 result SHA-256:
  `339081288531cbc4d2dd10c4ff6b4c3cbd1e9c6ad9c4b89b9da6073d0c88be47`.
- The manifest records and the artifact ledger seals snapshots and SHA-256s
  for all 14 treatment sources. This explicitly includes
  `VirtualSourceFanout.hh` (`a5c779bc...`),
  `VirtualResponsePayloadStore.hh` (`ec396606...`), and
  `VirtualCombinePayloadStore.hh` (`5dd77345...`), plus `IndirectAccess.cc/.hh`,
  `MAA.cc/.hh/.py`, the GZZ/API sources, and gem5 configuration sources.
- Manifest contract: `arms=[strict_bounded_hybrid]`, `native_simulations=0`,
  and `timeout=none`. The only arm and checkpoint directories have the same
  strict name.

The simulation completed once. Its first post-run classification stopped
before sealing because the runner omitted the explicit `pingpong=0` token from
its otherwise-correct marker check. The fail-closed `seal` path then classified
and ledger-sealed that same completed restore after the check was corrected;
no second checkpoint, restore, or native simulation was launched.

## Phase-level regression gate

The read-only comparator
`experiments/scripts/compare_gzz_shared_payload_phases.py` classifies the
current/historical difference as `SOURCE_MLP_COLLAPSE`. It launches no
simulation and keeps the comparison explicitly cross-binary and
non-attributable. B fetch is unchanged at 7,120 cycles and consumer time is
22,952 versus 22,949 cycles. In contrast:

| Metric | sealed r6 strict | current strict | Ratio |
|---|---:|---:|---:|
| A issue cycles | 7,154 | 60,978 | 8.524x |
| Backing cycles | 6,817 | 49,629 | 7.280x |
| Response-slot high water | 128 | 1 | 0.0078x |
| Build rounds | 59 | 1,029 | 17.441x |

The result is sealed at
`/data1/nier/dx100-runs/2026-09-01-gzz-shared-phase-comparison-r1/comparison.json`
with SHA-256
`4c20c362fd1b74c15e603e3d50a70a389be5cb1929b0d46de51b46076e083d39`.
This localizes the regression to lost source-line concurrency and backing
overlap; it does not claim the historical hidden-storage implementation is a
legal design or that one individual commit is solely responsible.
