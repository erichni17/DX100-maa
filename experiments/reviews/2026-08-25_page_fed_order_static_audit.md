# CG page-fed order static audit (2026-08-25)

## Decision

At base `e6373c9f3e7bb20fc2ef912ca78cd6b56db35e78`, there is **no
source-visible difference in the logical ordinal or same-destination FP32 ADD
order** between `physical_page_product_soa_jit` and
`page_fed_product_soa_jit`. Page-fed does mutate Row/Offset state after each
physical 4K index page, before all four pages have arrived, but it does **not**
issue an A read, product read, ADD, or A write before exact four-page admission
and close. Product publication is also closed before execution.

There is, however, a direct timing-to-semantics channel outside that descriptor:
CG combines per-thread `float` partials into shared `float` `d`, `rho`, and
`sum` in whichever order threads enter OpenMP `critical` sections. Page-fed
opens and occupies indirect units while the four product pages are still being
formed, so it can change thread arrival order. FP32 addition is not associative;
different critical-entry order can change `alpha`/`beta`, then all later `x`
and `z` values. This is the highest-ranked explanation for the rejected full-CG
quantized fingerprint and is not covered by the active MAA-only schedule
digests.

This is a static audit. No gem5 process was launched, stopped, signalled, or
otherwise modified. The concurrently owned schedule diagnosis was read only;
its live/incomplete artifacts are not evidence here.

## Exact path comparison

### Admission and execution boundary

- The page-fed descriptor is opened before the page loop
  (`benchmarks/NAS/cg/cg.cpp:1718-1728` and `2128-2138`). Each completed
  physical MUL and index tile is admitted inside that loop
  (`cg.cpp:1740-1767` and `2150-2175`), and close occurs only after the loop
  (`cg.cpp:1806-1818` and `2251-2262`). The physical-page-product descriptor is
  instead submitted there, after all four response-bearing publications.
- An admission synchronously inserts `ordinal = page * 4096 + lane` into the
  Row/Offset tables (`src/mem/MAA/IndirectAccess.cc:3430-3481`). Thus page-fed
  **begins RowTable insertion early**.
- It does **not begin RowTable issue early**. While the state is not closed,
  `fillRowTable` sets `waitForElement` and returns
  (`IndirectAccess.cc:3552-3560`). Only after close does `beginExecution`
  authorize the transition, while asserting exactly 16,384 committed ordinals
  (`IndirectAccess.cc:3561-3576`). `PageFedSoaJitState::close` itself requires
  four complete pages and 16,384 admissions
  (`include/gem5/maa_page_fed_soa_abi.hh:181-207`).

### Logical ordinal and same-destination order

- Page-fed rejects a page other than the next page and an ordinal other than
  the next admitted ordinal (`maa_page_fed_soa_abi.hh:137-177`). Its insertion
  additionally requires `ordinal == my_i`; every insert advances both the
  guarded source ordinal and cursor (`IndirectAccess.cc:3370-3419`).
- The physical path publishes logical page `page_offset / 4096` at its exact
  coherent offset (`cg.cpp:393-433`), then scans one ordinary `0:16384:1`
  SoA/JIT descriptor (`cg.cpp:435-452`). Ordinary SoA/JIT source commit rejects
  every duplicate or skipped ordinal (`IndirectAccess.cc:4600-4624`). The two
  paths therefore name the same nominal source sequence.
- Repeated updates to one destination cache line are appended to one OffsetTable
  chain (`src/mem/MAA/Tables.cc:348-376`). A claimed A-line context starts at
  that chain head, and a second live context for the line is forbidden
  (`IndirectAccess.cc:4651-4692`). Lookahead may deliver later values first, but
  apply accepts only `candidate.offset == context.nextOffset`, then consumes
  and verifies that exact head (`IndirectAccess.cc:5014-5068`). Different lines
  may interleave; updates to one FP32 word cannot change ordinal order.

### Product readiness and publication

- Both modes wait for the physical index and MUL result before handoff
  (`cg.cpp:1755-1758` and `2165-2168`). The physical path response-publishes
  index then product and waits for both completions (`cg.cpp:407-430`).
- Page-fed response-publishes the product, admits the already-finished physical
  index tile, then waits for product publication (`cg.cpp:484-503`). This is a
  real producer/consumer overlap and timing difference, not early accumulation.
  Close panics if any publication for the product range remains live
  (`IndirectAccess.cc:3513-3538`), and execution remains gated by close as
  above.
- The accepted product-handoff probe reports bit-identical 16,384 physical MUL
  words before and after response-bearing publication, and identical
  order-sensitive four-page versus one-pass destinations
  (`experiments/analysis/cg_product_handoff_probe_2026-08-25.md:72-90`). The
  accepted page-fed microprobe likewise matches ordinary, physical SoA/JIT, and
  page-fed destination hashes (`cg_page_fed_soa_2026-08-25.md:141-171`). These
  strongly lower, but do not universally eliminate, a data-dependent full-CG
  handoff defect.

### Multi-core and indirect-unit scheduling

- The MAA has four indirect lanes in the matched configuration. Page-fed open
  must first dispatch into `Fill`, and later doorbells locate that live owner by
  core (`src/mem/MAA/CpuSidePort.cc:241-263`). It therefore holds a lane during
  page production; the physical descriptor reaches the instruction file only
  after all publications.
- Functional-unit type, MAA, instruction-file starting slot, and the first idle
  indirect lane are schedule-sensitive: `MAA::issueInstruction` uses randomized
  traversal and assigns the first available lane
  (`src/mem/MAA/MAA.cc:810-885`), while `IF::getReady` begins at a randomized
  instruction slot (`src/mem/MAA/IF.cc:700-765`). Early page-fed residency can
  therefore change unit identities and cross-core issue timing even when source
  ordinals match.
- This concurrency does not expose two descriptors to the same q/r row block.
  Each OpenMP row-block iteration owns a disjoint `curr_q = &q[j_base]` or
  `curr_r = &r[j_base]` range (`cg.cpp:1623-1634` and `2031-2042`), and each
  routed window waits for its descriptor completion before the thread advances
  (`cg.cpp:1809-1818` and `2253-2262`). It can still change when threads reach
  subsequent global reductions.

## Rejected full-CG fingerprint in context

The accepted physical-page-product full run matches the native16 q5/q6 oracle
(`experiments/analysis/cg_full_native16_correctness_2026-08-25.md:43-63`). The
full page-fed run closes 10,960 operations, 43,840 admissions/publications, all
A read/write responses, and zero fallbacks/drains, yet all four final quantized
hashes differ while scalar tolerances pass
(`cg_page_fed_application_full_2026-08-25.md:27-63`). The page-fed hashes are
`88c0975669c7062d/a1c461b83b95f98f/1458f2551dfa99c6/9fd922f4ccdc69c9`;
native16 is
`bd71373530efa77d/9a25df4701c4afa9/973558f7c958b798/5c3a7792ee8d00f3`
for `x_q5/x_q6/z_q5/z_q6`.

Those are final `x`/`z` hashes, not a direct digest of one RMW window
(`cg.cpp:559-601`). They are downstream of the manual FP32 reductions:

- shared `d`, `rho`, `rho0`, and `sum` are `float` (`cg.cpp:1397-1398`);
- each thread computes a `float d_tmp`, then performs `d += d_tmp` in arrival
  order (`cg.cpp:1900-1919`); `alpha = rho0 / d` immediately consumes it
  (`cg.cpp:1925`);
- each thread similarly performs `rho += rho_tmp` in a `critical` section
  (`cg.cpp:1935-1974`), and `beta` consumes that result;
- the final residual uses the same pattern for `sum` (`cg.cpp:2421-2452`).

Consequently, exact mechanism closure plus per-window ordinal equality is not
sufficient to require the final fingerprint when treatment timing changes.

## Ranked falsifiable causes and smallest next actions

1. **Timing-dependent guest reduction order — high confidence.** Page-fed's
   early lane residency and publisher/admission overlap can change the order in
   which four FP32 partials enter `d`/`rho`/`sum` critical sections. **Diagnostic:**
   record only `(iteration, reduction, tid, partial_bits, before_bits,
   after_bits)` at each critical entry in the already planned matched arms; the
   first differing entry sequence must precede any resulting `alpha`/`beta`
   difference. **Intervention:** store one partial per `tid`, barrier, and have a
   single thread add `tid=0..3` deterministically. This is the smallest
   correctness experiment and should precede more MAA tracing.

2. **Early descriptor residency changes MAA/core schedule — certain timing
   difference, conditional semantic cause.** The open page-fed descriptor holds
   an indirect lane during four page productions; physical does not. This can
   alter unit assignment and A-line issue timing, but static source preserves
   each destination chain and disjoint row-block ownership. **Diagnostic:** add
   descriptor `(core, unit, operation_tick)` and reduction-entry order to the
   paired projection. **Intervention:** for diagnosis only, deterministically
   pin descriptors to a core-derived indirect lane or use deterministic IF
   selection in both arms. Do not treat that configuration as performance
   evidence.

3. **Index/source sequence differs despite nominal mapping — low confidence.**
   The ABI and cursor checks make skips/reordering fail closed, but they do not
   compare the actual full-CG index word in the two arms. **Diagnostic:** hash
   `(window, ordinal, index, destination line, word)` at successful RowTable
   insertion and compare by window, independent of unit/tick. **Intervention:**
   none unless that hash first differs; then compare the physical SPD word with
   the coherent physical-path index publication at that exact ordinal.

4. **Product visibility/value delivery differs — low confidence.** Source gates
   execution after publication closure and accepted adversarial probes are
   bit-identical, but the full run lacks a per-window product-bit digest.
   **Diagnostic:** hash `(window, ordinal, FP32 product bits)` once at publisher
   payload and once at SoA value delivery. **Intervention:** move the existing
   `wait_ready(product_completion_tile)` before the page-fed admit doorbell as a
   one-line overlap ablation; if fingerprints remain different, publication/
   admission overlap is falsified.

5. **Same-destination RowTable chain reorder — statically rejected.** Append,
   single-line ownership, head-only apply, exact identity checks, no-drain
   page-fed geometry, and accepted order-sensitive probes all oppose it.
   **Diagnostic:** only if causes 1-4 fail, emit a compact per-destination
   logical-ordinal chain digest at apply. **Intervention:** no new ordering
   hardware or masked four-pass emulation is justified.

## Handoff to the active schedule diagnosis

The read-only active runner compares final fingerprints, MAA source issue
digests, RowTable macro projections, A/value closure, publisher closure, and
epoch drains (`run_cg_page_fed_schedule_diagnosis.py:84-178` in the owned
schedule-diagnosis worktree). It does not observe the CG `critical` entry order
or `d/rho/sum` partial bits. After its current run terminates naturally, the
smallest successor is therefore reduction-order instrumentation, not another
gem5 size and not a full-CG rerun. Partial live output must not be classified.
