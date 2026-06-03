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
