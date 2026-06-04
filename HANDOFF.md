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
3.2× faster (≈93% of 2-channel DDR4-3200 peak active-phase). In this regime its throughput is
**insensitive to row-buffer-locality optimization from either side**: MAA reordering on/off
≈ 1%, row-table capacity 8→64 flat, and shrinking the memory controller's FRFCFS reorder
window 32→8 leaves cycles flat while only `MemLat` drops (325→125) — i.e. throughput-bound,
not latency-bound. So the row table's **reordering** is redundant here (it would only pay off
in a **latency-bound** regime — low MLP, row-hit latency on the critical path); its
word→cache-line **coalescing** is still useful. Net: **no safe accelerator-side code
optimization helps this config — the lever is memory bandwidth (channels).** Sweep harnesses:
`sweep_rt.sh` (capacity), `reorder_test.sh` (reorder), `chan_sweep.sh` (channels),
`queue_sweep.sh` (controller queue × reorder).
