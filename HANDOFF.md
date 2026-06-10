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
The MAA indirect **gather is DRAM-bandwidth-bound** — channel scaling gives 4× channels →
3.2× faster (≈93% of 2-channel DDR4-3200 peak active-phase). Its throughput is **insensitive
to row-buffer-locality optimization from either side**, across the *entire reachable design
space*: MAA reordering on/off ≈ 1%, row-table capacity 8→64 flat, controller FRFCFS queue
32→**1** flat (only `MemLat` moves, 277→66), and shrinking the problem n=20000→200 keeps the
MAA at **MLP ≈ 53–76** (a high-MLP engine by design — can't be starved into latency-bound).
So the row table's **reordering** is redundant here. (Reorder ON used to **livelock at
controller queue ≤ 2**, but that was a Ramulator2 active-buffer drop bug, now fixed — see the
Bug fix above.) Its word→cache-line **coalescing** is still useful. Net: **no safe accelerator-side code
optimization helps this config — the lever is memory bandwidth (channels).** A real
*reordering* win would need a low-MLP consumer the MAA doesn't produce. Sweep harnesses:
`sweep_rt.sh` (capacity), `reorder_test.sh` (reorder), `chan_sweep.sh` (channels),
`queue_sweep.sh` (queue×reorder), `nsweep.sh` (problem size), `latbound.sh` (queue 1/2/4).

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
