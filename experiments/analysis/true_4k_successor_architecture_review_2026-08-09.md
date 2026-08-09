# True-4K successor architecture review

Date: 2026-08-09

## Findings

1. **Replay recovered the desired A-side locality, but paid for it in the
   fill path.** In the accepted `9ddf1ad3` matrix, physical-grow replay
   reduced A line issues from native16's 10,576 to 9,603, row insertions from
   1,472 to 1,260, row drains from 846 to zero, DRAM reads from 25,783 to
   24,107, and activates from 4,510 to 3,848. It nevertheless took
   66,685,589 ticks versus 41,346,674 for native16: **61.284% slower**.
   The mechanism reads B once for the summary and four complete times for
   replay (81,920 words and 5,125 cache lines total), introduces a hard
   summary/plan barrier, and routes results through the virtual backing and
   page consumer. Its request stage is 3,568,200 cycles faster than native16,
   but its fill stage is 23,730,408 cycles slower. The A/DRAM savings are
   real; they are simply much smaller than B replay, phase, and C-retirement
   costs.

2. **Online oldest-grow removes the four B replays but cannot see enough of
   the future to reproduce global grow grouping.** The accepted
   `0ad5d2de`/`88805b81` arm reads B once (16,384 words, 1,025 lines) and takes
   56,700,576 ticks, **41.988% slower** than its 39,933,479-tick native16
   control, a 1.0401x speedup over native4 (3.855% fewer ticks), and a
   1.1762x speedup over the matched replay reference (14.979% fewer ticks).
   The 16K logical stream interleaves nine grows. With a
   4K window, oldest-grow evicts a grow before all of its future descriptors
   arrive and later reopens it: 20 row-full victims and 20 reopens. It issues
   15,360 A lines, 55.813% more than native16's 9,858 and 93.75% of native4's
   16,384. Its fill stage is 9,783,754 cycles slower than native16 and its
   request stage is also 1,118,036 cycles slower. One B pass is not enough to
   learn the whole-operation grouping unless descriptors not currently
   resident have somewhere finite and timed to wait.

3. **A timed descriptor spool can retain the replay plan's A-side grouping
   without four B replays, but `ab9666f6` is not performance evidence.** Its
   structure demonstrates the useful primitive: a timed summary scan, a
   timed bucket scan, four finite external pass segments, bounded line
   staging, acknowledged writes, and bounded replay reads. The candidate
   reached terminal `m5_exit`, reported `errors=0` and the expected output
   hash, and closed its 16,384 descriptors and 2,048 spool writes/reads.
   However, the matrix failed closed: `matrix.exit=1`, no `matrix.complete`,
   no candidate `result.tsv` or pass marker, and the launcher reports
   `invalid partition-filter inspections: 33870/32768`, exactly 1,102 extra
   inspections. Its 62,497,962 raw ticks and apparent 4.83% advantage over
   the local replay arm are quarantined. A separate worker is resolving that
   1,102-inspection accounting defect; this review neither assumes its cause
   is fixed nor uses the arm for a speedup claim.

4. **The measured replay and spool source are locality references, not
   strict implementations.** At `9ddf1ad3`, every admitted operation inserts
   Word, cache-line, and row addresses into unbounded `std::set` objects
   (`IndirectAccess.cc:1685-1688`, declarations at
   `IndirectAccess.hh:347-349`). Those sets select the cache route at
   `IndirectAccess.cc:2273` and are reported at retirement at
   `IndirectAccess.cc:2941-2947`; they therefore are not harmless trace-only
   state. `ab9666f6` retains the same pattern at
   `IndirectAccess.cc:2203-2207`, `IndirectAccess.hh:372-374`,
   `IndirectAccess.cc:2838`, and `IndirectAccess.cc:3553-3559`.
   `BoundedRangePass.hh:108-110,136-138` also allocates logical-size admitted
   and retired bit vectors for checking. A strict successor must remove the
   operation-sized sets and live bitmaps, use fixed arrays/CAMs only, select
   A routing from bounded operation policy, and move exhaustive checking to
   non-functional trace validation.

5. **Recommendation:** build and measure a **resident-first counted bucket
   spool**. A runtime, timed summary creates four grow-aware populations of
   at most 4,096 descriptors. A second and final B scan places one population
   directly in the active Word/Offset/RowTable state and writes only the
   other three to finite coherent backing. Each external descriptor is a
   densely packed 48-bit record. The design retains the replay plan's
   whole-operation grow knowledge while reducing B to two scans and avoiding
   one quarter of the spool. Its expected first bottleneck is C backing and
   retirement, followed by spool-read credit/LLC interference—not A DRAM.
   The fallback is the accepted online-oldest mechanism after its
   `0ad5d2de` deadlock fix.

## Evidence status and provenance

The comparisons below use only complete, correctness-passing matrices for
performance conclusions. Counts from different run roots are not mixed into
a speedup.

| Mechanism | Native source and report | Direct evidence | Classification |
| --- | --- | --- | --- |
| Physical-grow replay | mechanism `9ddf1ad3`; report/results `6e84c2c4:experiments/true_4k_reorder_2026_08_08/{report.md,results.json}` | `/data1/nier/dx100-runs/2026-08-08-true-4k-reorder/9ddf1ad3` | Accepted: `matrix.exit=0`, `matrix.complete`, all checkpoint/restore exits zero, pass markers present, exact output hash `7228541527853630339` |
| Online oldest-grow | initial mechanism `2f20dc55`; deadlock repair `0ad5d2de`; report `88805b81:experiments/analysis/true_4k_online_row_window_2026-08-08.md` | `/data1/nier/dx100-runs/2026-08-09-true-4k-online-row-window/0ad5d2de` | Accepted for `0ad5d2de`: complete shared-checkpoint matrix, exits zero, pass markers and exact hashes; the initial `2f20dc55` run that stopped at iteration 3,883 is negative evidence only |
| Descriptor spool | mechanism `ab9666f6` | `/data1/nier/worktrees/codex-coordination/sessions/true4k-llc-bucket-20260808-232857-fe0b3e72/evidence/true4k_descriptor_spool_ab9666f6_2gb` | Rejected for performance: terminal/correct raw run, but matrix and post-validation failed closed on 33,870 versus 32,768 filter inspections |

Replay used gem5 binary SHA-256
`64980714...f1e`, workload `f87d7206...dfc5`, and Ramulator
`76ea3a9c...5753`. The online matrix used candidate binary
`f08378...`, the same workload and Ramulator, and its own matched controls.
The spool evidence used binary `f89d5...` and workload `fdfc...`; that
additional identity difference is another reason not to splice its raw
ticks into either accepted cohort.

## Why the measured mechanisms behave this way

### Replay: good A ordering, expensive materialization

The bounded planner in
`9ddf1ad3:src/mem/MAA/BoundedQuantileRanges.hh:280-391` collects grow
populations, constructs a maximum-four-pass quota plan, and splits any grow
that crosses a pass capacity. The replay state at lines 456-500 consumes one
selected quota per pass; lines 512-523 account the bounded planner storage.
The measured plan contained 64 records, four exact 4,096-descriptor quotas,
and active high-water marks of 4,096 Word descriptors, 4,096 Offset
descriptors, 512 row directories, and 4,096 RowTable lines. This is why it
can eliminate intermediate row/offset drains and match or beat native16's
A-line and DRAM counts.

The cost is upstream and downstream:

| Replay-cohort metric | native16 | native4 | physical-grow replay |
| --- | ---: | ---: | ---: |
| `simTicks` | 41,346,674 | 59,297,850 | 66,685,589 |
| B words / B lines | 16,384 / 1,025 | 16,384 / 1,025 | 81,920 / 5,125 |
| A line issues | 10,576 | 16,384 | 9,603 |
| row insertions / drains | 1,472 / 846 | 2,103 / 3,589 | 1,260 / 0 |
| DRAM reads / activates | 25,783 / 4,510 | 31,624 / 5,115 | 24,107 / 3,848 |
| fill cycles | 3,878,696 | — | 27,609,104 |
| request cycles | 34,480,393 | — | 30,912,193 |

The five B passes consist of a 16,384-word/1,025-line summary plus four
16,384-word/1,025-line replay scans. The filter itself records 6,413 cycles
and 1,301 wait cycles across 14 intervals, but the larger cost is the serial
scan/barrier/materialization structure. The request interval improves
10.35%; the fill interval expands 611.8%, producing a net stage increase of
20,163,147 cycles.

The benchmark also makes native16 and true-4K retirement materially
different. The native path directly chains gather, ALU, and destination
store (`9ddf1ad3:benchmarks/API/test_virtual_tile_consumer.cpp:171-196`).
The true-4K path first writes the logical gather into backing and then, page
by page, reloads backing, runs the ALU, and stores the destination (lines
197-285). Accordingly, the replay indirect unit records 2,941 virtual write
issues/completions while the native indirect unit records zero. Low A-line
and DRAM totals cannot pay back five B scans plus this C/backing path.

### Online oldest-grow: bounded immediacy fragments grows

`2f20dc55:src/mem/MAA/OnlineRowWindow.hh:95-171` defines the bounded live
window; selection at lines 138-171 chooses the oldest grow. The integration
scans bounded victims at `IndirectAccess.cc:756-798`, issues the selected
grow at lines 2537-2542, and closes it at lines 2823-2852. `0ad5d2de` fixes
the observed iteration-3,883 deadlock by preventing refill while a victim is
active. The accepted arm charges a 12,416-byte policy, visits its 512-entry
ledger 10,240 times, and reaches high-water marks of 3,944 descriptors,
3,883 lines, and 512 rows.

It cannot distinguish “this grow is finished” from “this grow has no more
descriptors in the current 4K window.” With nine grows interleaved through
the logical order, the oldest grow is issued, evicted, and then reopened.
That turns a global grouping problem into a succession of locally reasonable
but fragmented decisions. The result is nearly native4 A behavior:

| Online-cohort metric | native16 | native4 | replay9dd | online oldest |
| --- | ---: | ---: | ---: | ---: |
| `simTicks` | 39,933,479 | 58,974,208 | 66,689,971 | 56,700,576 |
| A line issues | 9,858 | 16,384 | — | 15,360 |
| row insertions | 1,468 | — | — | 2,096 |
| fill cycles | 4,151,632 | — | — | 13,935,386 |
| request cycles | 33,302,574 | — | — | 34,420,610 |

The arm also hardwires `my_force_cache=true` for this policy at
`0ad5d2de:src/mem/MAA/IndirectAccess.cc:2417-2420`. Its low 13,656 DRAM-read
and 1,080-activate counts therefore partly reflect routing policy and are not
evidence that the lost grow ordering is harmless. Online records 2,053
virtual C write issues versus zero for native indirect.

### Descriptor spool: useful structure, failed measurement

`ab9666f6:src/mem/MAA/BoundedDescriptorSpool.hh:28-42` defines an 8-byte
record (`uint16_t iteration`, `uint16_t sourcePage`, `uint32_t value`), four
64-byte append buffers, 16 acknowledged write slots, and four replay-read
lines. The run reached these internally consistent closure counts:

- summary: nine grows and an accepted four-pass plan;
- bucket scan: 16,384 descriptors, 2,048 line writes and acknowledgements,
  131,328 reserved bytes, 32 active staging descriptors, and write HWM 16;
- replay: four times 4,096 descriptors and 512 lines, with 2,048 read issues
  and responses total;
- active state: at most 4,096 descriptors and 4,096 lines;
- raw A behavior: 9,523 line issues, consistent with retaining the replay
  plan's locality rather than online-oldest fragmentation.

It uses only two B scans (summary plus bucket), then replays descriptors from
timed backing. Thus the architectural answer is **yes**: external stable
partitioning can retain whole-operation grow-aware pass membership without
four full B replays. It is not an exact native16 global issue-order promise;
each 4K population is still independently materialized and drained. The
next experiment must compare pass-membership and A-issue digests with replay
and measure how much ordering survives.

The failed postcondition remains decisive. The source increments
`num_direct_index_filter_words` before some write-credit retry exits
(`ab9666f6:src/mem/MAA/IndirectAccess.cc:1857-1859`, with relevant stalls at
1892-1895 and 1982-1985). That placement is a plausible retry-accounting
lead, not a concluded diagnosis; the separate accounting worker owns the
resolution. Until its rerun produces a complete matrix and exact expected
inspection counts, all spool timing is diagnostic only.

## Recommended design: resident-first counted bucket spool

This is a bounded stable partition, not a free sort. It keeps the useful
`ab9666f6` structure, removes its hidden operation-sized state, compacts the
record, and avoids writing and rereading one of the four populations.

### State contract and exact descriptor metadata

For this fixed logical-16K treatment, the functional descriptor minimum is
**46 bits**:

| Field | Bits | Location | Reason |
| --- | ---: | --- | --- |
| B/index value | 32 | active Word entry or external descriptor | reconstructs A virtual address, translated grow, line, and word offset |
| logical iteration | 14 | active Offset entry or external descriptor | identifies one of 16,384 logical C/backing destinations |
| pass/bucket | 0 per descriptor | implicit in the selected fixed segment | the runtime plan chooses the segment before append |
| grow key | 0 per descriptor | derived from value and bounded operation translation state | storing it would duplicate information |
| B source page | 0 per descriptor | derived from iteration if diagnostic provenance is needed | `sourcePage` in the 8-byte prototype is redundant; a fixed maximum-17-page operation map can reconstruct it |
| valid/length | 0 per descriptor | fixed population counters delimit each segment | no sentinel scan is allowed |

Pack the 46 functional bits in a **48-bit (6-byte) dense byte stream**. A
record may cross a 64-byte line; a fixed at-most-five-byte carry register
handles the boundary. No decoded descriptor queue is permitted.

One selected population lives directly in the existing active Word and
Offset tables. The other three live in three pre-reserved, coherent,
timing-visible segments, each capped at `4096 * 6 = 24,576` bytes (384 cache
lines). Total external payload/capacity is therefore **73,728 bytes and 1,152
lines per direction**, not an operation-sized host container. Reservation,
address-range registration, cache misses, eviction, and DRAM service are all
ordinary timed behavior. The arena cannot alias A, B, C/backing, destination,
checkpoint memory, or another live operation.

The remaining live control is fixed:

| Structure | Hard cap |
| --- | ---: |
| active Word descriptors / active Offset descriptors | 4,096 / 4,096 |
| RowTable line state / row directories | 4,096 / 512 |
| B input feeder | 4 cache lines |
| external append staging | 3 x 64-byte lines plus 3 x at-most-5-byte carries |
| outstanding spool writes | 16 fixed scoreboard entries |
| outstanding spool reads | 4 fixed tagged line slots, consumed in line order |
| virtual A response slots / packed words | 96 / 480 |
| bounded C combiner | existing 384 lines / 4,096 words |
| grow plan | at most 64 fixed 128-bit entries = 1,024 bytes |

Each grow-plan entry contains a 32-bit translated-grow key, a 15-bit total
population, four 13-bit pass quotas, a 15-bit classification cursor, and a
valid/flag bit: 115 semantic bits, padded to a fixed 128 bits. Four exact
pass-population counters, fixed scan cursors, operation identity, and arena
bounds are scalar control. Summary histogram storage is phase-shared with
this plan and is cleared before active descriptors are admitted. A 65th grow,
an unsplittable quota, or a population above 4,096 fails closed; none may
select an iteration fallback.

All `std::set`, `std::map`, logical-size vector, and logical-size queue use
must disappear from functional/timing decisions. Fixed arrays may be used
to implement the scoreboards above. Exact per-iteration admission/retirement
checking belongs in a trace post-validator and must not feed simulation
state, timing, routing, or completion.

### Macro event timeline

1. **Reserve and initialize.** At dispatch, reserve the three 24,576-byte
   arena segments and clear the 64-entry grow table and scalar transaction
   counters. Reject overlap, bad alignment, unsupported logical size, or
   unavailable backing before consuming B.

2. **Timed summary scan.** Read B once through the four-line feeder. For each
   accepted word, perform the ordinary address translation, update one of
   the 64 fixed grow counters, and only then advance the B cursor. Predicate
   rejects, if supported, receive an explicit bounded class and exact quota;
   they are not silently dropped. Charge table probes and translation/filter
   bandwidth.

3. **Timed plan formation.** After the final B response, serially scan the
   at-most-64 grow records and assign/split quotas into four populations of
   at most 4,096. Charge every planner scan/compare cycle. Choose pass zero as
   resident by a deterministic runtime rule; the baseline carries no free
   oracle knowledge of which population will seal first. For exact logical16K
   with four legal populations, each is 4,096.

4. **Second and final B scan.** Re-read B once. Translate and consult the
   fixed grow record plus its ordinal. If the descriptor belongs to the
   resident population, admit it directly to the active Word/Offset/RowTable
   state. Otherwise append its 48-bit record to the appropriate coherent
   segment. A B word is committed exactly once: classification counters,
   inspection counters, and the B cursor advance only after the active-table
   insert or complete spool-record append has succeeded.

5. **Seal and drain the resident population.** When its exact expected count
   has been admitted, its membership is final even if later B words target
   external buckets. It may begin grow-aware A issue while remaining spool
   writes proceed, subject to ordinary shared-port arbitration. The first
   implementation should also provide overlap-off mode so any benefit is
   measured rather than assumed.

6. **Replay three sealed segments.** A segment is readable only after its
   descriptor count, final partial-line write, all line-write issues, and all
   acknowledgements agree. Fetch at most four lines, unpack in line-number
   order into empty active tables, and admit no more than its exact population
   (at most 4,096). Once materialized, issue A in the same RowTable policy as
   replay and retire its C/backing effects before reusing active state for the
   next segment.

7. **Commit and reclaim.** Operation completion requires both B scans exact;
   every planned quota classified; all spool writes acknowledged; all three
   segments' read issues equal read responses; per-population admits equal A
   retirements; active Word/Offset/RowTable state empty; all A responses
   consumed; the C combiner empty; every C write acknowledged; page-ready and
   final completion notifications delivered. Only then may the arena be
   reclaimed.

### Backpressure and hazards

- If an append would fill a line, reserve write credit and the next empty
  staging state before altering the current record. With no credit, preserve
  the B word, ordinal, inspection counter, and partial bytes unchanged.
- A full A-response pool or C combiner stops new A issues and prioritizes
  bounded C drain. It must not trigger early RowTable reuse.
- Four read tags carry operation generation, segment, line number, descriptor
  count, and valid state. A stale, duplicate, out-of-range, or post-reclaim
  response fails closed. Out-of-order responses wait in these four slots;
  they do not enter a host map.
- The dense format must explicitly test all six line-boundary phases, final
  partial-line zeroing, endian packing, and a descriptor split across lines.
- The grow translation used for summary, classification, and replay must be
  identical. A mismatch, 65th grow, quota overrun, RowTable insertion beyond
  4,096 lines, or descriptor count disagreement is fatal—not a fallback to a
  16K vector or another B scan.
- Coherence must order spool write acknowledgement before replay read; arena
  generations prevent an old response from matching a new operation.
- Page readiness cannot lead the last backing write, and overall completion
  cannot lead the last destination/C acknowledgement. These are explicit
  counters, not inference from an empty input queue.
- Cache routing is declared from bounded policy for the whole operation. It
  may not be selected from a unique-address set accumulated over 16K inputs.

### Traffic expectation and likely bottleneck

Worst-case auxiliary line transfers are:

| Design | B reads | spool writes | spool reads | total auxiliary lines |
| --- | ---: | ---: | ---: | ---: |
| replay `9ddf1ad3` | 5,125 | 0 | 0 | 5,125 |
| all-four 8-byte spool `ab9666f6` | 2,050 | 2,048 | 2,048 | 6,146 |
| resident-first 6-byte spool | 2,050 | 1,152 | 1,152 | 4,354 |

The proposed design moves 15.04% fewer auxiliary cache lines than replay and
29.16% fewer than the all-four prototype. This arithmetic does **not** claim
equal cost per line: B reads, LLC hits, LLC pollution, and DRAM-backed spool
lines have different latency and contention. The plan and per-pass
membership should reproduce replay-like A locality (roughly 9.6K rather than
online's 15.4K A-line issues), but only an issue digest and matched run can
establish that.

C retirement is the expected limiter. Accepted replay already shows that
the fill/backing path overwhelms its A savings, and all true-4K arms add
virtual C writes that native indirect lacks. The rejected spool trace also
shows 1,105 write-credit stalls, 18,280 read-credit stalls, and 2,683 C write
issues; these are hypotheses about where to instrument, not accepted timing
evidence. After C, the likely limit is the four-line spool read window plus
LLC/DRAM interference. The unavoidable timed summary barrier is the third
candidate.

## Fallback: fixed online oldest-grow

If finite coherent backing, C pressure, or spool-read interference erases the
ordering benefit, retain the `0ad5d2de` online-oldest mechanism as the
fallback. It is the only reviewed candidate with a complete strict-4K,
single-B-pass performance result and the victim/refill deadlock repaired. It
is measured at 41.988% behind native16 but at a 1.1762x speedup over replay
(14.979% fewer ticks) in its matched matrix. Its fixed 512-entry ledger and
explicit active high-water
marks make the state bound auditable, and its predeclared cache route avoids
the operation-sized uniqueness sets used by replay/spool.

This is a throughput fallback, not the answer to the ordering objective: it
retains almost native4 A-line behavior and must not be described as preserving
native16 grouping. Replay remains the locality oracle, not the fallback,
because it is slower and its current source violates the strict host-state
constraint.

## Required attribution matrix

Every arm must use the same source commit except the named treatment switch,
the same binary and libraries, the same workload, the same checkpoint digest,
the same cache-routing declaration unless routing itself is the ablation, and
the same correctness validator. Require zero exits, terminal marker, exact
destination and backing hashes, exact descriptor/filter counts, pass marker,
and `matrix.complete` before comparing `simTicks`.

1. **Controls and format ladder.** Run native16, native4, fixed online-oldest,
   strict replay, strict all-four 8-byte spool, strict all-four 6-byte spool,
   and resident-first three-segment 6-byte spool. The 8-to-6 comparison
   isolates record density; all-four versus resident-first isolates the
   resident population.

2. **B traffic attribution.** Tag summary, bucket, and replay B requests
   separately. Report words, line issues, responses, cache hits/misses, DRAM
   reads/activates/precharges, service latency, filter cycles, retry cycles,
   and first/last tick for each tag. Compare replay and spool with the exact
   same runtime plan; never inject a plan computed offline.

3. **LLC spool traffic.** Report bytes, line writes/issues/acks, partial
   writes, line reads/responses, LLC hit/miss/eviction/writeback, DRAM spill,
   write/read credit stalls, high-water marks, and per-segment seal ticks.
   Sweep read credits 1/2/4 and write credits 1/4/16. A deliberately
   LLC-resident arena and a deliberately capacity-pressured timed arena are
   useful endpoints; a zero-latency or infinite “ideal spool” is not evidence.

4. **Planner and classification scans.** Charge summary histogram probes,
   bounded 64-record planner scans/compares, classifier lookups, and ordinal
   commits. Sweep classifier bandwidth 1/4/16 words per cycle with the same
   plan and memory traffic. Separately report plan time, B wait time, and
   write-credit retry time so the 1,102-inspection defect cannot hide in an
   aggregate fill number.

5. **A locality and ordering.** With spool traffic held constant, compare
   runtime grow-aware quotas against a bounded iteration-balanced assignment.
   Report A line issues/responses, offline trace-derived unique lines, row
   insertions/transitions/drains/reopens, cache hits/misses, DRAM row-buffer
   hits, activates/precharges, and a digest of `(pass,grow,line,iteration)`
   issue order. Require the grow-aware pass membership to match the strict
   replay oracle. Unique-address analysis may postprocess a trace; it may not
   alter cache routing or simulated timing.

6. **C/backing retirement.** Compare the same bounded combiner retained across
   pass boundaries against a forced bounded drain at each boundary. Report
   full/partial writes, RMW reads, combiner evictions/stalls/HWM, backing and
   destination bytes, write issues/acks, page-ready-to-last-write ordering,
   and final retirement span. Preserve exact backing and destination hashes.

7. **Overlap.** For resident-first only, compare seal-and-drain overlap on and
   off with identical populations. Record B last-response, each bucket's last
   write issue/ack, first A issue, last A response, last C ack, and completion
   ticks. This attributes overlap rather than folding it into “spool speed.”

If repeated runs are not deterministic, use multiple repetitions and report
the distribution; do not select a favorable run. Promotion requires accepted
correctness, an audited state-cap table, replay-like A locality on matched
traffic, and end-to-end improvement over fixed online-oldest. Proxy or raw
failed-arm timing is insufficient.

## Explicitly rejected designs and claims

- A 16K WordTable, OffsetTable, RowTable, reorder buffer, host vector, map,
  set, queue, bitmap, or “temporary” descriptor arena inside the mechanism.
- A free/offline histogram, global descriptor sort, oracle pass assignment,
  or precomputed translated-grow plan. Only a timed runtime summary and a
  fixed 64-record control sort/scan are legal.
- Four complete B replay scans. The recommended mechanism has exactly two B
  scans; all later materialization comes from finite timed backing.
- Infinite, pinned-for-free, zero-latency, non-coherent, or unaccounted LLC
  spool storage. Cache pollution and DRAM spill are part of the result.
- Iteration fallback, silent grow coalescing, or dynamically enlarged state
  on overflow. Unsupported shape fails closed before architectural effects.
- Cache-routing decisions based on operation-sized unique-address sets.
- A claim that low DRAM counts alone prove better reordering when treatments
  hardwire different cache routes.
- Any speedup or bottleneck claim from the failed `ab9666f6` candidate until
  the separate 1,102-inspection repair produces a complete validated matrix.

The next candidate is therefore narrow: strict bounded control, one resident
4K population, three timed 24,576-byte descriptor segments, two B scans,
three spool replays, replay-equivalent runtime grow quotas, and explicit C
closure. Anything larger or computed for free is not a true-4K successor.
