# CLAUDE.md — orientation for a fresh session

You are picking up a fork of the **ISCA'25 DX100/MAA artifact** (gem5 model of a Memory Access
Accelerator that offloads indirect gather/scatter/RMW/stream patterns; Ramulator2 models the DRAM
underneath). Work happens on branch **`dx100-improvements`**. Read this first, then the docs below.

## Read these, in order
1. **[`HANDOFF.md`](./HANDOFF.md) — the source of truth.** Current state, every change + why, build
   instructions, the test loop, architectural findings, and the T-A / T-B experiment write-ups.
2. **[`README.md`](./README.md) §Findings** — the headline results in brief.
3. **[`IMPROVEMENT_LOG.md`](./IMPROVEMENT_LOG.md)** — full chronological detail.
4. `AGENT_SUMMARY.md` — older (pre-T-B) historical handoff; **HANDOFF.md supersedes it** where they
   disagree.

## Current state (keep this section updated as work lands)
- **Runnable, correctness-verified, characterized** on a modern ~17 GB host, no Docker. Includes a
  Ramulator2 controller bug fix (silent request drop) and an O(1) `RequestTable` rewrite.
- **Headline finding (RESOLVED):** row-table **reordering is the single most valuable
  accelerator-side lever on scattered indices — up to 2.77×** (gather, 0% row-buffer hit). The
  earlier "reorder ≈ 1%, redundant" conclusion was an artifact of testing *only* the tame
  `allmiss 1 100 1 1` pattern (100% row-buffer hit). **Never characterize reorder at a single index
  pattern — always sweep row-buffer-hit%** (`reorder_dist_sweep.sh`).
- **BFS A/B done at toy scale:** high-MLP (~40) hides reorder's −17% DRAM-latency win end-to-end. An
  instruction-identical BFS A/B is *impossible* (gather completion-order → parent-claim race), so use
  latency/MLP deltas as the invariant, not byte-identical stats.

## Next step (UNBLOCKED — big box acquired)
- **Full-scale BFS rerun is now runnable on `mbit1.eecs.umich.edu`** (330 GB RAM / 28 cores —
  see §Running on mbit1 below). Deeper frontiers / working sets past the 8 MB L3 may drop MLP and
  unmask reorder's latency win. This is the highest-value open experiment. The `~64 GB box / mbit10`
  blocker is resolved — mbit1 is ~5× that. Cheaper local follow-ups and the parked task queue are in
  HANDOFF.md.

## Running on mbit1 (the remote box, as of 2026-06-15)
- **Repo lives at `/data1/nier/DX100`** (NOT `/home/nier` — home is small; `/data1` is scratch/**not
  backed up**, so push anything worth keeping to git).
- **Toolchain: the default `gcc` is 9.5 and REJECTS `-std=c++20`** (Ramulator2 won't build). Before
  building, `export CC=gcc-12 CXX=g++-12` (g++-10/11/12 are all in `/usr/bin`; no sudo / no `module`).
- **28 physical cores** → bump the gem5 build and parallel sims well past `-j4` (e.g. `-j16`), leaving
  headroom; 2 NUMA nodes, so consider `numactl` pinning for latency-sensitive sims.
- **Path fix:** the root `*.sh` scripts hard-code `GH=/home/nier/DX100`; set `GH=/data1/nier/DX100`.
- **Etiquette (shared box, owner Sumanth Umesh via Reetuparna Das's group):** check `htop` for free
  RAM/CPU first; CPU/RAM-heavy job **>2 h → post in the MBit Management group**, **>4 h → message
  Sumanth directly**. The OpenEvolve loop (many parallel gem5 sims, long wall-clock) is the >4 h case.

## Conventions
- **Verification for perf changes = pattern/action invariants, not byte-identical stats** (timing
  may move; instruction count + functional output must not — except BFS-class kernels, see above).
- **Build:** Ramulator2 (`libramulator.so`) then `scons build/X86/gem5.opt -j4`; editing
  `src/mem/packet.hh` forces a ~30 min full rebuild. Details in HANDOFF.md §Build.
- **Run loop:** `bash run_test.sh <outdir> MAA gather "<dist>" <n>` (2-step checkpoint→restore).
  Sweep harnesses are the root `*.sh` scripts; experiment outputs are gitignored.
- Scripts currently hard-code `GH=/home/nier/DX100` — adjust if the repo moves (on mbit1:
  `GH=/data1/nier/DX100`; see §Running on mbit1).
