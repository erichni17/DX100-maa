# Hybrid SSSP S22 full-progress audit (fail closed)

Audit time: 2026-08-26 (read-only; no signal, attach, edit, or new gem5 run).

## Recommendation

**STOP / recover; do not call r2 complete and do not restart it as a continuation.**
The recorded wrapper and gem5 PIDs were observed with the exact requested root
and command, then both disappeared.  The retained log has no exit status,
`m5_exit`, ROI end, hybrid terminal record, oracle output, or nonempty final
stats.  It therefore cannot establish completion or correctness, whatever the
reason for disappearance.  The marker trace was healthy-looking up to its last
flush, but that is not a terminal result.

## Active-root identity and evidence

Root: `/data1/nier/dx100-runs/2026-08-25-sssp-coherent-full-s22-r2`

* Observed before disappearance: wrapper PID `2635394`, command
  `run_sssp_old_result_hybrid_full.sh ROOT`; gem5 PID `2637298`, exactly using
  `ROOT/run`, `ROOT/checkpoint`, and `ROOT/bin/sssp_maa_2G_old_result_hybrid_fp`.
  A later read found both `/proc` entries absent.  User-service inspection was
  unavailable to this session (`systemctl --user` could not connect to that
  user's bus), so no unit/cgroup identity is claimed beyond that PID/command
  evidence.
* Candidate provenance: source commit `e152d6922e48ca0342f170e3e73f267d297c315d`;
  `sssp.cc` SHA-256
  `07b8a02cc96ef8bf42ab2c9622de8da7c99efc8b2fdac257ef355168dbadd116`;
  guest SHA-256
  `3719bf7812a67681c8087887af306ab66c813da77e75678e3d818406c7d4fa17`;
  gem5 SHA-256
  `1e079112469892681d661925db09ccfbc845d1a2ce45c79e1d9a4902c19a9863`.
* Input is the full S22 graph, SHA-256
  `23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc`
  (1,090,514,493 bytes), matching the frozen external reference.  It specifies
  4,194,304 vertices, 134,217,158 directed edges, source 2,796,003, and the
  expected PASS fingerprint in `external_reference.manifest`.
* Command options are logical 16K / physical 4K, four indirect units, 32 row
  slices, densest old-result policy, partial credits 4, active contexts 8,
  value cache + pre-A lookahead enabled, zero value-prefetch credits, 64 active
  value owners, and one apply lane.  The checkpoint exited cleanly at tick
  `11619954500`; restore switched CPUs at `11619964500`, began ROI at
  `11619965057`, and reports a host tick rate of `10^12` ticks/s.
* Immutable-file hashes: `candidate.manifest`
  `4deea5dea450cb0bb710e483f391cd0b354a6cae5331130dac9e1a28c8ee9082`;
  `external_reference.manifest`
  `5b836a16e885eb7216cb3b7b7c98f700f93da4988da6e63af7bdd217bb852b2f`;
  `run/command` `19080feea26db8d9c42959e6424db5c7273472c76c53956917b006d8d6375632`;
  retained `run/restore.log`
  `a2e2ce57a944829ab48ea3fa65fcf1d3b793cfd55450d4308d33fd3d2ff79cf2`.

## Progress and prior failure boundaries

`run/restore.log` contains 107 `Starting DeltaStepMAA` markers.  The first 75
are base work; marker 76 is 4,133 elements in `maa-1024`; markers 82--87 are
`maa-2048`; markers 88--107 are `maa-4096`.  The final retained frontier is
103,006.  Within markers 76--107, frontiers rise strictly from 4,133 to
103,006 (4,800, 5,380, 6,012, ..., 96,044, 103,006): no repeated-state or
shrinking-frontier signature is present in that observable window.

Two retained full-graph failures share the same input/options geometry and
both fail at the first 1K tiled boundary:

| Root and retained log | Source / guest | Last marker | Terminal evidence |
| --- | --- | ---: | --- |
| `/data1/nier/dx100-runs/2026-08-24-sssp-old-result-full-e690867f-r1/run/restore.log` (SHA-256 `fefb97aa3363b9334437c58d6d781528f002fc968a841c0ec1a768c633386f88`) | `sssp.cc` `71cef23d49cba69b15d9dc9747822e14ef823d687fd8865574fa4528a62ec4f1`; guest `8992340c6a39738c66227e58b22dca1c55e61bd0d2170ac86860d1938c6a490f` | 76, 4,133, `maa-1024` | exit 134; panic: SPD element 4096 exceeds physical capacity 4096 |
| `/data1/nier/dx100-runs/2026-08-24-sssp-aperture-full-s22-r1/run/restore.log` (SHA-256 `830694c4a48b8da3043e0fa009929266d86f242a43c9885f241d8a4b3d523da7`) | same source hash; guest `b92252492af0fbae8b3a27d2e57d403cbbc2f03b830090ae767f50cac8904c3c` | 76, 4,132, `maa-1024` | exit 134; panic: physical-out-of-range aperture access at tile 28, offset 16384 |
| `/data1/nier/dx100-runs/2026-08-25-sssp-coherent-full-s22-r2/run/restore.log` | `sssp.cc` `07b8a02cc96ef8bf42ab2c9622de8da7c99efc8b2fdac257ef355168dbadd116`; guest `3719bf7812a67681c8087887af306ab66c813da77e75678e3d818406c7d4fa17` | 107, 103,006, `maa-4096` | process gone; no terminal evidence |

The r2 marker text through marker 76 exactly matches the `e690867f` failure,
then continues through both the 2K and 4K phases.  It has therefore passed all
known explicit SPD/aperture failure boundaries.  This supports the conclusion
that marker 107's growing frontier was expected convergence progress, not the
previous immediate bounds failure.  It does **not** prove continued liveness,
completion, or output correctness.

Other located full-S22 hybrid roots are not usable success/failure evidence:

* `2026-08-24-sssp-old-result-full-s22-2840d930-r2` intentionally stopped as a
  duplicate (`wrapper.status`: terminal=false, accepted=false), before ROI.
* `2026-08-24-sssp-old-result-full-s22-aa41bdd7-r1` is superseded and has only
  a zero-byte observed-exit record.
* `2026-08-25-sssp-coherent-full-s22-r1` is superseded during checkpoint
  generation; its restore log reaches setup only, with zero final stats.

No per-marker simulated tick, bucket index, or relaxation/work counter is
logged.  Thus there is no tick at either marker-76 failure and no tick at r2
marker 107.  The startup/checkpoint ticks above are the only usable r2 tick
values; all three incomplete/failing restore `stats.txt` files are zero bytes.

## Authoritative native tile evidence

The frozen manifest
`/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/experiments/analysis/physical_tile_sweep_baseline_20260822.json`
(SHA-256 `d8cd2afe18de4f7983b1d9d59a0ea04e102a51bc7146a9d85c3c9a19cc73d069`)
names the authoritative S22 physical sweep report
`/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/monitor-3h/reports/20260822_092051/tile_sweep_source.tsv`
(SHA-256 `e870eba1f74cd37c2f58695ef7ac6a5778ab030e635f55e0ce1464d72f0142cd`).
Its SSSP rows record the same PASS fingerprint for all seven tiles:

| Native physical tile | simTicks | Aggregate status |
| ---: | ---: | --- |
| 1K | 1,200,433,049,367 | valid, rc 0 |
| 2K | 986,124,368,544 | valid, rc 0 |
| 4K | 799,456,471,390 | valid, rc 0 |
| 8K | 795,712,908,445 | valid, rc 0 |
| 16K | 758,524,789,379 | valid, rc 0 |
| 32K | 764,785,006,914 | valid, rc 0 |
| 64K | 761,125,332,038 | valid, ROI-complete anchored, rc 143 |

The authoritative native4 raw log is
`/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/repair3-validation/gapbs/sssp_s22_t4096_m2GB_gem5.opt.ovl_base_sha256_1ff4a396b98d6c838f695c4cbd631ca16e7ed12407365f17707bcf6df93e1343/run.log`
(SHA-256 `66266052bbc6d1f32f0acccb36a489c82a5d7ada72d54f65fcc23330ea278771`):
364 markers, PASS fingerprint, and m5_exit at tick `2794427057862`.
The native16 reference is
`/data1/nier/dx100-runs/2026-07-20-full-tile-sweep/repair3-validation/gapbs/sssp_s22_t16384_m2GB_gem5.opt.ovl_base_sha256_1ff4a396b98d6c838f695c4cbd631ca16e7ed12407365f17707bcf6df93e1343/run.log`
(SHA-256 `20012684fa3cd2a4d6e6d75ecdb05f82ad818a3315e69afdd18b6c4a6f6798b7`):
365 markers, PASS fingerprint, and m5_exit at tick `2753688698110`.

At native4 markers 96--107, the frontier follows the same rising 4K-phase
shape (39,268 to 103,020) as r2 (39,264 to 103,006), then completes.  It is a
useful convergence-shape control only: its source/binary cohort (`1ff4a396...`)
is not r2's, so neither its 364-marker total nor its ticks is a valid r2
remaining-work or performance estimate.

## Required future instrumentation

Before any future rerun, add a flushed, machine-readable progress record at
each DeltaStep marker in `benchmarks/gapbs/src/sssp.cc` (the current prints are
at lines 596 and 673): monotonic marker number, `curTick`, `curr_bin_index`,
frontier size, selected tile, cumulative relaxation/insert counts, and a
state fingerprint.  Have the wrapper atomically write PID start-time,
service/cgroup identity, final exit/signal, and last progress record; retain a
periodic stats snapshot.  This would distinguish an external interruption
from a liveness failure and make any remaining-work estimate auditable.
