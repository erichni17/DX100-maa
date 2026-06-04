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

**The blocker for the edit->test->compare loop:** instantiating the config costs
**~9.8 GB RSS before simulation starts**. *(SUPERSEDED: the cause guessed here — "the MAA
SimObject build" — was DISPROVEN by the 2026-06-04 bisection. It is actually the per-region
cache stats driven by `MAX_CMD_REGIONS=256`, and it happens even without `--maa`. It is a
DX100-fork bug, not just an environment limit, and it is fixable — see below.)* It's not
the CPU, the mem-size backstore, or the 1.8 MB checkpoint. On this **shared 17 GB host**
that's OOM-killed intermittently — even when ~14 GB looked free it died at ~9.8 GB.

**Recommended ways to actually run the loop:**
- Run on a host with more (and non-shared) RAM — ~16-24 GB free should comfortably hold the
  ~10 GB instantiation + the ROI simulation.
- Or profile the ~9.8 GB MAA-construction allocation (likely a structure sized by the
  Ramulator DRAM org / address space) and shrink it — that would make this config fit and
  is itself a worthwhile simulator improvement.

**Next model improvement queued (Iteration 2):** the behavior-preserving `RequestTable`
O(1) optimization (see above). It is ready to implement; its correctness check is exact
`stats.txt` equality, which needs a host that can run the sim.

---

## 2026-06-04 — Root cause of the instantiation OOM FOUND (and the earlier diagnosis was wrong)

The previous session's "honest assessment" blamed the ~10 GB instantiation cost on the
**MAA SimObject build**. **That was wrong.** A systematic bisection disproved it.

### Systematic bisection (pure platform instantiation, `--initialize-only`, no-restore BASE)
Probed peak RSS (VmHWM) while toggling one axis at a time vs the real run config
(`measure_nr.sh` + `bisect.sh`):

| config | peak RSS | verdict |
|---|---|---|
| 1 core, full caches + Ramulator2 | 1767 MB | ok |
| 2 cores, full caches + Ramulator2 | 4678 MB | ok |
| 4 cores, Ramulator2, **no caches** | 77 MB | ok — MAA/mem/cores alone are cheap |
| 4 cores, caches, **SimpleMemory (no Ramulator2)** | 10031 MB | HUNG — **Ramulator2 exonerated** |
| 4 cores, caches **L1+L2 only (no L3)** | 1361 MB | (early exit) — far below 10 GB |
| 4 cores, full caches (L1+L2+L3) | 10079 MB | HUNG — reproduces the OOM |

Conclusions: not MAA (BASE mode blows up too), not Ramulator2, not the guest mem-size
(1 GB), not core count alone. It is the **cache hierarchy**, scaling with cores, and it
both balloons RAM *and hangs* (>180 s) — i.e. a loop/alloc during **init**, not simulation.

### Caught in the act with gdb
Three backtraces of the hung process (RSS climbing ~1 GB/s) were all in the same place:
`BaseCache::CacheCmdStats::regStatsFromParent` (`src/mem/cache/base.cc:2206`) →
`statistics::DataWrapVec::subname` → `std::vector<string>::resize`. So the cost is
**statistics registration**, not SimObject construction.

### Real root cause
This fork added per-region cache stats. `BaseCache::CacheStats` (base.cc:2227-2276)
eagerly allocates `cmdRegions` = **`MAX_CMD_REGIONS` × `NUM_MEM_CMDS`** `CacheCmdStats`
objects *per cache*, and each `regStatsFromParent()` builds ~15 per-requestor stat vectors
holding `max_requestors` subname strings. With `#define MAX_CMD_REGIONS 256`
(`src/mem/packet.hh:67`) that is ~257× the vanilla per-cache stat tree, ×~16 caches at
4 cores → ~10 GB and a multi-minute init hang. It triggers regardless of `--maa` because
the cache stats are unconditional.

### Fix (Iteration 2a — behavior-preserving)
`src/mem/packet.hh`: `MAX_CMD_REGIONS` 256 → **32**. The bundled workloads register at most
~15 regions (microbench peaks at region id 11; see `test.cpp` / `MAA_gem5.hpp`). Regions
`0..N-1` are byte-identical in behavior and in stats; only the ceiling drops, so this does
**not** change any simulated result for any region a workload actually uses. A workload
that registers id ≥ 32 still fails loudly (`MAA::addAddrRegion` panic), never silently.
Predicted init memory: ~257/33 ≈ 7.8× lower (~10 GB → ~1.3 GB). Verification: rebuild +
re-measure peak RSS, then confirm the full `--maa` run fits and produces stats. (pending)

### Tooling added this session
- `bisect.sh` — the isolation matrix above.
- `diag.sh` — launches the blowup config and grabs gdb backtraces once RSS climbs, to
  pinpoint the allocating call site.

---

## 2026-06-04 (cont.) — MILESTONE: full MAA simulation runs end-to-end & verified correct

After the `MAX_CMD_REGIONS` fix removed the instantiation OOM, the first `--maa` run still
**segfaulted** right after `initializing done, testing...`. Backtrace:
`pseudo_inst::clearmemregion` → `static_cast<o3::CPU*>(tc->getCpuPtr())->clearMemRegion()`
→ `o3::LSQ::clearAddrRegion` (`src/sim/pseudo_inst.cc:558-570`). The region pseudo-ops
**unconditionally cast the active CPU to `o3::CPU`** (only the MAA call beside them is
`hasMAA()`-guarded). Under `TimingSimpleCPU` that pointer isn't an O3 CPU → garbage LSQ →
segfault.

**Root fix:** the artifact's ROI CPU is **`X86O3CPU`**, not TimingSimpleCPU — confirmed by
the artifact's own driver `scripts/sim.py:89` (`cpu_type = "X86O3CPU"`) and by its reference
stats keying off `switch_cpus0.*` (O3). The earlier session's switch to TimingSimpleCPU (to
dodge a from-tick-0 deadlock) was invalid for this artifact. `run_test.sh` step 2 now uses
`--cpu-type X86O3CPU` and mirrors `scripts/sim.py` MAA-mode caches for 4 cores
(L3 = 2MB*cores = 8MB, assoc 4*cores = 16, l3_ports = cores, Stride prefetchers on L1d/L1i/L2).

**Intentional host-driven deltas from the paper config (kept):**
- `--mem-size 1GB` (paper: 16GB) — matches the `MAA_MEM_SIZE=0x40000000` binary's MAA region
  base and the AtomicSimpleCPU checkpoint; avoids a 16GB backing-store on the shared host.
- Binary built without `-fopenmp` → single-threaded driver: only `switch_cpus0` runs
  (cpus 1-3 = 0 cycles). The MAA itself is fully exercised; per-core parallel scaling is not.

**Result (run_baseline, MAA gather allhit n=20000):** `gem5 exit=0`,
**"End of Test, all tests correct!"**, peak RSS ~5.0 GB (fits the 17 GB host). Final stat dump:

| metric | value |
|---|---|
| simSeconds | 0.000029 |
| simTicks | 28,525,255 |
| system.maa.numInst | 6 |
| system.maa.cycles (INDRD+STRRD) | 6509 (3989 + 2520) |
| IND_AvgUniqueWordsPerCacheLine | 16 |
| IND_AvgUniqueCacheLinesPerRow | 89.285714 |
| IND_AvgUniqueRowsPerInst | 7 |
| switch_cpus0.numCycles | 91135 |
| switch_cpus0.ipc | 1.337510 |

**Comparison methodology for the edit->test->compare loop:** `compare_stats.sh <ref> <new>`
diffs two `stats.txt` ignoring only wall-clock fields (`host*`). gem5 is deterministic, so an
unchanged model must produce byte-identical stats; any diff after a model edit is the signal
to inspect. Baseline saved under `baselines/` (gitignored; key metrics tabled above).

---

## 2026-06-04 (cont.) — Iteration 2b: RequestTable O(num_addresses) -> O(1) [behavior-preserving]

First real edit→test→compare loop iteration on the validated harness.

**Change** (`src/mem/MAA/Tables.{hh,cc}`): `RequestTable` previously did linear scans over
`num_addresses` (default **128**) in the three methods called *per word* on the stream/
indirect hot path:
- `add_entry()` — scan to find the address slot + scan for a free entry slot
- `get_entries()` — scan to find the matching address
- `is_full()` — scan for any free slot

Replaced with O(1): an `unordered_map<Addr,int>` (base_addr → slot), a per-slot contiguous
`entry_count`, and a `free_slots` stack (seeded so slot 0 is handed out first, matching the
old lowest-index allocation). Same entries stored/returned in the same order; same stats
incremented under the same conditions. Covers every RequestTable instance (Stream unit's
one + the Indirect unit's `RT[config][idx]` array).

**Why it's safe:** the callers (`StreamAccess.cc`, `IndirectAccess.cc`) use these only as
control-flow predicates; modeled cycles come from the access-unit state machines, not from
the table's scan length. So results must be invariant.

**Verification:** `gem5 exit=0`, "all tests correct!", and
`compare_stats.sh baseline run_iter2` → **IDENTICAL** (byte-identical modulo wall-clock).
Confirms the optimization changes only host cost, not the simulated result. (This microbench
issues just 6 MAA instructions, so the wall-clock win here is in the noise; the benefit is
128x→1 per-word table ops on large gathers.)

---

## 2026-06-04 (cont.) — Iteration 2b validation under stress + 2nd baseline point

Stress-tested the O(1) `RequestTable` on the DRAM-missing pattern (`gather allmiss 1 100 1 1`,
n=20000) which actually fills the table: **S0_STR_NumWordsInserted = 20000 over 1250 unique
cache-line addresses** (vs 128 slots → repeated fill/drain/refill, the exact path rewritten).
Result: `gem5 exit=0`, **"all tests correct!"**. Combined with the byte-IDENTICAL allhit
diff, the O(1) change is validated both for exactness (allhit) and correctness-under-load
(allmiss).

This also gives the regression suite a **2nd, more interesting baseline point** (allmiss
exercises the row-table reordering: maa.cycles 6509→91914, IND_AvgUniqueRowsPerInst 7→99,
IND_AvgUniqueCacheLinesPerRow 89.3→101). Saved under `baselines/`.

### Status checkpoint
Runnable + verified-correct + deterministic MAA simulation on the 17 GB host, with a
validated edit→test→compare loop and 2 baseline points. Landed improvements:
- **MAX_CMD_REGIONS 256→32** — fixes the ~10 GB init OOM (was misdiagnosed as the MAA build).
- **RequestTable O(n)→O(1)** — behavior-preserving host-side speedup on the per-word hot path.

---

## 2026-06-04 (cont.) — Architectural investigation: row-table sizing is NOT the lever (negative result)

Targeted the row table (the MAA's "central performance mechanism"). Profiling the DRAM-bound
`gather allmiss` indirect read showed `IND_CyclesRequest` = 84915 of 86236 INDRD cycles, and
`NumRowsInserted/Unique` = 2510/198 = **12.7× row re-activation**. Root mechanism (confirmed
in code): each row-table row holds only `num_row_table_entries_per_subslice_row = 8` distinct
cache lines (`RowTableEntry::insert`, Tables.cc:222), but the pattern has ~101 unique lines
per DRAM row → a row fills after 8, drains, and re-opens ~101/8 ≈ 12.6×.

**Hypothesis:** raise entries-per-row → fewer drains/re-activations → fewer DRAM activations
→ lower latency/cycles. **Swept it (sweep_rt.sh) over {8,16,32,64}:**

| EPR | cycles_INDRD | re-activation | AvgMemLat | correct |
|----:|-------------:|--------------:|----------:|:-------:|
| 8   | 86236        | 12.68x        | 325.78    | yes |
| 16  | 86270        | 6.35x         | 326.89    | yes |
| 32  | 86411        | 3.19x         | 327.13    | yes |
| 64  | 86411        | 1.81x         | 327.13    | yes |

**Result: re-activation fell 7x but cycles & memory latency did NOT move** (cycles even 0.2%
worse). **Conclusion:** Ramulator2's **FRFCFS** memory scheduler already reorders requests for
row-buffer hits, so the MAA row-table's reordering is largely redundant here and reducing its
re-activation yields no end-to-end gain; the indirect read is **memory-throughput-bound**
(`port_mem_RD_BW` ≈ 22.85 GB/s on 2 channels), not row-locality-bound.

**Action:** do NOT change the default (8 is fine; larger only adds modeled SRAM for no gain —
and the model charges a fixed `rowtable_latency` regardless of size, so it wouldn't even show
the area cost). Value of this iteration = a measured *negative* result that prevents a
pointless "optimization", and a pointer for real perf work: the lever is memory-level
parallelism / throughput, not row-table capacity. (Open question for future: quantify the MAA
row table's benefit vs FRFCFS-only — it may be near-redundant in this config.)

---

## 2026-06-04 (cont.) — Coverage: validate across MAA instruction types

Ran the other MAA op types on the current build (incl. the O(1) RequestTable) to surface
latent bugs and broaden the regression suite:

| kernel  | gem5 exit | maa.cycles | all correct? |
|---------|-----------|------------|--------------|
| scatter (INDIR_ST)  | 0 | 12806 | yes |
| rmw (INDIR_RMW)     | 0 | 12806 | yes |

Both pass. The codebase is now validated across **gather/scatter/rmw × allhit/allmiss**
(4+ patterns). Baselines saved under `baselines/`.

### Session-end status (this session's net result)
Started: artifact OOM-killed at instantiation, would not run. Ended: **runnable,
verified-correct, deterministic MAA simulation on the 17 GB host, with a validated
edit→test→compare loop and a 4-pattern regression suite.** Landed:
1. **MAX_CMD_REGIONS 256→32** — fixes the ~10 GB init OOM (correctly diagnosed via bisection
   + gdb after the prior session's wrong guess).
2. **X86O3CPU ROI** — fixes the region-pseudo-op segfault (TimingSimpleCPU was invalid for
   this artifact); first end-to-end correct run.
3. **RequestTable O(num_addresses)→O(1)** — behavior-preserving (IDENTICAL stats), validated
   under allmiss stress + all op types.
4. **Row-table sizing investigation** — rigorous negative result: re-activation falls 7x but
   performance is flat; the workload is memory-throughput-bound and MAA row reordering is
   masked by Ramulator FRFCFS. Default kept.

**Honest read on further *modeled-performance* gains:** this microbench config is
memory-bound, so accelerator-side perf wins are not available without changing the memory
system or moving to a config/workload where the MAA's mechanisms aren't masked by FRFCFS.
Remaining safe host-side optimizations (e.g. RowTableSlice O(1)) are low-value (sim speed
only) and riskier (drain-order is observable). Good checkpoint.

---

## 2026-06-04 (cont.) — Confirming experiment: reorder ON vs OFF (row table masked)

Second, independent test of whether the MAA row-table reordering helps here: ran
`gather allmiss` with reordering ON (default) vs OFF (`--maa_no_reorder`):

| config       | cycles_INDRD | AvgMemLat | port_mem_RD_BW | correct |
|--------------|-------------:|----------:|---------------:|:-------:|
| reorder ON   | 86236        | 325.78    | 22.85          | yes |
| reorder OFF  | 85385        | 325.83    | 23.02          | yes |

Disabling reordering entirely moves indirect-read perf ~1% (marginally *better*). Combined
with the capacity sweep, this firmly establishes: **the row-table *reordering* is masked by
Ramulator FRFCFS in this config and provides no end-to-end benefit** (its word→cacheline
*coalescing* is still useful). The indirect read is memory-bound; the lever is bandwidth/MLP,
not the row table. Next: check the Ramulator DRAM org to see if the MAA is at the BW ceiling
or leaving bandwidth unclaimed.

---

## 2026-06-04 (cont.) — Bottleneck RESOLVED: the gather is DRAM-bandwidth-bound near peak

The Ramulator config is DDR4_3200 (`channel:1` each) and gem5 instantiates **2** controllers
(`system.mem_ctrls0/1`) → ~51.2 GB/s peak (2 × 25.6). The whole-run averages were misleading
(diluted by idle time). The **active-phase** read bandwidth is what matters:

- MAA reads ~20000 unique cache lines × 64 B = **1.28 MB** during `cycles_INDRD = 86236`
  (= 26.95 µs at 3.2 GHz) → **47.5 GB/s**, i.e. **~93% of the 2-channel DDR4 peak**.
- (Consistent with the diluted `port_mem_RD_BW`=22.85 over a ~2× wider stat window.)

**Conclusion (ties all the negative results together):** the indirect gather is
**memory-bandwidth-bound at ~93% of peak** during its active phase. That is exactly why
neither row-table *reordering* (masked by FRFCFS) nor row-table *capacity* (8→64) changed
performance — **there is no bandwidth headroom to reclaim.** This is a *positive* result about
the artifact: the MAA already saturates DRAM on irregular gather, which is its design goal.
Further gather speedup in this config requires **more memory bandwidth (channels)**, not a MAA
algorithm change — so there is no safe accelerator-side code optimization to be had here.

### Architectural investigation: closed
Three controlled results (capacity sweep, reorder on/off, active-BW calc) converge on the same
story. The MAA model is correct and bandwidth-efficient in this config; the row table's
*reordering* is redundant with FRFCFS (its *coalescing* still matters). No further
modeled-perf improvement is available without changing the memory system.
