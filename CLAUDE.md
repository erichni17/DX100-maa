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

## Next step (parked)
- **Full-scale BFS rerun on a ~64 GB box (`mbit10`, via Ritu):** deeper frontiers / working sets
  past the 8 MB L3 may drop MLP and unmask reorder's latency win. This is the highest-value open
  experiment. Cheaper local follow-ups and the parked task queue are in HANDOFF.md.

## Conventions
- **Verification for perf changes = pattern/action invariants, not byte-identical stats** (timing
  may move; instruction count + functional output must not — except BFS-class kernels, see above).
- **Build:** Ramulator2 (`libramulator.so`) then `scons build/X86/gem5.opt -j4`; editing
  `src/mem/packet.hh` forces a ~30 min full rebuild. Details in HANDOFF.md §Build.
- **Run loop:** `bash run_test.sh <outdir> MAA gather "<dist>" <n>` (2-step checkpoint→restore).
  Sweep harnesses are the root `*.sh` scripts; experiment outputs are gitignored.
- Scripts currently hard-code `GH=/home/nier/DX100` — adjust if the repo moves.
