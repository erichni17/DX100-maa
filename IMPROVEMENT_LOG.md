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
