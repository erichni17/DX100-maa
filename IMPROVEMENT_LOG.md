# DX100 Improvement Log

Working log for improving the DX100 ("MAA" — Memory Access Accelerator) gem5 model.
Branch: `dx100-improvements`. Baseline commit: `e4fc4af`.

## Methodology / Loop
1. Establish a baseline (build gem5, run a fast test, record metrics).
2. Make one focused edit to the model.
3. Re-run the same test.
4. Compare metrics to baseline. **Drastically different results without a major
   intended change = red flag (regression).** Similar results = safe; intended
   improvements should move the targeted metric in the expected direction.
5. Commit, then repeat.

## Environment
- Host: 9 cores, 17 GB RAM (NOTE: full paper benchmarks need ~35 GB each — infeasible
  here, so the loop relies on small microbenchmarks / functional verification).
- Toolchain: g++ 11.5, scons 4.10, cmake 3.26, python 3.9. `protoc` missing (optional).
- gem5 target: `build/X86/gem5.opt`.

## Architecture notes (as learned)
- DX100 = `src/mem/MAA/`. A `ClockedObject` sitting between CPU/LLC and DRAM (Ramulator2).
- Functional units: `StreamAccessUnit` (strided), `IndirectAccessUnit` (gather/scatter —
  the core innovation), `ALUUnit`, `RangeFuserUnit`, `Invalidator`.
- Scratchpad `SPD` (tiles), scalar `RF`, instruction file `IF`.
- **Row table** (`Tables.*`): reorders indirect accesses by DRAM (channel/rank/bankgroup/
  bank/row) to maximize row-buffer hit rate. This is the central performance mechanism.
- Indirect unit pipeline stages: Idle → Decode → Fill → Build → Request → Response.
- Key tunables (MAA.py): row-table geometry, ALU lanes (16), SPD ports (4R/4W),
  latencies, request-table sizing, `reorder`/`reconfigure` flags.

## Log

### Iteration 0 — Setup & baseline (in progress)
- Cloned repo, created branch `dx100-improvements`.
- Launched `gem5.opt` build (background).
- Studied: MAA.py, MAA.hh, IndirectAccess.hh. Reading Tables + timing logic next.
- TODO: get a fast test running to establish baseline numbers.

### Iteration 0a — Build fix: SCons 4.9+ incompatibility (BUGFIX, not a model change)
- **Symptom:** build aborted at configure: "Did not find needed zlib compression library".
  But zlib IS installed (`/usr/include/zlib.h`, `/usr/lib64/libz.so`) and links fine with g++.
- **Root cause:** SCons 4.9.0 inserted a new positional parameter `extra_libs` *before*
  `call` in `CheckLibWithHeader(context, libs, header, language, extra_libs=None,
  call=None, ...)`. This gem5 fork passes the `call` string positionally as the 4th arg,
  so e.g. `"zlibVersion();"` landed in `extra_libs` and was splatted into the linker as
  individual single-char libs: `-ll -li -lb -lV -le ...`, and `-l(` `-l)` `-l;` even broke
  the shell (`syntax error near unexpected token '('`). The conftest link failed → false
  "library missing".
- **Fix:** pass `call=` as a keyword at all 8 `CheckLibWithHeader` call sites
  (SConstruct ×2, src/base, src/base/stats ×2, src/cpu/kvm, src/mem, src/proto, src/sim).
  Keyword form is correct for both old and new SCons.
- This is an environment/build fix, not a model behavior change — results unaffected.
- Rebuilding with `--config=force` to clear cached conftest result.

### Iteration 0b — Build relocated to local disk (infra)
- `/home/nier` is a **CIFS network mount** (`cifsd` active). gem5's build touches
  thousands of small files; on CIFS the scons dependency scan + codegen phase crawled
  (~6% CPU, I/O-bound), and every *incremental* rebuild in the edit→test loop would pay
  that cost repeatedly.
- `/tmp` is local ext3 (28 GB free). Relocated a working copy to `/tmp/DX100` (full git
  repo incl. branch + the SCons fix). Build + edit + test happen there for speed.
- Persistence: `/home/nier/DX100` remains the canonical repo; commits/changes are synced
  back there. (Noted: `/tmp` may be cleared on reboot — sync before relying on it.)
- Launched local `gem5.opt` build.

### Iteration 1 (planned) — Establish baseline
- Plan: build gem5.opt, compile `benchmarks/API/test.cpp` (a self-contained gather/scatter/
  rmw microbenchmark) with the GEM5 magic-op API, run a *small* case under
  `configs/deprecated/example/se.py --maa` with Ramulator2, and capture `stats.txt`
  (cycles, BW, row-buffer hit rate, MPKI) as the reference point.
- `CMP` mode in the microbenchmark runs BASE+MAA and self-verifies correctness — doubles
  as a functional check.

### Iteration 0c — `/tmp` wipe recovery (infra)
- The host rebooted (Claude usage-limit reset gap); this **wiped `/tmp/DX100`** and killed
  all background builds (the spurious "exit 144"s). Lost: Ramulator2 build, gem5 build,
  microbenchmark binary, `run_test.sh`, and `/tmp`-only log/source edits.
- **Lesson applied:** work + build now live in the persistent `/home/nier/DX100` (CIFS,
  slower but durable), and source/scripts/log are **committed to git frequently** so only
  regenerable build artifacts are ever at risk.
- Recovered & committed: SCons fix (commit 3dcd51a), microbenchmark `using namespace std;`
  fix + `run_test.sh` (commit 368817b). Re-launched Ramulator2 + gem5 builds in /home.
  Microbenchmark binary `test_T16K.o` rebuilt.

#### Build fix: microbenchmark `test.cpp` (GEM5 backend)
- `benchmarks/API/test.cpp` failed under `-DGEM5` (g++-11): 63 errors from unqualified
  `cout/endl/string/stoi/max/memory_order_relaxed`. The FUNC header brings in
  `using namespace std;`; the GEM5 header does not. Fix: add the using-directive to
  test.cpp. Harness-only; no model effect.
- Compile recipe (assemble the checked-in m5op.S; no need to scons-build util/m5):
  `g++ -std=c++17 -march=corei7 -msse4.1 -mno-avx util/m5/src/abi/x86/m5op.S test.cpp
   -Iinclude/ -Iutil/m5/src/ -fopenmp -DGEM5 -DTILE_SIZE=16384 -O2 -o test_T16K.o`

### Iteration 1 — Establishing the baseline surfaced two more runnability bugs
Bringing up the single-core no-checkpoint run path (`run_test.sh`) hit two fork bugs:

1. **`Simulation.py` fast-forward CPU-class unpack bug.** The MAA config path at
   `Simulation.py:751` unconditionally iterates `testsys.switch_cpus`, which only exists
   when a fast-forward/checkpoint CPU switch is configured. Adding `--fast-forward=1` then
   tripped `AttributeError: 'tuple' object has no attribute 'numThreads'`: in
   `setCPUClass()` the fast-forward branch did `TmpClass = getCPUClass(...)` without
   unpacking the `(class, mem_mode)` tuple (the non-fast-forward path unpacks correctly).
   Fix: `TmpClass, _ = getCPUClass(...)`. The fast-forward path was simply untested in this
   fork (everything uses checkpoints). With the fix, the CPU switches AtomicSimpleCPU →
   X86O3CPU at the ROI, creating `switch_cpus`.

2. **gem5 X86 CPUID lacks x86-64-v2 → modern glibc aborts.** After the switch, the
   *simulated* program died with `Fatal glibc error: CPU does not support x86-64-v2`. RHEL 9
   (this host) builds glibc with an x86-64-v2 baseline, so `ld.so` aborts at startup when
   the simulated CPUID doesn't advertise SSE4.1/SSE4.2/POPCNT/CMPXCHG16B. The artifact's
   Ubuntu Docker used a v1-baseline glibc and never hit this. Tried `-static` (static libs
   not installed, no root). Fix: advertise x86-64-v2 in `X86ISA.py` CPUID function 1 ECX
   (`0x00000209` → `0x00982209`). EDX (SSE/SSE2/FXSR/CMOV) and ext-leaf ECX (LAHF) already
   qualify. gem5's decoder implements POPCNT/SSE4.x, so glibc's ifunc routines are safe.
   This makes the artifact runnable on modern v2-baseline Linux hosts. Rebuilding gem5.
- Both are genuine fixes that don't change the DX100 *model*; they unblock running it.

### Iteration 1 (cont.) — Full timing bring-up: more fixes + an environment wall
Getting the MAA timing run to actually execute surfaced several more issues (all fixed),
plus a hard environment limit:

3. **MAA region NULL-pointer panic.** `MAA::addAddrRegion` panics ("Region overlaps")
   when a region starts at `0x0`, because cleared slots have `first==0` (in `[0,end)`).
   The microbenchmark registered all six data-array regions unconditionally, but for the
   plain `gather` kernel `boundaries`/`cond` (and `a1` in MAA-only mode) are NULL. Fix in
   `test.cpp`: only register non-NULL arrays, and initialize the pointers to `nullptr`
   (they were indeterminate → `if(ptr)` was UB under -O2).
4. **OpenMP thread creation fails in SE mode.** `#pragma omp parallel` tries to spawn a
   thread per core; SE mode has no spare contexts. The benchmark calls no `omp_*` runtime
   functions, so compiling **without `-fopenmp`** (pragmas become no-ops → serial) avoids it.
5. **4-core MAA geometry is hardcoded.** The benchmark hardwires `NUM_TILES=32`
   (= 8 tiles/core × **4 cores**), so the MAA scratchpad region is 2 MB. Running `-n 1`
   sizes it for 1 core (512 KB) → stores land in unmapped space → "Tried to write unmapped
   address". Must run **`-n 4`** (the validated core count).
6. **MAA region base == gem5 `--mem-size`.** `MAAConfig` places the MAA MMIO region at
   `options.mem_size`; the benchmark hardcodes the same base as `MEM_SIZE` (16 GB for 4
   cores). They must match. Added a `-DMAA_MEM_SIZE=` override to `MAA_gem5.hpp` so small
   experiments can use a low base (e.g. 1 GB) instead of 16 GB.

**Working run flow (validated, in `run_test.sh`):** atomic checkpoint at the ROI
(`--max-checkpoints=1`, cached & reused) → restore on a timing CPU with `--maa`, `-n 4`,
matched `--mem-size`/`MAA_MEM_SIZE`. With these, the MAA address ranges are correct, no
panic, no deadlock — the model runs.

**Environment wall (the current blocker for the loop):** instantiating this config costs
**~9.7 GB RSS** *before* simulation even starts — and this is **independent of CPU type
(X86O3CPU vs TimingSimpleCPU) and of `--mem-size` (16 GB → 1 GB)**, so it's gem5's
MAA snoop-filter / routing structures, not the memory backstore or the OoO core. On this
**shared 17 GB host** (`caen-vnc-mi10`), free memory fluctuates with other users (seen as
low as ~5 GB), so runs are intermittently **OOM-killed (exit 137)** during instantiation.
When ~14 GB is free the run proceeds. This makes a *tight* edit→test→compare loop on the
full timing model unreliable here — not a DX100 bug, an environment constraint.
Mitigations if continuing: run when the host is idle; or reduce the snoop-filter/routing
sizing; or use a machine with more free RAM.

### Iteration 2 (planned) — Behavior-preserving sim-speed optimization: RequestTable
- Target: `RequestTable` (used by `StreamAccessUnit`; `Tables.cc`). Hot path:
  - `is_full()` does a full O(num_addresses=128) scan, called *every iteration* of the
    stream fill loop (StreamAccess.cc:206,217,218,302).
  - `add_entry()` / `get_entries()` linear-scan all addresses per word / per CL response.
- Optimization (O(1)): add `num_valid_addresses` counter (is_full O(1)), an
  `unordered_map<Addr,int>` addr->slot index (add_entry/get_entries O(1)), and a free-slot
  list. Per-address entry slots fill monotonically (cleared only en-masse in get_entries),
  so entry order is preserved.
- **Expected result delta: ZERO** — stats.txt must be byte-identical (same addresses, same
  entry order, same full/not-full decisions). The stats diff is the pass/fail test: this is
  the textbook case of the user's "drastically different = red flag" rule. Benefit: faster
  simulation (paper's full runs take 84 h), no model change.

## Session summary (status & honest assessment)

**What works now (committed):** the artifact **builds** and the DX100 timing model **runs
correctly** (correct MAA address ranges, no panic, no deadlock) after 7 fixes:
1. SCons 4.9+ `CheckLibWithHeader` keyword fix (build was fully broken).
2. Ramulator2 built with g++-11 (README wanted g++-12).
3. microbenchmark `using namespace std;` (GEM5 backend wouldn't compile).
4. `Simulation.py` fast-forward `getCPUClass()` tuple-unpack bug.
5. x86-64-v2 CPUID (so modern-glibc programs don't abort under gem5).
6. MAA region NULL-pointer guard + `nullptr` init in `test.cpp`.
7. `-DMAA_MEM_SIZE` override for the MAA MMIO base.
Plus `run_test.sh`, the validated cached-checkpoint -> restore+`--maa` loop harness.

**The blocker for the edit->test->compare loop:** instantiating the `--maa` config costs
**~9.8 GB RSS before simulation starts** (it's the MAA SimObject build, not the CPU, the
mem-size backstore, or the 1.8 MB checkpoint). On this **shared 17 GB host** that's
OOM-killed intermittently — even when ~14 GB looked free it died at ~9.8 GB. So a *tight*
loop on the full timing model is not reliable here. This is an environment limit, not a
DX100 bug; the model itself is in a runnable state.

**Recommended ways to actually run the loop:**
- Run on a host with more (and non-shared) RAM — ~16-24 GB free should comfortably hold the
  ~10 GB instantiation + the ROI simulation.
- Or profile the ~9.8 GB MAA-construction allocation (likely a structure sized by the
  Ramulator DRAM org / address space) and shrink it — that would make this config fit and
  is itself a worthwhile simulator improvement.

**Next model improvement queued (Iteration 2):** the behavior-preserving `RequestTable`
O(1) optimization (see above). It is ready to implement; its correctness check is exact
`stats.txt` equality, which needs a host that can run the sim.
