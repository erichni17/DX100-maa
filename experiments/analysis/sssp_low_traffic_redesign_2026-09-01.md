# SSSP logical16/physical4 low-traffic redesign (2026-09-01)

## Decision

The 12.784006x slowdown is not a failure to find 16K locality and it is not a
RangeLoop cost.  The accepted micro preserves useful 16K locality, but the
current guest turns every four 4K producer pages into a serial

`publish index -> wait -> publish value -> wait -> rebuild Row/Offset -> RMW ->
write old results -> wait -> CPU reconstruct`

transaction.  Of the 25,318,291 excess MAA cycles over native4, 24,845,915
(98.1343%) are excess idle cycles.  The explicit ledger contains 8,192
publisher writes, 15,633 old-result writes, 4,096 index reads, 4,096 predicate
reads, 10,176 JIT value reads, and 16,385 A reads plus writes.  These total
74,963 cache-line requests (4,797,632 transport bytes) before counting CPU
reconstruction traffic.  The 16 RangeLoop instructions themselves account for
only 4,096 compute, 768 SPD-read, and 4,096 SPD-write cycles.

Prototype a default-off combination of **A and D**: paired page-fed
destination/value admission into one existing 16K Row/Offset epoch, with the
FP32 operand carried in the existing mutually-exclusive `pass`/aux field, plus
a generic MIN success-retirement sink whose records become visible only after
the corresponding A-line WriteResp.  This is a reusable inline-operand,
condition-producing RMW mode, not an SSSP opcode.  It removes coherent
index/value publication and reads, the all-ones predicate stream, the
old-result stream, and page-local CPU reconstruction.

The model projects 40,962 explicit request lines for the matched micro (16,385
A reads, 16,385 A writes, and 8,192 dense eight-byte retirement-record writes),
a 45.3570% reduction from the directly accounted current requests.  Replacing
the 23,825 publisher plus old-result write lines with 8,192 retirement lines is
a 65.6160% reduction.  This is a traffic bound, **not** a timing prediction.

Do not run S22.  The first live step, after implementation and host tests, is
one candidate-only locality micro against the frozen native observations.

## Evidence basis

The accepted evidence is the correctness-complete three-arm result documented
in `experiments/analysis/sssp_locality_matched_micro_2026-09-01.md`.  Its raw
immutable root is:

`/data1/nier/worktrees/codex-coordination/sessions/sssp-locality-matched-micro-20260901-20260831-225546-d4d67a8b/evidence/sssp-locality-matched-micro-r1/campaign`

The graph SHA-256 is
`902d3b2dfceddc44a354ce2f7a9a3d572327c2c2fc7ff99190baff74d059c3e3`.
All arms produced the exact accepted fingerprint, all restore wrappers and
terminal checks closed, and the treatment used four routed windows with no
unsafe or fallback window.  The comparison is one exploratory replica, enough
to reject the present mechanism but not to estimate variability.

| Arm | `simTicks` | MAA cycles | Idle / busy cycles | Cache lines | Rows |
|---|---:|---:|---:|---:|---:|
| native4 | 672,489,890 | 2,148,530 | 1,826,561 / 321,969 | 345,420 | 43,416 |
| native16 | 618,231,027 | 1,975,179 | 1,816,702 / 158,477 | 148,768 | 18,696 |
| hybrid | 8,597,114,973 | 27,466,821 | 26,672,476 / 794,345 | 230,733 | 29,024 |

Native16 is 1.087764704x native4 and cuts generic cache-line and row
insertions by 56.93%, proving the constructed locality opportunity.  Hybrid
still cuts them by about 33%, so routing locality survives.  Hybrid is only
0.078222740x native4 because the handoff/retirement protocol dominates it.
ACT/PRE do not monotonically follow the generic locality counters, so this
report uses the demonstrated cache-line/row direction and does not claim a
general DRAM-row win.

### Exact current traffic ledger

The final hybrid stats and terminal log establish:

| Event | Lines or words | Transport / semantic consequence |
|---|---:|---:|
| index + value page publisher | 8,192 lines, 32 terminals | 524,288 B written |
| publisher full-credit stalls | 7,936 observations | only 88 issues overlap another same-MAA unit |
| coherent index ingestion | 4,096 lines | 65,536 index words |
| all-ones predicate ingestion | 4,096 lines | 65,536 predicate words |
| JIT value ingestion | 10,176 lines | 651,264 B; 2.484375x the aligned 4,096-line minimum |
| A RMW | 16,385 reads + 16,385 writes | 2,097,280 B |
| old-result retirement | 15,633 writes, 15,606 under pressure | 1,000,512 B to carry 262,144 useful B; 3.81665x line amplification |
| SoA/JIT stalls | 206,560 old-result + 404,039 context observations | directional counters, not additive cycles |

Ramulator suppresses its zero `WR` command field in this run.  The explicit
response-bearing MAA ledgers above are therefore the write evidence; a missing
Ramulator statistic is not evidence that no writes occurred.

The four per-core aligned arrays at `benchmarks/gapbs/src/sssp.cc:102-113`
allocate 1 MiB of ordinary coherent guest backing: 256 KiB each for index,
value, predicate, and old result.  The terminal's
`new_dedicated_payload_bytes=0` correctly says this is not hidden dedicated
hardware, but it must not be mistaken for zero external backing.

## Source audit

### `PublishSsspHybridPage`

`benchmarks/gapbs/src/sssp.cc:137-168` publishes one index page, waits for its
unique response-bearing completion, then publishes one value page and waits
again.  Four pages per logical window and four windows produce 16 index plus
16 value pages, or exactly 32 terminals and 8,192 64-byte writes.  The calls
sit inside the OpenMP critical section at `sssp.cc:802-836`; the final page
immediately launches and waits for the full logical RMW.  This deliberately
safe lifetime creates a hard producer/consumer phase boundary and explains
why only 88 of 8,192 publisher issues overlap another same-MAA unit.

The publication is semantically unnecessary for destination indices: the
already-present page-fed mechanism at `src/mem/MAA/IndirectAccess.cc:3675-3826`
reads a completed 4K index tile directly, inserts 16K exact ordinals into the
existing Row/Offset tables, requires ordered pages, and reports zero coherent
index read/write lines.  Its terminal contract at
`IndirectAccess.cc:5852-5878` also proves no epoch drain and no index backing.
What it does **not** solve is candidate-value lifetime.

### RangeLoop producer

The guest initializes `(last_i,last_j)=(0,-1)` once per frontier chunk and
reissues RangeLoop until all edges are generated (`sssp.cc:755-793`).  The
unit reloads those registers at `src/mem/MAA/RangeFuser.cc:93-100`, stops when
the physical tile is full, and writes the updated cursor back at
`RangeFuser.cc:245-247`.  Therefore a source adjacency can cross a 4K boundary
without duplication or omission.  The host model independently walks a
20,000-edge mixed-boundary example and obtains page sizes
`4096,4096,4096,4096,3616` with exact edge ordinals.

The producer already has both useful tiles resident at `sssp.cc:791-810`:
`tilev` contains destinations and `tile2` contains final candidates.  Making
them travel out to coherent memory and back is the avoidable break.  The
RangeLoop is not the observed performance cause.

### Row/Offset and operand lifetime

The matched configuration has 16,384 Offset entries and a 16,384-entry epoch;
the hybrid reports zero Offset epoch drains.  Thus the design already pays for
one logical window of routing metadata.  `OffsetTableEntry` is currently four
32-bit fields—`itr`, `wid`, `next_itr`, and `pass`—at
`src/mem/MAA/Tables.hh:52-57`.  Page-fed SoA/JIT does not use partition pass
tags.  A default-off FP32 inline-operand mode can therefore store the candidate
bits in `pass` without enlarging this implementation's entry.  It must bill
`inline_operand_live_bytes=65,536` to show that existing aux capacity is live;
`row_offset_incremental_bytes=0` is acceptable only together with that bill
and mutual exclusion from pass partitioning.

Without this reuse, A needs an additional 4-byte value per descriptor: 64 KiB
per live logical window.  That preserves a narrow “only 4K words in each SPD
tile” statement but refutes any broader “only 4K total payload storage” claim.
A design that keeps only the last value page is simply wrong; the executable
model has a two-destination counterexample where the correct result `(3,4)`
becomes `(10,9)`.

### SoA/JIT old-result path

Before each MIN, `applySoaJitValue` captures the destination word into the
bounded old-result buffer (`src/mem/MAA/IndirectAccess.cc:5456-5481`).  Buffer
pressure emits masked response-bearing lines (`IndirectAccess.cc:5523-5601`),
and instruction completion requires captures and all write responses to close
(`IndirectAccess.cc:5924-5943`).  Reordered A-line service scatters original
logical ordinals, explaining why 65,536 useful old words require 15,633 line
writes instead of the aligned minimum 4,096.

The old value exists only to reconstruct frontier winners.  It is not needed
for MIN final-distance correctness.  Removing it is safe only if another
mechanism proves that every strict final decrease produces finite future work.

### Frontier reconstruction

After the entire RMW completes, `RunSsspHybridWindow` scans each original 4K
page separately.  A reverse pass derives that page's last-alias final value;
a forward pass pushes lanes satisfying `candidate == final && old > final`
(`sssp.cc:318-348`).  Under the admitted single-owner, stable-source contract
and the SoA/JIT offset-chain order, this reproduces the legacy page-local
winner rule and was exactly correct in the micro.

It is not a reusable retirement interface.  It requires all three coherent
arrays, retains work-order semantics that shortest-path convergence does not
need, and becomes unsafe if aliases within a page cease to apply in original
chain order.  Cross-unit full-line read/modify/write is also not globally
linearizable merely because each unit is internally ordered.  The initial
prototype must retain the current critical section and cross-owner rejection;
future overlap needs an explicit line-ownership/atomicity proof.

## Alternatives A--D

### Correctness summary

| Alternative | Final-distance result with duplicates/conflicts | Progress result | Verdict |
|---|---|---|---|
| A: exact descriptor/value handoff | MIN is associative, commutative, and idempotent; any linearization gives the minimum if every lane and immutable value is handed off exactly once | A alone has no frontier interface; pair with C or D | viable substrate, incomplete alone |
| B: ordinary MIN + unconditional/stale pushes | all executed MINs still give correct final distances | **fails**: positive edges can form a same-bin cycle that pushes forever after reaching a fixed point | reject |
| C: post-update graph/snapshot recomputation | correct if a pre-wave snapshot is immutable, all MINs complete, and changed destinations satisfy `final < snapshot` | every strict wave decrease is pushed once; stale extra pushes are harmless | correct but traffic/backing-heavy |
| D: fused MIN + retirement | correct if MIN is globally linearizable and success is `candidate < old` at that linearization point | each strict decrease retires; finite decreases imply finite work; stale successes from later conflicts are safe | preferred |

The host search covers 81 duplicate/conflict candidate assignments and all 24
linearizations for each (1,944 schedules).  A, snapshot-C, and coupled-D always
produce the oracle final distances and preserve at least the final strict
decrease.  D intentionally produces 1,008 stale-but-safe retirement records
across those schedules.  Exact frontier order and multiplicity are not a
correctness requirement.

#### A proof obligations

An A handoff is correct only when the four pages carry exact ordinal,
destination, operand bits, generation, and page identity; the operand field
remains immutable until its descriptor is consumed; pages are neither missing
nor duplicated; and the 16K epoch cannot drain early.  Destination conflicts
are harmless only after the eventual MIN implementation supplies a single
linearization order.  With these obligations, A changes transport, not the
mathematical set of relaxations.  It preserves 4K physical SPD and adds 16
control bytes per unit in the existing page-fed state.  Inline FP32 uses
64 KiB of already-provisioned Offset aux bits; without aux reuse it adds 64 KiB
SRAM and loses the broad 4K-storage claim.

For the micro, A expects 65,536 index and 65,536 value SPD-word reads, 65,536
Row/Offset insertions, zero coherent index/value publications, and zero
coherent index/value reads.  It does not reduce the 16,385 A reads/writes.

#### B counterexample

Let delta be 4, with positive edges `0 --1--> 1` and `1 --1--> 0`, final
distances `(0,1)`, and frontier `(0,1)` in bin zero.  Both sources pass the
production lower-bound active test.  Neither MIN changes a distance, but
unconditional retirement creates frontier `(1,0)`, which creates `(0,1)`,
forever.  Positive weights and stale-entry filtering do not help because the
entries remain in the same bin.  B removes 15,633 old-result write lines and
adds no SRAM, but it cannot be promoted regardless of its micro time.

#### C proof and cost

At a barrier, snapshot every source/destination value that the wave may use.
Compute both the initial RMW operands and the post-wave graph replay from that
immutable snapshot.  After all MINs linearize, reload final destinations and
push each `final < snapshot[destination]` once.  A strict final decrease cannot
be missed; duplicates collapse safely; a concurrent later decrease either is
seen here or has its own successful push.  Reading only the post-update graph
without the pre-wave destination snapshot is insufficient because “candidate
equals final” cannot distinguish a new decrease from an already-equal value.

The simple whole-distance snapshot for this micro is 69,633 FP32 words
(278,532 B external backing), written as 4,353 lines.  Relevant snapshot reads
are about 4,352 lines, graph replay reads 8,192 lines for 65,536 eight-byte
edges, and post-distance reads are 16,385 observed A lines.  It removes old
results but retains current publication unless combined with A.  It preserves
4K SPD only in the narrow sense; storage scales with graph/frontier size rather
than fixed hardware geometry.  Use C as a correctness oracle or fallback, not
the default datapath.

#### D proof and cost

For each linearizable MIN, compare candidate with the old value at the same
linearization point.  If strict, mark the destination word in its active A-line
context.  Once the updated A-line WriteResp arrives, emit `(destination,
new_value)`; do not expose the record earlier.  Multiple aliases in one A line
may collapse to its final successful value.  Across lines or owners, stale
success records are allowed, but the record for the final strict decrease must
exist.  Because a successful MIN strictly lowers a bounded nonnegative value,
only finitely many success records can occur for finite input, and the normal
bin loop eventually processes the final one.

Eight active A contexts require sixteen success-mask bytes (16 destination
words per line).  A conservative eight-line response-credit store is 512 B,
a line packer 64 B, and control 16 B: 608 incremental SRAM bytes per unit.  A
4K-record ordinary coherent ring is at most 32 KiB live external backing per
unit; it is drained and may backpressure the RMW, so it is not a hidden 16K
queue.  The all-success micro emits 65,536 eight-byte records, exactly 8,192
dense line writes.  A reads/writes remain 16,385 each; old-result writes become
zero.  This preserves physical 4K SPD.

## Default-off prototype: inline-operand conditional RMW

### Architectural contract

Add a generic mode with these semantics:

1. Open one logical instruction and generation.  Admit four ordered pairs of
   completed physical index/value pages.  Each pair is read before either tile
   may be reused.
2. Insert destination routing metadata into the existing 16K Row/Offset epoch
   and store 32-bit operand bits in the mutually-exclusive Offset aux field.
   No predicate is needed for an all-selected page.
3. Close only after exactly 16,384 ordinals.  No A request may issue earlier;
   no Offset epoch drain is legal.
4. Apply ordinary typed MIN/ADD/MAX logic.  For a requested comparison mode,
   retain a per-A-word success mask.  Retirement records are response-gated to
   the A-line write, densely packed, and acknowledged.
5. Terminal closure proves exact admission, exact operand consumption,
   balanced A read/write responses, balanced retirement responses, no live
   ring record, and zero coherent index/value/predicate/old-result traffic.

The first implementation supports 32-bit inline operands only.  It must reject
64-bit data rather than silently allocating another payload.  Partition-pass
and inline-operand modes are mutually exclusive.  Retain the current OpenMP
critical section and admission rules for the first screen.

### Explicit source plan

1. `include/gem5/maa_page_fed_soa_abi.hh`: add a generation-bound paired-page
   admit action with index and value tile identities, exact page ordering, and
   no command queue.
2. `benchmarks/API/MAA_gem5.hpp`: add generic open/admit/close wrappers for an
   inline-operand RMW and a bounded retirement ring address/size.
3. `src/mem/MAA/Tables.hh` and `Tables.cc`: add typed aux accessors without
   changing `OffsetTableEntry` size; poison/check aux on free and forbid pass
   use in inline mode.
4. `src/mem/MAA/IndirectAccess.hh` and `IndirectAccess.cc`: ingest paired SPD
   words into Row/Offset, bypass predicate/value feeders, use aux bits in
   lookahead, track per-context success masks, gate dense retirement on A
   WriteResp, and fail closed on every lifetime/accounting mismatch.
5. `src/mem/MAA/CpuSidePort.cc`, `MAA.py`, `MAA.hh`, and `MAA.cc`: decode the
   command, add a default-false selector, configuration legality, counters,
   storage ledger, and terminal invariants.
6. `configs/common/Options.py` and `MAAConfig.py`: expose the selector, default
   off, without changing existing arms.
7. `benchmarks/gapbs/src/sssp.cc` and its Makefile: add a new compile-time arm;
   keep the old-result arm intact as a control.  The new arm supplies page
   pairs and consumes response-closed retirement records into ordinary bins.
8. Add focused C++ tests for ABI ordering, aux lifetime, duplicate MINs,
   same-line aliases, sink backpressure, WriteResp-before-record visibility,
   interrupted generations, capacity failure, and storage accounting.  Keep
   the Python exhaustive model as the semantic oracle.

### Acceptance thresholds

No native rerun is needed.  Freeze the accepted graph, native4/native16 stats,
binary/config identities, and exact fingerprint.  Build and run only the new
candidate after host/unit tests pass.

The candidate micro passes only if all of the following hold:

- exact fingerprint:
  `vertices=69633 reached=69633 unreachable=0 distance_sum=135168 max_distance=2 hash_a=a0531a7ddb9387df hash_b=39f1ea63bc8817e8 triangle_violations=0 missing_predecessors=0 nonpositive_weights=0 negative_distances=0 result=PASS`;
- normal wrapper RC, one terminal exit, final nonempty stats, and no
  panic/fatal/assert/abort/error marker;
- the frozen native16 locality direction remains established
  (`618,231,027 < 672,489,890`, `148,768 < 345,420` cache lines, and
  `18,696 < 43,416` rows), and the candidate itself remains on that useful
  direction: fewer than 345,420 cache-line insertions and 43,416 rows;
- candidate speedup `native4_ticks / candidate_ticks >= 0.8`, equivalently
  candidate `simTicks <= 840,612,362` against the frozen native4 observation;
- exactly four logical operations, sixteen paired page admissions, 65,536
  exact ordinal/operand insertions and consumptions, zero Offset epoch drains,
  and 4K physical SPD geometry;
- zero index/value publisher lines and terminals, zero coherent
  index/predicate/value reads, zero old-result captures/writes, balanced A
  reads/writes, exactly 65,536 success records and 8,192 dense retirement
  write responses for this all-unique-decrease graph;
- `inline_operand_live_bytes=65,536`, `row_offset_incremental_bytes=0`, no
  hidden/dedicated logical payload, at most 1,024 incremental SRAM bytes per
  indirect unit, and at most a 32-KiB live external retirement ring per unit;
- response closure and all count/storage invariants equal one.

A single candidate observation meeting these thresholds is an initial micro
screen, not architecture promotion.  Repeat the candidate to assess
variability and independently review the raw mechanism signature before any
full S22 run.  Failure of any correctness, storage, traffic, locality, or 0.8x
threshold keeps the full run prohibited.

## Executable host evidence

`experiments/analysis/sssp_low_traffic_redesign_model.py` prints the frozen
counter arithmetic, per-option storage/traffic ledger, exhaustive correctness
summary, and B counterexample as JSON.  The tests bind it to the current SSSP,
RangeFuser, Row/Offset, page-fed, and old-result source shapes.

Run:

```sh
python3 -m unittest experiments.tests.test_sssp_low_traffic_redesign_model -v
python3 experiments/analysis/sssp_low_traffic_redesign_model.py --pretty
```

These are host cost/correctness models only.  No production source, native
binary, checkpoint, gem5 run, or full application was changed or launched in
this audit.
