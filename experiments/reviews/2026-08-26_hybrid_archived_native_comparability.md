# Archived native versus full-hybrid comparability audit (2026-08-26)

## Verdict

**REJECT: no existing native16/native4 timing is comparable to the supplied
full-hybrid IS, HashJoin PRO, or HashJoin PRH roots.**  This is deliberately a
fail-closed provenance result.  No speedup, slowdown percentage, ratio, or
derived arithmetic is reported: every proposed pair fails a comparability
prerequisite before performance arithmetic is permitted.  The selected
HashJoin hardened successors themselves pass their terminal/correctness gates;
they are not rejected as incomplete.

The frozen physical-tile index is authoritative for locating the prior native
arms, but is not itself a proof that a later executable, input, ROI, and
treatment are identical.  Its policy permits reuse only when those identities
match.

## Evidence roots and gate result

| Workload | Full hybrid / certificate root | Archived native16 root | Archived native4 root | Decision |
|---|---|---|---|---|
| NAS IS full class | `2026-08-26-is-scalar-soa-full-certificate-r1`, raw `2026-08-24-is-scalar-soa-full-a44aaa60-r5` | `2026-07-20-full-tile-sweep/is_recovery2/t16384_gem5.opt.ovl_base` | `2026-07-20-full-tile-sweep/final-recovery/is/t4096_gem5.opt.ovl_base_sha256_bcc30842...` | Reject: executable/source cohort differs; native input identity is not captured. |
| HashJoin PRO 2M/2M | `2026-08-24-hashjoin-pro-hardened-r1/PRO` | `2026-07-11_hashjoin_tile_smoke/PRO_r2000000_s2000000_t16384_m2GB_gem5.opt.ovl_base` | `2026-07-11_hashjoin_tile_smoke/PRO_r2000000_s2000000_t4096_m2GB_gem5.opt.ovl_base` | Reject: native input/source/guest provenance is insufficient to prove a treatment-only pair. |
| HashJoin PRH 2M/2M | `2026-08-24-hashjoin-prh-hardened-r1/PRH` | `2026-07-10_hashjoin_tile_smoke/PRH_r2000000_s2000000_t16384_m2GB_gem5.opt.ovl_base` | `2026-07-10_hashjoin_tile_smoke/PRH_r2000000_s2000000_t4096_m2GB_gem5.opt.ovl_base` | Reject: native input/source/guest provenance is insufficient to prove a treatment-only pair. |

All paths above are rooted at `/data1/nier/dx100-runs` except the HashJoin
archive roots, which are under `/data1/nier/DX100/experiments/campaigns`.
They are the paths recorded in
`monitor-3h/reports/20260822_092051/tile_sweep_source.tsv` (SHA-256
`e870eba1...0142cd`) and its frozen baseline descriptor
`physical_tile_sweep_baseline_20260822.json` (SHA-256
`d8cd2afe...73d069`).

## NAS IS: exact failure evidence

The certificate accepts full-IS correctness only: it points to raw root
`2026-08-24-is-scalar-soa-full-a44aaa60-r5`, records terminal `PASS`, official
NAS verification, and `simTicks=379831843258`; it explicitly says
`native_rerun=false` and `performance_promoted=false`.

* Full input is the captured `key_array_4C.h`: SHA-256
  `b70a33ed...cf75479`, 421,494,742 bytes.  The full manifest records that
  hash.  The native16/native4 command lines name their guests but do not record
  an input path, hash, or size.  The current historical source includes
  `key_array_4C.h`, but that observation cannot retroactively bind the archived
  runs to the full input hash.
* Full source is `is.cpp` at commit `f7d268f...9b68b`, SHA-256
  `5d9af5cc...7c26510`, 46,783 bytes.  The historical native-source file now
  hashes `497c540a...c550ac`, 42,910 bytes.  Thus source identity is unequal.
* Full guest is SHA-256 `c76e84ca...4a1ff1`, 134,257,352 bytes.  The present
  historical native16/native4 guests hash respectively `2ba16480...e9ae6`
  (134,486,584 bytes) and `b9d25802...09136` (134,484,832 bytes).  These are
  unequal executable identities, not a treatment-only change.
* Full gem5 is archived and bound as SHA-256 `2d02fa40...86152`, 876,002,040
  bytes.  Both native IS arms bind `gem5.opt.ovl_base` SHA-256
  `bcc30842...c18ab`, 863,591,000 bytes.  This is a second non-treatment
  executable mismatch.
* Completion/correctness is asymmetric: the full root has `PASS` and
  `IS_VERIFY passed=6 expected=6 result=PASS`; native16 has that same terminal
  verification marker and `m5_exit`; native4 has only the anchor exit policy
  and `m5_exit`, with no captured `IS_VERIFY` result.  Native4 therefore cannot
  establish the required exact correctness identity on its own.

The shared command-level platform fields are not enough to repair those
failures: four X86O3 cores, 3.2-GHz system/CPU clocks, 32-KiB/8-way L1s,
256-KiB/4-way L2s, 8-MiB/16-way L3 with four ports, 64-byte lines, two
Ramulator2 channels, and the same 380-byte RAMulator YAML
(`aca6e27b...68731b`) are recorded.  The treatment geometry is nevertheless
not ordinary native equivalence: full IS records 16,384 logical elements,
4,096 physical elements, four indirect units, 32 row-table slices, and a
524,288-byte physical SPD payload.  Native16 records a 16,384-element tile;
native4 records a 4,096-element tile; neither archived native config carries
the full root's logical/physical split.  This is not a narrowly isolated,
same-binary treatment comparison.

## HashJoin PRO and PRH: selected hardened successors

The selected full roots are the hardened successors, not their historical
underlying wrappers.  Their manifests bind the exact generated input
`r_size=2000000,s_size=2000000,r_seed=12345,s_seed=54321,non_unique=0,full_range=0`,
their source identities, guest/gem5 hashes, checkpoint paths, geometry, and a
raw hash ledger.  `gate.complete` says `terminal=pass` for each kernel.  The
one-shot classifier reports both roots `terminal-valid`, exact terminal
correctness `pass`, and expected coverage: PRO first-pass `routed` with shifted
pass `not_applicable`; PRH first-pass `routed` with shifted pass `tail_only`.

The archive does locate ordinary 2M/2M endpoints and their `m5_exit`/stats:
PRO native16/native4 report `simTicks` 24,114,794,223 / 24,558,534,636 and PRH
native16/native4 report 42,084,947,726 / 42,868,174,199.  These are retained
raw observations only, not comparisons to the hardened roots.

* The archived native commands specify `-n 4 -r 2000000 -s 2000000` but do not
  capture the seed flags, a generated-relation fingerprint, or an input ledger.
  The current historical `main.c` does default those omitted flags to
  `12345`/`54321` and hashes identically to the hardened source file; that is
  supporting context, not immutable proof that the archived guests consumed
  the hardened manifest's exact generated relations.  Exact input equivalence
  is therefore **unverified**, not asserted to be a seed mismatch.
* The archived native guests are `hj_maa_16K`/`hj_maa_4K`, with current hashes
  `de290d71...5239d` / `6f5df7d8...8403e` and 345,952 bytes each.  Hardened PRO
  binds source commit `2570bae...f6109`, fingerprint `7b8d8bdc...901d3`, and
  guest `d81880c8...936c6e`; hardened PRH binds `792387b...7b0c8`,
  `a06cbf3c...84948`, and `8c304ec8...6aae7`.  The ordinary archive has no
  immutable source/binary manifest that proves these guest differences contain
  only the intended treatment.
* Full HashJoin uses archived gem5 SHA-256 `2d02fa40...86152`; the native
  archive uses `bcc30842...c18ab`.  The command-level cache/DRAM geometry
  otherwise agrees: four X86O3 cores at 3.2 GHz, L1 32 KiB/8-way,
  L2 256 KiB/4-way, L3 8 MiB/16-way/four ports, 64-byte lines, Ramulator2,
  two channels, and the same YAML hash `aca6e27b...68731b`.
* Hardened configurations add the material hybrid geometry: 16,384 logical
  elements, 4,096 physical elements, four indirect units, and 32 row-table
  slices.  Native16/native4 instead select a single 16,384/4,096 tile and
  have no physical-tile field.  This is not treatment-only with the recorded
  guest and seed differences still open.
* Hardened raw logs contain `HASHJOIN_HYBRID_RESULT result=2000000`, routed
  first-pass windows, and `m5_exit`; their result rows are PRO
  `2000000/240/240/240/28733719886` and PRH
  `2000000/240/240/240/46317022917` for
  result/routed/instructions/terminals/simTicks.  The classifier's
  `performance_promotable=false` is a candidate-only/no-routed-shifted-window
  promotion limitation, not incomplete correctness.  Native logs still supply
  `m5_exit` but no captured exact result cardinality or fingerprint.  The
  hardened correctness gates pass; the native-to-hardened *pair* remains
  unproven because its input and treatment-only provenance gates fail.

## Handoff

Do not reuse these native endpoints for full-hybrid performance claims.  A
future comparison needs, per kernel and arm: immutable guest/source and gem5
hashes, a native generated-relation fingerprint bound to the fixed seed/size,
identical ROI/checkpoint and platform configuration, and exact terminal output
correctness.  Only then may a
same-workload comparison compute `simTicks` arithmetic; a backed16/backed4
matrix remains necessary if the requested causal claim is specifically the
physical-payload virtualization cost rather than an end-to-end candidate
observation.
