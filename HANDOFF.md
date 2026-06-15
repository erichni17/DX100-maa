# DX100 (`dx100-improvements`) — Handoff

This branch takes the ISCA'25 DX100/MAA artifact from **not running** on a modern,
RAM-constrained, non-Docker host to **runnable, correctness-verified, deterministic, and
characterized**, plus a behavior-preserving optimization. Full chronological detail and the
reasoning behind every change is in [`IMPROVEMENT_LOG.md`](./IMPROVEMENT_LOG.md).

## TL;DR — what changed vs upstream (`e4fc4af`)

**Runnability** (modern toolchain + ~17 GB shared host, no Docker):
- SCons 4.9+ `CheckLibWithHeader` keyword fix — the build was fully broken otherwise.
- x86-64-v2 CPUID (`X86ISA.py`) so modern-glibc guest binaries don't abort at startup.
- `Simulation.py` fast-forward tuple-unpack fix; MAA-region NULL guard (`MAA.cc`).
- **`MAX_CMD_REGIONS` 256→32** (`src/mem/packet.hh`) — fixes a ~10 GB stats-registration OOM
  at 4 cores (per-region cache stats were allocated eagerly for all 256 regions per cache).
- ROI must run on **`X86O3CPU`** — the `m5 add/clear_mem_region` pseudo-ops `static_cast` the
  CPU to `o3::CPU`, so TimingSimpleCPU segfaults.
- Microbench built **without OpenMP** + `-DMAA_MEM_SIZE=0x40000000` so the run fits ~5 GB RSS.

**Improvement:**
- **`RequestTable` O(num_addresses)→O(1)** (`Tables.{hh,cc}`) — hash map + free-slot stack;
  behavior-preserving (byte-identical stats), validated across all op types.

**Bug fix (Ramulator2 controller — silent request drop / livelock):**
- `ext/ramulator2/ramulator2/src/dram_controller/impl/generic_dram_controller.cpp`: when a
  request issued its opening (ACT) command, `m_active_buffer.enqueue()`'s return value was
  ignored and the request removed from the read buffer unconditionally. With
  `active_buffer.max_size == queue_size`, a shallow queue (≤ 2) overflows the active buffer and
  the request is **silently dropped** — never completes, hanging the requestor (observed as the
  MAA reorder-ON livelock at `queue_size ≤ 2`). Fix: only retire the request on a successful
  `enqueue()`. **Byte-identical on realistic configs** (active buffer never fills at default
  queue=32 ≥ #banks); resolves the hang across queue∈{1,2}×n∈{200,1k,4k}. Rebuild
  `libramulator.so` only. See log for the trace-level diagnosis.
- Follow-up (root mis-sizing): the active buffer is also re-sized in `setup()` to
  `max(queue_size, banks_per_channel)` — its true bound is one open row per bank (16 here),
  independent of `queue_size` — so the overflow is now structurally impossible and the guard is a
  backstop. No-op at default (16 < 32). Verify both with `bash test_fix.sh` (deadlock
  reproducers + byte-identical regression suite).
- Real-kernel demo: `bash bfs_run.sh 16` runs **GAP BFS** (a real graph kernel) end-to-end
  through gem5 on the fixed MAA at a host-fitting scale (2^16-node toy graph, 1 GB MAA region,
  4 cores) — 1157 MAA instructions incl. 454 indirect-read gathers, clean exit. Correctness via
  the functional build (`-DFUNC … -v` → `Verification: PASS`). Not artifact-scale (that needs a
  ≥40 GB box + ~20 GB datasets + days); a plumbing proof that a real benchmark drives the fix.

## Build (constrained host, no Docker)
```bash
# 1. Ramulator2 (produces ext/ramulator2/ramulator2/libramulator.so) — g++-11 OK
cd ext/ramulator2/ramulator2 && mkdir -p build && cd build && cmake .. && make -j4 && cp libramulator.so ../ && cd -
# 2. gem5 (keep -j4 on a shared host; editing src/mem/packet.hh forces a ~30min full rebuild)
scons build/X86/gem5.opt -j4
```

## Run the validated test loop
```bash
bash run_test.sh <outdir> MAA gather allhit 20000      # or:  gather "allmiss 1 100 1 1"
```
- 2-step: AtomicSimpleCPU checkpoint at the ROI (cached under `ckpt_cache/`) → **X86O3CPU +
  `--maa`** restore+run. ~5 GB RSS, ~2 min. Prints **"End of Test, all tests correct!"** and
  writes `<outdir>/stats.txt`.
- `run_test.sh <outdir> MAA <kernel> "<dist args>" <n> "<extra gem5 flags>"` — the optional
  6th arg appends/overrides flags (used by the design-space sweeps below).
- Compare two runs: `bash compare_stats.sh <ref-stats> <new-stats>` — diffs ignoring
  wall-clock fields. gem5 is deterministic here, so an unchanged model ⇒ **IDENTICAL**.

## Regression suite
`baselines/` holds reference `stats.txt` for `gather` (allhit, allmiss), `scatter`, `rmw`.
Key baseline (gather allhit): `maa.cycles=6509`, `switch_cpus0.ipc=1.34`, all-correct.

## Architectural findings (see log for the experiments)

> **⚠️ SUPERSEDED in part — see T-B gather sweep below.** The "reordering ≈ 1%, redundant,
> no accelerator-side optimization helps" claim below was measured **only at the tame
> `allmiss 1 100 1 1` index pattern = 100% row-buffer hit**, where reordering by definition has
> nothing to recover. Sweeping row-buffer-hit% (`reorder_dist_sweep.sh`) shows reordering is
> worth **up to 2.77× on scattered indices** — it is the single most valuable accelerator-side
> lever, not redundant. Read the original text below as "true at ROH=100 only."

The MAA indirect **gather is DRAM-bandwidth-bound** — channel scaling gives 4× channels →
3.2× faster (≈93% of 2-channel DDR4-3200 peak active-phase). At the **100%-row-buffer-hit index
pattern only**, its throughput is insensitive to row-buffer-locality optimization from either
side: MAA reordering on/off ≈ 1%, row-table capacity 8→64 flat, controller FRFCFS queue
32→**1** flat (only `MemLat` moves, 277→66), and shrinking the problem n=20000→200 keeps the
MAA at **MLP ≈ 53–76** (a high-MLP engine by design — can't be starved into latency-bound).
The row table's **reordering** is redundant *at that pattern* (but decisive on scattered ones —
see T-B). (Reorder ON used to **livelock at controller queue ≤ 2**, but that was a Ramulator2
active-buffer drop bug, now fixed — see the Bug fix above.) Its word→cache-line **coalescing**
is always useful. Sweep harnesses: `sweep_rt.sh` (capacity), `reorder_test.sh` (reorder, tame
pattern), `reorder_dist_sweep.sh` (**reorder × index-spread — the corrective experiment**),
`chan_sweep.sh` (channels), `queue_sweep.sh` (queue×reorder), `nsweep.sh` (problem size),
`latbound.sh` (queue 1/2/4).

## T-A — reorder-disable experiment scoping (INVESTIGATION ONLY — nothing applied/run)

Scoping for the professor's question "why does DX100 reordering show ~no end-to-end gain when
Ali got most of his benefit from it." Holding for Eric before the T-B A/B.

1. **Full-disable switch already exists: `--maa_no_reorder`** (`Options.py:227`). This is a real
   on/off switch, *distinct from shrinking the window* (`--maa_num_row_table_rows_per_slice`,
   `--maa_num_initial_row_table_slices`, …). Wiring: `MAAConfig._get_maa_opts` → `MAA.py:23` →
   `MAA.cc:59` `reorder_row_table = !no_reorder` → `IndirectAccessUnit::reorder_RT`. With
   `reorder_RT==false`: reads are issued the moment a CL is first touched, **in index/program
   order** (`IndirectAccess.cc:517-521`), and the **entire `Build` stage is skipped**
   (`Fill→Request`, not `Fill→Build→Request`, `:660-664`) — `Build` is exactly the row-table
   walk in DRAM-locality order. The row table is **still used for CL coalescing** (insert /
   `first_CL_access`); only DRAM-row *reordering* is removed. Clean ablation.
2. **No code change needed.** The A/B is already wired: `run_gem5_all.py:18-21` enumerates
   `do_reorder True/False`, `:270-271` emits `--maa_no_reorder` (same in `scripts/sim.py:244`).
3. **The microbenchmarks are Ali's, not ours.** Origin is `github.com/arkhadem/DX100`
   (**arkhadem = Alireza Khadem = "Ali"**); every kernel file (`benchmarks/API/test.cpp`,
   `MAA.hpp`, …) is from his initial artifact commit `a40792a` (2025-03-26). This branch touched
   only 2 benchmark files, only for runnability (region override, safer registration) — **zero new
   kernels.** `benchmarks/API/` holds his MAA **"micro kernels"** (`gather`, `scatter`, `rmw`,
   `gather_scatter`, `gather_rmw`, and `*_cond` / `*_rangeloop` variants, scalar + `_maa` pair) —
   this is what every sweep on disk ran (`gather, allmiss 1 100 1 1`). Other `benchmarks/` dirs are
   standard third-party suites (gapbs, spatter, NAS, hashjoin, UME).
4. **Bandwidth-bound = `gather` (allmiss, n=20000):** channel scaling ~linear
   (`chan_results.txt` 2→4→8 ch: 86236→50295→26861 cycles_INDRD, RD_BW 22.8→34.9 GB/s), and
   halving the controller queue halves MemLat but leaves cycles flat (`queue_results.txt`) →
   throughput-bound. **Latency-bound: none of the API kernels naturally** — gather stays high-MLP
   (~55-76) even at n=200; the latency regime had to be *forced* from the memory side
   (`queue_size=1`, `latbound_results.txt`). The structurally latency-bound real workload is
   **GAP BFS** (low-MLP frontier expansion), not yet quantified at scale.
5. **🚩 Flag for T-B:** existing data already shows reorder ON vs OFF on the bandwidth-bound gather
   = **86236 vs 85385** cycles_INDRD — OFF marginally *faster* (`reorder_results.txt`). That is the
   *opposite* of the professor's hypothesis (reorder should help bandwidth-bound most). Leading
   cause: `allmiss 1 100 1 1` is a low-spread index pattern with already-high row-buffer locality,
   leaving nothing for reordering to recover. **When T-B runs, sweep two index distributions** (the
   tame one AND a high-spread/sparse one) on both regimes — else we risk "proving" reorder is
   useless when the test pattern was just too easy.

## T-B — gather reorder × index-distribution sweep (EXECUTED — answers the core question)

`reorder_dist_sweep.sh` (n=20000, 2-ch DDR4). The prior reorder A/B used `allmiss 1 100 1 1`
= **100% row-buffer hit**, so reordering had nothing to recover and looked useless. Sweeping the
row-buffer-hit% (`allmiss BAH ROH CHH BGH`: `0 ROH 0 0` scatters across banks/channels/bankgroups)
shows reorder's value scales directly with how scattered the indices are. All runs verified
correct (`all tests correct`).

| index pattern | reorder | cycles_INDRD | MemLat | RD_BW | ON vs OFF |
|---|---|---|---|---|---|
| `1 100 1 1` (100% RB-hit, tame)  | ON / OFF | 86236 / 85385  | 326 / 326   | 22.9 / 23.0 | ON **1% slower** |
| `0 50 0 0` (50% RB-hit)          | ON / OFF | 248209 / 273868 | 460 / 516  | 12.0 / 11.1 | ON **10% faster** |
| `0 0 0 0` (0% RB-hit, scattered) | ON / OFF | 266140 / **737821** | 487 / **1327** | 11.4 / 4.9 | ON **2.77× faster** |

**Conclusion — the professor's hypothesis is confirmed; our earlier "reorder is redundant" was a
test-pattern artifact.** With reorder the MAA degrades *gracefully* as locality drops (86k→248k→266k,
stays bandwidth-bound); without it, scattered gathers fall off a cliff (85k→274k→**738k**, latency
explodes 4×, BW collapses to 4.9 GB/s). Reordering is precisely what keeps a scattered gather
bandwidth-bound instead of latency-bound — which is why Ali's real (sparse, low-locality) workloads
benefited from it. Reordering is the **single most valuable accelerator-side lever**, not redundant;
the prior conclusion only held at the 100%-row-buffer-hit corner. This also strengthens the BFS /
mbit10 case: real BFS frontiers gather scattered neighbor indices (low row-buffer locality), the
regime where reorder gives multiples.

## T-B — BFS reorder A/B (EXECUTED, scaled-down 2^16-node graph)

Ran reorder ON vs OFF (`--maa_no_reorder`) on the real GAP BFS at host-fitting scale, both
4-thread and a deterministic single-worker config. Harnesses: `bfs_reorder_ab.sh` (4-thread),
`bfs_reorder_ab_sc.sh` (single-worker).

1. **MLP gate (checked first): BFS at this scale is high-MLP (~40), NOT latency-bound** — same
   regime as the bandwidth-bound gather. So toy-scale BFS is *not* the latency-bound witness; the
   premise "reorder should pay off because latency is exposed" isn't established here.
2. **Reorder's mechanism is real but hidden:** ON cuts MAA avg DRAM load latency **~17%**
   (`AvgLoadsMemAccessingLatency` 379 vs 456) — the row-buffer-locality win — yet **end-to-end is a
   dead tie** (`simTicks` Δ 0.01%; `cycles_INDRD` Δ ≤0.6%) because MLP ~40 fully hides it. Same
   story as the gather. (`IND_CyclesBuild` rounds below the print threshold; the latency delta is
   the proof reorder engaged.)
3. **A byte/instruction-identical BFS A/B is impossible** — `numInst_INDRD` ON/OFF differs in BOTH
   4-thread (454/451) and single-worker (454/457). It is **not** OMP noise (it persisted at one
   worker): reordering changes MAA *gather completion order*, which changes which frontier vertex
   wins the conditional parent-claim (`benchmarks/gapbs/src/bfs.cc:179`) → a different-but-valid BFS
   tree (same reachable set; `NumUniqueRowsInserted` 85377 vs 85411, 0.04%) → ±few gathers. The
   T-C "same instructions, only cycles differ" invariant holds for straight-line micro kernels, NOT
   for racing-parent-claim graph kernels — use latency/MLP deltas as the BFS invariant.
4. **Binary constraint:** `bfs_maa` bakes `-DNUM_CORES=4`; it won't run at `-n 1` (libgomp needs a
   spare context) or `-n 2` (MAA scratchpad layout assumes 4 CPU ports → unmapped-write panic at
   `0x40400088`). Deterministic single-worker recipe: **`-n 4` + `OMP_NUM_THREADS=1`** (verified
   work totals match the 4-thread run within 0.04%, so the traversal is complete). A literal 1-core
   build needs recompiling with `-DNUM_CORES=1`.
5. **🚩 Flag for full-scale rerun on mbit10:** reorder's −17% latency is a *real* benefit, just
   masked by high MLP at toy scale. Full-scale BFS (deeper frontiers, working sets past the 8 MB
   L3) may drop MLP enough to unmask it. That is the rerun worth doing; do not chase an
   instruction-identical A/B (it cannot exist for this kernel).
