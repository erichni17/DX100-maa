# Hybrid A-Row / C-Page Scheduler for the 4K SPD Cache

## Decision

Do not globally choose either a monolithic 16K A-row schedule or the current
four independent 4K epochs.  Implement a finite, per-logical-tile mode gate and
a page-focused completion buffer:

- use the full-row schedule when cross-page A-line sharing is below 1/16;
- otherwise use **PFCC-64** (Page Focus with 64 destination-line Completion
  Credits): retain full 16K Row/Offset visibility, split each A descriptor's
  Offset chain into four 4K-page subchains, focus the oldest page, and carry
  future-page values only in a bounded 64-line buffer which emits complete C
  lines;
- never inject a partial future-page line into the existing C combiner; if the
  64-line carry buffer has no free entry, leave that Offset work unconsumed and
  refetch the A line in its later page, at most once per page.

This policy is finite: four pages, 16,384 A-line descriptor slots, 16,384 Offset
entries, 32 Row-Table slices, 64 rows per slice, eight A-line entries per row,
a maximum 128-line DRAM grow quantum, and 64 carry lines.  It performs no
unbounded sort.

The checked replay supports the conflict and rejects a tempting simpler
policy.  It does **not** predict PFCC latency.  No gem5 run was launched and no
simulator source was changed.

## Why the two existing results conflict

The controlled full-XRAGE comparison holds the physical SPD at 4K and the Row
capacity at 4K while changing the Offset epoch:

| XRAGE arm | ROI ticks | inserted A lines | inserted rows | C writes |
|---|---:|---:|---:|---:|
| Row4 / epoch16 | 1,149,406,425 | 302,676 | 39,830 | 327,924 |
| bounded4 | 1,083,316,475 | 322,414 | 43,452 | 262,903 |

Bounded4 is 5.750% faster even though it inserts 19,738 more A cache lines and
3,622 more row descriptors.  It wins by eliminating 65,021 C writes.  Full
visibility therefore improves A-line/row reuse, while page-sized scheduling
improves C-line completion order.  Neither signature can be treated as a proxy
for the other.

FLAG has the opposite default.  Across 14 exact-output, same-binary gathers,
changing only the Offset epoch from 16K to 4K changes geometric-mean latency by
-1.051%, with individual effects from -7.412% to +1.209%; shrinking capacity at
the matched 4K epoch changes timing and traffic by exactly zero.  On the
representative `001.fp/config_00_gather`, full-descriptor direct4 takes
36,662,629 ticks and bounded4 takes 37,737,471 ticks, so full visibility is
2.93% lower.  The broad bounded4-versus-compact16 comparison is 5.290% slower
geometrically, with only 2/14 wins; those two wins are precisely the two inputs
with thousands of avoidable compact C writes.

The design problem is therefore not “pick the smaller structure.”  It is “keep
future-page outputs out of the C combiner unless they can complete a line,
without always paying four A fetches.”

## Exact PFCC-64 algorithm

### Fill and mode gate

For every Row-Table A-line descriptor, derive a four-bit `page_mask` from the
existing Offset `itr` field: `page = itr >> 12`.  Maintain four head/tail pairs
over the existing Offset entries, one pair per page.  An Offset entry remains
the existing `(itr, wid, next)` record; insertion links it only into its page's
subchain.

After the 16K logical tile is scanned, compute:

```text
U = number of valid A-line descriptors
D = sum over descriptors(popcount(page_mask) - 1)
use PFCC-64 iff 16 * D >= U
```

`D` is the exact maximum number of cross-page A refetches for a strict
page-local schedule.  The comparison is a multiply-by-16 shift and a 15-bit
compare, not a learned table.  The 1/16 threshold is an archive-derived
screening point, not yet a promoted constant.

If the gate is false, drain the existing full-row schedule.  If true, initialize
`focus_page = 0` and use PFCC-64.

### Page-aware A issue

Each Row-Table row keeps four small counts of unconsumed descriptors.  Within
each existing RT slice:

1. Continue its active `grow_addr` while that grow has work for `focus_page`,
   up to 128 A-line claims.  The 128 bound is the number of cache-line columns
   in one bank row under the archived RoBaRaCoCh mapping.
2. Otherwise use a two-level round-robin selector: choose one of four fixed
   16-row groups, then use a 16-way priority encoder to choose a row whose
   `focus_page` count is nonzero.  The existing eight entries in that row are
   scanned for a nonempty page subchain.
3. A request consumes only the focus-page Offset subchain immediately.  Empty
   subchains are skipped.  An A line can therefore be fetched no more than four
   times; a carried completion reduces that count.
4. Advance the focus when every Offset entry in that page is either committed
   to a C write or is represented by a pending complete-line write.  Page ready
   remains the existing stronger condition: all logical words scanned, all
   expected words issued, and all writes acknowledged.

This retains the current 32-slice parallel issue structure.  Both selection
levels are fixed (four groups and 16 rows); there is no global row sort or
associative scan over 16K entries.

### Destination-line completion credits

PFCC adds a 64-line, 16-set, four-way deferred buffer.  It is separate from the
384-line C combiner.

For each returned source line:

1. Send focus-page Offset words to the normal C combiner.
2. For later-page Offset words, probe the four ways of their PFCC set.  Merge
   into an existing entry.  At most one previously absent future C line may be
   allocated per source response, chosen by largest word contribution and then
   lowest `(page, line)`; allocation is refused when the set is full.
3. Each buffered word retains its Offset-entry token and sets that Offset's
   `tentative` bit.  Tentative work is not architecturally consumed and is not
   requested again while its entry remains resident.
4. When an entry reaches its expected mask (normally `0xff`, with the final
   short line using its exact mask), issue one full or exact-final-line write.
   Only after that write is accepted into the existing acknowledged-write path
   are its Offset tokens committed and the entry freed.
5. Incomplete PFCC entries are never partially written and never evicted.  A
   full set simply declines new future carries, leaving those Offset entries
   for a later page.  When their page becomes the focus, missing owners are
   issued normally, so every allocated entry eventually reaches its expected
   mask without requiring another unbounded structure.

The invariant is deliberate: PFCC may trade an A refetch for bounded state, but
it may not trade it for a partial future-page C write.

### Correctness invariants

- Every Offset entry is exactly one of `unconsumed`, `tentative(line,word)`, or
  `committed`; transitions are `unconsumed -> tentative -> committed` or
  `unconsumed -> committed`.
- A carry entry contains at most one value for each destination word and owns
  the matching Offset token until write acceptance.
- A page-ready event requires the existing scanned/expected/issued/completed
  equality and the correct descriptor generation.  Scheduler issue completion
  alone is not page readiness.
- Completion requires all four page subchains empty, no tentative token, no
  response or source reservation, no carry/C-combiner entry, and no outstanding
  write.
- Predicated-false words are accounted once using their architectural no-write
  rule; they must not be reintroduced on a later page.

## Checked replay and what it says

`dual_locality_scheduler_model.py` reads the archived index JSON, builds exact
`destination -> A cache line -> C line` mappings, applies the archived DDR4
RoBaRaCoCh geometry, and replays a 384-line/four-way masked-write combiner under
issue-order responses.  The frozen output is
`hybrid_dual_locality_replay_2026-08-02.json`.

The archive is sufficient for this ordering model but not a timing replay:

- the 20K XRAGE issue trace records A request addresses and order but not the
  destination Offset list, response tick, or C-write event;
- the bounded FLAG trace records aggregate request heartbeats and page-ready
  events, not per-response identity;
- the JSON index streams recover the missing static mapping, but they cannot
  recover cache/memory response order or write backpressure.

The bounded replay is a useful calibration.  On full XRAGE it predicts 322,188
A requests and 262,762 C writes, versus 322,414 inserted lines and 262,903
writes in gem5 (differences of 226 and 141).  The full-row replay predicts only
271,221 writes versus 327,924 for the row4/epoch16 run.  That large miss is
direct evidence that response timing/order is essential for the full-row C
cost and that the replay must not be converted into a speedup claim.

### XRAGE full input

| Replay policy | A requests | duplicate A requests | same-bank-row successor proxy | C writes | excess C writes | mean page-0 issue-complete ordinal |
|---|---:|---:|---:|---:|---:|---:|
| full-row | 299,046 | 0 | 98.685% | 271,221 | 9,077 | 989.930 |
| bounded4 | 322,188 | 23,142 | 97.981% | 262,762 | 618 | 633.844 |
| naive page-focus R1 | 299,046 | 0 | 74.356% | 270,187 | 8,043 | 639.523 |

The naive policy is rejected.  Allowing one non-focus descriptor per four-line
row burst recovers only 1,034 of the 9,077 full-row excess writes; future-page
words still pollute the main combiner.  PFCC's separate no-partial carry buffer
is therefore necessary.

The mode gate sees 23,142 cross-page refetch opportunities over 299,046 unique
descriptors and selects PFCC on 71/128 XRAGE tiles.  A pure PFCC ordering bound
is 299,046--322,188 A requests, with a target C-write ceiling equal to the
bounded issue-order proxy (262,762).  These are mechanism bounds, not observed
PFCC traffic.

### Fourteen FLAG inputs

| Replay policy | A requests | duplicate A requests | same-bank-row successor proxy | C writes | excess C writes |
|---|---:|---:|---:|---:|---:|
| full-row | 153,567 | 0 | 87.768% | 80,650 | 836 |
| bounded4 | 155,262 | 1,695 | 87.754% | 79,958 | 144 |
| naive page-focus R1 | 153,567 | 0 | 69.235% | 81,459 | 1,645 |

FLAG exposes little cross-page A reuse in the common cases: bounded4 refetches
only 1,695 lines across 638,460 outputs.  The 1/16 per-tile gate selects just
6/40 tiles, all in `static_2d/001/config_00_gather` and
`static_2d/001.nonfp/config_00_gather`.  Those are also the two measured FLAG
cases where bounded4 beats compact16 after removing roughly 3,400 excess C
writes.  This agreement is encouraging but is a fitted observation on one
suite, not independent validation.

## Expected architectural behavior

| Property | XRAGE expectation | FLAG expectation |
|---|---|---|
| Mode | PFCC on high-pressure tiles (71/128 in replay) | Full-row on 34/40 tiles; PFCC only on the two fragmented cases |
| Page-ready latency | Page-0 source issue no later than bounded4's page-local path; actual completion depends on carry/full-write ACKs | Full-row behavior on bypassed tiles; page-local behavior on six selected tiles |
| A locality | Requests between full-row and bounded4; grow quantum preserves bank-row service | Usually full-row; PFCC can save only a small 1,695-request upper bound suite-wide |
| C writes | Target bounded4 geometry because future carries emit only complete lines | Full-row on bypassed tiles; bounded-like geometry only where the gate detects pressure |
| Overall latency | Could retain bounded4's XRAGE win if carry lookup/update is off the critical source path | Should avoid paying page focus on most FLAG tiles; no speedup is claimed |

The existing bounded FLAG trace reports 26,939 first-page-ready cycles, 80,660
all-page-ready cycles, and a 53,721-cycle span summed over its two logical
instructions.  It provides a baseline counter contract only; no PFCC trace
exists.

## State and timing cost

The bit-packed incremental lower bound is 204,049 B (199.27 KiB):

| PFCC component | Bytes |
|---|---:|
| Three additional page head/tail pairs per A descriptor | 184,320 |
| Four-bit descriptor page masks | 8,192 |
| Per-row/page descriptor counters | 4,096 |
| Per-slice/focus/page/gate control | 97 |
| 64-line carry payload | 4,096 |
| Carry tags, masks, and page metadata | 240 |
| Eight Offset tokens per carry line | 960 |
| Tentative Offset bitmap | 2,048 |
| **Total** | **204,049** |

Adding PFCC to the earlier 4K-SPD/full-16K-metadata lower bound raises it from
842,482 B to 1,046,531 B.  That is 393,393 B above the 653,138-B fully bounded4
ledger.  These remain storage lower bounds, not synthesized area or energy.

The timing additions are also explicit:

- fill writes one of four head/tail pairs and maintains a four-bit page mask;
- a new row selection adds a fixed four-group round robin followed by a 16-way
  page-count zero-test/priority encoder before the existing eight-entry
  selection; active-grow claims bypass it for up to 128 requests;
- each future word performs a four-way tag lookup in one of 16 PFCC sets;
- carry updates share the existing four-word/cycle retirement datapath, and at
  most one new carry line is allocated per source response.

The selector should be pipelined as an additional scheduler stage if it misses
the current clock.  This analysis does not claim a one-cycle lookup and has no
RTL synthesis evidence.

## Counters and trace required for validation

Add these counters before a PFCC gem5 experiment:

- tiles gated to full-row/PFCC and the `(U,D)` histogram;
- A requests per logical tile, unique A lines, refetches, actual memory-system
  row hits/misses, grow switches, and grow-quantum truncations;
- focus-page switches, per-page source-issue-complete cycles, and existing
  first/all page-ready cycles;
- PFCC lookup hit/miss, allocation refusal, occupancy high water, tentative
  words, full-line commits, and cycles blocked by its set or write limit;
- existing full/partial C writes, plus a fatal counter asserting zero partial
  writes sourced from a future-page carry;
- scheduler select cycles, empty-page-row scans, response stalls, and write ACK
  stalls.

For a replayable timing trace, emit one bounded record per event with
`instruction_generation`, source issue sequence/tick, `source_addr`, RT slice,
`grow_addr`, response tick, every consumed `(offset_id, itr, wid, page)`, PFCC
line/mask transition, and C-write line/mask issue/ACK ticks.  Without these
fields, page-ready and C fragmentation cannot be causally reconstructed from
the archived logs.

## Integration blockers from the transparent-SPD review

The separately published read-only review at coordination commit
`fec94f43150bff72da3a40c95fc6abd815f8d97c` rejects prototype `19c31c6` as a
transparent SPD cache.  PFCC must not be integrated until its four P1 failures
are fixed and tested:

- hazardous backing/destination overlap;
- register clobber, including FP64 upper-half gaps;
- adjacent-base FP64 tile-span conflicts;
- liveness failure for an already-ready descriptor.

The same review also requires memory ACK rather than STREAM_ST send acceptance,
generation-bound page tokens, and clock-timed lookup validation.  PFCC relies
directly on all three: carry tokens cannot commit on send acceptance, stale page
readiness cannot release a reused descriptor, and its lookup is on a real
scheduler/retirement path.

## Reproduction and limitations

```bash
python3 -m unittest experiments.tests.test_dual_locality_scheduler_model -v
python3 experiments/analysis/dual_locality_scheduler_model.py \
  --xrage /data1/nier/DX100/experiments/inputs/xrage_gather0_full.json \
  --flag-root /data1/nier/worktrees/DX100-transparent-virtual-tile-20260725/benchmarks/spatter/tests/test-data/lanl/flag \
  --output /tmp/hybrid_dual_locality_replay.json
```

- The XRAGE input is hash-bound to
  `1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde`.
- The XRAGE source-line phase `3585` is recovered from the archived 20K exact
  issue trace and assumed for the full input.  FLAG did not archive an exact A
  base address; its phase-zero bank-row result is explicitly a proxy.
- Source responses are replayed in issue order and writes are assumed to be
  accepted immediately.  Cache latency, FR-FCFS reordering, outstanding limits,
  response-pool pressure, and page write-ACK time are absent.
- PFCC-64 itself is specified and state-accounted but not response-timed in the
  replay.  The JSON reports conservative endpoint bounds, not PFCC observations.
- The 1/16 gate and 64-line capacity have no sweep, repetitions, or independent
  workload validation.  They are implementation points for the first A/B, not
  promotion evidence.
- No gem5 run, RTL synthesis, source integration, or application speedup claim
  is part of this handoff.

Primary archived evidence is under
`/data1/nier/dx100-runs/2026-07-29-xrage-bounded-storage-attribution-3b50cdb`,
`/data1/nier/dx100-runs/2026-07-29-flag-matched-offset-epoch-broad-3b50cdb`,
`/data1/nier/dx100-runs/2026-07-29-flag-bounded-vs-compact-3b50cdb-v2`, and
`/data1/nier/dx100-runs/2026-07-29-flag-offset-epoch-trace-3b50cdb`.
