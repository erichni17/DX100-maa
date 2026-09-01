# SSSP locality-matched three-arm micro (2026-09-01)

## Decision

**Do not launch full S22 from this mechanism.** The locality construction is
effective enough to make native16 1.087764704x faster than native4, but the
current T16/P4 hybrid is only 0.078222740x as fast as native4 (12.784006x
slower). It is 13.905991x slower than native16. All arms are exactly correct,
so this is a performance/mechanism rejection rather than a functional failure.

This was one exploratory replica per arm. It is sufficient to reject the
predeclared launch screen because the hybrid misses the 1.05x native4 threshold
by more than an order of magnitude and its DRAM mechanism signature is wrong.
It is not a variability estimate or a general architecture claim.

## Input and prediction

The directed graph has 69,633 vertices and 69,632 edges: 4,096 edges from the
source to middle vertices, then 16 unique leaf edges per middle vertex. For the
65,536 leaf-edge ordinals, the destination permutation is

`p(e) = 16 * (e mod 4096) + floor(e / 4096)`.

It is a bijection. Every 4K edge tile touches one word in each of 4,096 cache
lines, while every 16K tile covers four words in those same 4,096 lines. Thus
all arms perform the same 65,536 leaf relaxations, but only the 16K orderings
can coalesce across four globally interleaved 4K groups.

The immutable prediction was written at `2026-09-01T03:43:55Z`, before any
checkpoint or restore. It predicted unchanged semantic work and fingerprint,
lower line/row/ACT/PRE routing for native16, and a hybrid that retained 16K
routing locality while adding publisher and old-result writes. The launch
screen required exact correctness and closure, at least 1.05x speedup over
native4 for both native16 and hybrid, and the predicted routing direction.

## Results

| Arm | Geometry | `simTicks` | Speedup vs native4 | MAA cycles | Cache lines | Rows | DRAM RD / ACT / PRE |
|---|---|---:|---:|---:|---:|---:|---:|
| native4 | T4/P4 | 672,489,890 | 1.000000000x | 2,148,530 | 345,420 | 43,416 | 30,499 / 814 / 150 |
| native16 | T16/P16 | 618,231,027 | 1.087764704x | 1,975,179 | 148,768 | 18,696 | 30,482 / 843 / 213 |
| hybrid | T16/P4 | 8,597,114,973 | 0.078222740x | 27,466,821 | 230,733 | 29,024 | 36,890 / 1,623 / 155 |

Native16 reduces `simTicks` by 8.068354%, line insertions by 56.931272%, and
row insertions by 56.937535% relative to native4. This validates the intended
ordering opportunity, although ACT and PRE rise by 3.562654% and 42.0%; the
full predicted DRAM direction therefore does not hold.

The hybrid reduces generic line and row insertions by 33.202189% and 33.149069%
relative to native4, so some 16K routing benefit survives. It nevertheless
adds 20.954785% DRAM reads and 99.385749% ACTs, and its request cycles rise to
729,486. The retained locality is overwhelmed by the current producer,
old-result, and response-bearing write machinery.

## Routing and write closure

Native arms activate no SoA/JIT or publisher counter. The hybrid reports:

- 4 routed SoA/JIT instructions, 65,536 selected words, zero predicate
  rejections, and 65,536 old-result captures;
- 16,385 A reads and responses, paired with exactly 16,385 A writes and
  responses;
- 15,633 old-result write issues and 15,633 responses; and
- 8,192 publisher issues, 8,192 publisher write responses, and 32 publisher
  terminals (16 index plus 16 value pages).

The guest terminal closes at 4 eligible/routed windows, zero unsafe or fallback
windows, 65,536 old-result words, zero legacy words, zero host-SPD reads, zero
illegal apertures, zero hidden/dedicated payload, `response_closure=1`, and
`counts_close=1`.

Ramulator suppresses its zero-valued `WR` command statistic, so the result table
records DRAM WR as zero only after confirming the field is absent in all three
terminal logs. This does not mean the hybrid issued no MAA writes; the explicit
response-bearing counters above are the authoritative write ledger.

## Correctness

Every arm emits exactly once:

`SSSP_FINGERPRINT vertices=69633 reached=69633 unreachable=0 distance_sum=135168 max_distance=2 hash_a=a0531a7ddb9387df hash_b=39f1ea63bc8817e8 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS`

All checkpoint and restore wrappers return zero, every restore has one
`m5_exit`, final nonempty stats, and no panic/fatal/assert/abort/error marker.
The graph SHA-256 is
`902d3b2dfceddc44a354ce2f7a9a3d572327c2c2fc7ff99190baff74d059c3e3`;
this binds the exact permuted topology even though the distance-vector
fingerprint intentionally matches the accepted sequential-leaf graph.

## Provenance

Raw immutable evidence is under
`/data1/nier/worktrees/codex-coordination/sessions/sssp-locality-matched-micro-20260901-20260831-225546-d4d67a8b/evidence/sssp-locality-matched-micro-r1/campaign`.
The manifest binds:

- source/runner commit
  `2d8d67ba2c0b17bc17a26d5bdf95de44ce71211c`;
- gem5 SHA-256
  `45206b3433449e10b26bbd8ff32281c06e533c101213097a27d50c364ca3c267`;
- Ramulator library SHA-256
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`;
- graph/WEL and all three guest hashes;
- the accepted all-safe cache/memory surface: four O3 CPUs, 32-KiB L1s,
  256-KiB L2, 8-MiB/16-way/4-port L3, 64-byte lines, Ramulator2 with two
  channels, four indirect units, eight tiles/core, and 32 RowTable slices; and
- `full_app_runs=0`, `external_native_baseline_reruns=0`, one replica, and no
  wall timeout.

Checkpoint identities are:

- native4:
  `d11956775ead7d18fbbcd165cb2c948c80b9b7823ca6a3451831ff01195228f6`;
- native16:
  `da0fde7f6f2c88481100136d041be8904679cba70ec4a09f364bccd3b1970e1b`;
- hybrid:
  `4949120444e4ebb75825101660be2590db6712ec86c0af7cc6f235b2fc9a4323`.

The original simulator driver completed all three restores, then returned 2
during report extraction because it required Ramulator's suppressed zero WR
field. `postprocess.recovery.txt` preserves that failure and
`postprocess.latest.txt` hashes the corrected analyzer. Postprocessing launched
zero gem5/checkpoint runs. The final terminal record is `PASS` with driver RC 0,
the before/after artifact manifests compare byte-for-byte, the compact evidence
identity verifies, and `gate.complete` exists.

## Handoff

The micro supports the premise that a 16K ordering window can exploit the
constructed locality (native16 wins), but it rejects the current T16/P4 hybrid
as the vehicle for a full S22 launch. Improve or replace the hybrid
publisher/old-result path and repeat a bounded micro screen before spending a
full-application run.
