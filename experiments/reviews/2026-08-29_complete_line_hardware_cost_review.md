# Complete-line XRAGE/FLAG hardware-cost review (2026-08-29)

## Decision

**Keep the complete-line hybrid as exact functional/simulator evidence, but do
not promote either the 1,536-tag XRAGE point or the 2,048-tag FLAG point as a
hardware-timed or iso-area design.**  Source commit `15919194` genuinely bounds
logical/physical geometry, tag count, useful-word capacity, response
reservations, insertion attempts, write credits, and (when nonzero) complete
line issues per MAA cycle.  It also fails closed on a non-tail partial victim or
drain.

The published XRAGE and FLAG timing observations remain lower bounds for a
real implementation.  They were produced by binaries from before the new
drain-width model, and the selected organization gives zero modeled time to
the 16-way tag match, a 1,536/2,048-slot ready-line scan, shared-payload reads
and frees, response allocation, scoreboard searches, and operation reset.
The current default drain width is still zero, meaning unlimited.  No current
binary drain-width result exists in the reviewed evidence, and this review did
not launch one.

The cheapest next gates are: replay exact insertion traces at 8/4 ways before
running gem5; run width 1 and 2 from the already-written drain sweep before the
full 0/1/2/4/8 matrix; add a 1/2-cycle pipelined lookup sensitivity; and run a
small dirty-cache/competing-owner coherence test.  Any failure is enough to
reject the present performance interpretation without synthesis.

## Scope and evidence boundary

This is an independent read-only audit of commit
`15919194c94e54e911aba034b373dc99437b1f71`.  No production source was changed
and no simulation was launched.  Existing artifacts were read only, and the
current `report_maa_storage.py` was rerun against their frozen `config.ini`
files into `/tmp` to recompute packed fields.

The prior XRAGE record remains useful but narrow: the two selected launches at
source `63795eaa` reproduce exact output hash `5576400619275092867`, 8,192 full
producer writes, zero partial writes, and 37,268,284 ROI `simTicks`
([result record](../analysis/xrage_complete_line_hybrid_results_2026-08-29.md#L1)).
The native comparison is not a byte-identical checkpoint pair, as already
documented at
[`2026-08-29_xrage_complete_line_final_review.md:15-24`](2026-08-29_xrage_complete_line_final_review.md).

The FLAG all-14 audit is also valid functional evidence for its older binary:
it requires the 2,048-tag/3,072-word/16-way point, terminal `m5_exit`, an exact
hash, issue/ACK equality, and exactly `floor(length/8)` full FP64 lines plus one
tail when needed (`experiments/scripts/audit_flag_complete_line_campaign.py:72-169`).
It pins source `30a67a7a`, which also predates the drain-width implementation.
The incomplete 1,536/2,560 all-14 campaign is not a negative completion
accident: `flag_static_2d_001_00_gather` terminates with the intended
partial-victim panic.  Thus 1,536 tags are supported for the reviewed XRAGE
input and some FLAG cases, not the full FLAG set.

## Findings, ordered by severity

### F1 — blocker: no performance observation contains the current drain model

The current knob accepts only 0/1/2/4/8 and defines zero as unlimited
(`src/mem/MAA/CompleteLineDrainBudget.hh:27-41`; `src/mem/MAA/MAA.py:103-106`).
The budget counts successfully created full-line writes per `maa->curCycle()`
and schedules a retry at the next clock edge when exhausted
(`src/mem/MAA/IndirectAccess.cc:11057-11077`).  That is a real issue-count
bound when configured to a finite value.

It is not present in the accepted measurements:

- XRAGE artifacts pin source `63795eaa`; FLAG pins `30a67a7a`.  The drain
  budget lands later at `382c4c23` and its cycle-identity correction at
  `a8df60d3`.
- The current runner defaults the width to unlimited and passes that value
  directly to gem5 (`experiments/scripts/run_xrage_direct_index_smoke.sh:49,147-153,449-455`).
- The new sweep is a launch recipe for widths 0/1/2/4/8 and exact closure, not
  a result (`experiments/scripts/run_xrage_complete_line_drain_sweep.sh:42-50,53-125`).
- The unit test proves token accounting only; its unlimited case legally
  records 300 lines in one cycle (`tests/maa/complete_line_drain_budget_test.cc:60-81`).

Even finite width bounds only `createRetirementWrite()` calls.  It does not
charge the scan that finds a full line, eight scattered payload reads, eight
reference frees, tag invalidation, scoreboard search, or cache-port
arbitration.  Therefore an eventual width-1 result would close one important
bandwidth lower bound, not the whole drain datapath.

**Consequence:** retain 37,268,284 XRAGE ticks and the FLAG table as results of
their exact older simulator configurations.  Do not compare them to a current
RTL budget or call the new finite drain modeled until a current binary rerun
closes exact output, 8,192 issues/ACKs, peak width, stalls, and source identity.

### F2 — high: the hot lookup and drain-selection paths are zero-latency host scans

The four-word insertion limit is real: the response loop increments an attempt
counter and stops at `virtual_words_per_cycle`, which the selected runner fixes
at four (`src/mem/MAA/IndirectAccess.cc:10404-10417,10638-10659`;
`experiments/scripts/run_xrage_direct_index_smoke.sh:455`).  It bounds
throughput attempts, not lookup latency.

For each attempt, the source computes a set and compares as many as 16 tags
serially in the host loop (`src/mem/MAA/IndirectAccess.cc:10824-10845`).  The
selected runner simultaneously forces `virtual_combine_banks=0`, for which
bank reservation returns immediately and records no conflict
(`experiments/scripts/run_xrage_direct_index_smoke.sh:449-455`;
`src/mem/MAA/IndirectAccess.cc:10794-10813`).  Four accepted attempts in a
cycle can therefore imply four independent 16-way matches and four
read-modify-writes with no comparator, bank, hazard, or pipeline cost.

The drain is more optimistic than the 16-way insertion lookup.  Because both
selected configs resolve `virtual_page_ordered_combiner_drain=false`, every
drain call walks all 1,536 or 2,048 line slots looking for full masks
(`src/mem/MAA/IndirectAccess.cc:11080-11175`).  The optional ready queues would
replace that scan with a fixed 16-page head encoder
(`src/mem/MAA/VirtualCombinerPageOrder.hh:13-32,92-109`), but they are disabled
and their links/ports are not part of the selected cost.

Shared-payload pressure adds another global selection obligation.  When the
word pool is full but the incoming set has a place, victim selection can walk
all tags from a global pointer (`src/mem/MAA/VirtualCombineVictimSelector.hh:45-72`;
`src/mem/MAA/IndirectAccess.cc:10846-10880`).  Round-robin often stops early,
but hardware still needs a bounded valid/full directory or a wide rotating
priority encoder.  Policies 1/2 require the full comparison.

The same issue appears around the combiner:

- an arriving source response scans 128 slots for the first free record and
  copies useful words into a per-slot `std::vector`
  (`src/mem/MAA/IndirectAccess.cc:9561-9606`);
- address-to-reservation ownership is a bounded `std::map`, not a timed 128-tag
  response table (`src/mem/MAA/IndirectAccess.hh:132-141` and
  `src/mem/MAA/IndirectAccess.cc:2759-2769`); and
- each retirement issue/ACK searches as many as 64 scoreboard entries
  (`src/mem/MAA/VirtualRetirementScoreboard.hh:86-149`).

**Consequence:** four inserts/cycle is not yet a credible delivered bandwidth.
A realizable version needs either four lookup lanes (64 tag comparators), a
banked/pipelined table with explicit conflicts, or lower throughput.  It also
needs a ready FIFO/bitmap and an indexed ACK identity, rather than zero-time
slot scans.

### F3 — high: the 4,096-word check is a payload bound, not an area or port bound

The source genuinely rejects zero result pools and
`combiner_words + response_words > physical_tile_elements`
(`src/mem/MAA/MAA.cc:397-407`).  The selected XRAGE sum is 2,560 + 1,024 =
3,584 words; FLAG is 3,072 + 1,024 = 4,096.  Source reservation cannot exceed
the response slot/word credits (`src/mem/MAA/IndirectAccess.cc:2724-2735`),
the payload allocator is reset to the configured combiner capacity
(`src/mem/MAA/IndirectAccess.cc:6143-6156`), and allocation fails at exhaustion
(`src/mem/MAA/VirtualCombinePayloadStore.hh:80-134`).

That check counts only useful FP64 data words.  It does not count tags, masks,
references, allocators, ready state, or ports.  The current ledger explicitly
calls its equations hardware lower bounds and excludes periphery, ports, and
wiring (`experiments/scripts/report_maa_storage.py:461-475`).  The source even
states that its 32-bit generation-bearing word reference is simulator bug
detection, not a synthesized encoding claim
(`src/mem/MAA/VirtualCombinePayloadStore.hh:13-20`).

Reset is also free in simulated time.  Every operation clears all response and
tag slots, reconstructs page queues, assigns the entire payload array, and
rebuilds every free-list entry (`src/mem/MAA/IndirectAccess.cc:6200-6212,6261-6267`;
`src/mem/MAA/VirtualCombinePayloadStore.hh:80-107`).  A serial one-entry/cycle
reset would take at least 2,560 cycles for XRAGE or 3,072 for FLAG per logical
operation.  A practical implementation needs multi-bank clear, a free bitmap,
or epoch-valid tags with a defined wrap drain; each choice adds state and
ports.

**Consequence:** the payload check is a useful admission invariant, but
“within physical4K” must be written as “useful result payload is within 4,096
words.”  It cannot support iso-area, SRAM count, energy, or Fmax claims.

### F4 — high correctness risk: private retention assumes exclusive destination ownership

Complete-line retention intentionally keeps partial words outside coherence
until a full line is issued.  The issued packet is a coherent full-line
`WriteReq`, and page readiness normally waits for its exact WriteResp
(`src/mem/MAA/IndirectAccess.cc:10291-10318,10331-10372` and
`src/mem/MAA/IndirectAccess.cc:10037-10080`).  This is correct for an aligned,
producer-owned destination.

Source does not, however, reserve the backing line against an arbitrary CPU or
second MAA owner while fragments are private.  Before issue it checks only the
MAA's own outstanding packet map and the 64-entry retirement scoreboard
(`src/mem/MAA/IndirectAccess.cc:10253-10268`;
`src/mem/MAA/MAA.hh:1647-1655`).  Registered-region validation proves address
bounds, not exclusive lifetime ownership
(`src/mem/MAA/IndirectAccess.cc:6818-6827,9920-9976`).  There is no combiner
snoop/invalidation path that merges an external write into a retained partial
line.

A dirty old cache line should be handled by the coherent full-line request,
but a competing writer or overlapping producer can legally create a lost
update unless the software/API contract forbids that access.  The reviewed
applications obey the intended producer/consumer ordering; their final hashes
do not adversarially prove the ownership rule.

**Consequence:** document backing as exclusively producer-owned from admission
through final WriteResp, or add an owner/conflict mechanism.  Until a directed
test proves dirty-cache, invalidation, retry, and overlapping-producer behavior,
this remains a correctness promotion gate rather than merely a performance
detail.

### F5 — medium: 1,536 is an XRAGE knee, not a general FLAG capacity, and its set count is awkward

The 1,536/2,560 XRAGE point is only 9,703 ticks (0.026%) slower than the older
2,048/3,072 point in the pre-drain simulator
(`experiments/analysis/xrage_complete_line_hybrid_results_2026-08-29.md:74-83`).
That is a useful semantic-capacity knee.  It does not establish the cheaper
physical organization.

At 16 ways, 1,536 tags form 96 sets while 2,048 form 128 sets.  Source indexes
with integer modulo (`src/mem/MAA/IndirectAccess.cc:10830-10835`), so the
1,536-tag point either needs non-power-of-two set reduction, a 3x32-set bank
organization, or a changed hash.  The 2,048 point uses simple seven low set
bits and completed the audited all-14 FLAG set.  The attempted 1,536/2,560
all-14 campaign fails closed on at least one real FLAG gather; it cannot be
substituted without an application-specific admission/fallback rule.

Lower associativity is entirely open for XRAGE/FLAG.  The current source can
configure it, but no accepted selected-workload evidence shows that 8 or 4
ways avoids partial-victim rejection.  Since a rejection is deterministic,
offline exact-trace replay is the cheapest gate before timing runs.

### F6 — medium: the 64-credit and 1,024-response limits are finite but their hardware access is not

The configured write capacity is validated in `[1,64]`
(`src/mem/MAA/IndirectAccess.cc:174-191`), and the scoreboard has a fixed
64-entry ceiling with exact address/generation/transaction checking
(`src/mem/MAA/VirtualRetirementScoreboard.hh:20-36,74-84,127-149`).  The
1,024-word response pool and 128 slots are likewise enforced before request
issue.  These are genuine liveness/capacity bounds.

The packed ledger charges 44 B per live write plus one 8-B allocator, or 2,824
B at 64 credits (`experiments/scripts/report_maa_storage.py:526-534`).  It does
not give the scoreboard an indexed transaction slot; `contains`, `find`, and
`take` are linear searches.  Nor does the response pool source instantiate a
shared 1,024-word RAM: it accounts global reservations but stores per-response
vectors.  A real response RAM needs allocation, an incoming-response tag
lookup, up to four word reads for insertion, and reclamation.

**Consequence:** the capacities are bounded; their CAM/RAM ports and latency
are simulator lower bounds.  A slot-index-plus-generation ACK encoding and a
fixed response-pool free list are the cheapest plausible hardware mappings.

## What is genuinely bounded

| Resource or behavior | Source-backed statement | Remaining lower bound |
|---|---|---|
| Logical16K / physical4K | Strict mode rejects other geometry; SPD payload allocation uses `physical_tile_elements` (`src/mem/MAA/MAA.cc:409-432`; `src/mem/MAA/SPD.cc:255-286`). | Retained logical Row/Offset/readiness metadata is separate and still substantial. |
| 1,536 or 2,048 line tags | Fixed-size slot vector; 16-way set mapping is exact (`src/mem/MAA/IndirectAccess.cc:145-158,10830-10845`). | Comparator/mux latency, 96-set decoding, ready selection, and ports. |
| 2,560 or 3,072 combiner words | Shared useful-word pool fails at capacity. | Physical RAM organization, arbitrary-reference read ports, allocator timing, and reset. |
| 1,024 response words / 128 responses | Request admission is blocked before exceeding either credit. | Fixed pool implementation, response-tag lookup, fragmentation, and read ports. |
| Four word attempts/cycle | Attempt counter throttles response retirement. | Lookup pipeline latency and same-set/read-modify-write hazards. |
| 64 writes | Exact issue/ACK metadata cannot exceed the configured limit. | 64-entry lookup/retire implementation and cache/network injection arbitration. |
| Drain width | Nonzero 1/2/4/8 bounds successfully created full-line writes per MAA cycle. | Accepted results use the older unlimited model; selection, payload gather/free, and downstream ports remain free. |
| Complete-line correctness | Non-tail partial victim/final drain panics; exact tail is masked; terminal count is checked (`src/mem/MAA/IndirectAccess.cc:910-934,10873-10880,11178-11211,7792-7799`). | Panic is an experiment rejection, not a hardware recovery path; destination ownership and competing coherence are assumed. |

## Packed storage floor and likely datapath

The following is one indirect unit, FP64 (eight words per 64-B line), 64-bit
conservative address tags, 12-bit references, 128 response slots, and 64 write
credits.  It reproduces the current reporter's bit equations
(`experiments/scripts/report_maa_storage.py:461-601`) rather than `sizeof` of
the host containers.

| Packed item | XRAGE 1,536 tags / 2,560 words | FLAG 2,048 tags / 3,072 words |
|---|---:|---:|
| Combiner useful-word payload | 20,480 B | 24,576 B |
| Combiner tags + masks + 8x12-bit refs + allocator + replacement | 36,659 B | 48,323 B |
| Response useful-word payload | 8,192 B | 8,192 B |
| 128 response records | 3,088 B | 3,088 B |
| 64-entry retirement scoreboard | 2,824 B | 2,824 B |
| Result-path subtotal including page/mode bits | **71,281 B** | **87,041 B** |
| 128-line direct-index feeder payload + metadata | 13,350 B | 13,350 B |
| Virtual producer subtotal before completion/direct handoff | **84,631 B** | **100,391 B** |
| Completion bits and XRAGE direct-line handoff lower bound | 12 B + 9,472 B | 12 B + 0 B |
| Increment above the 512-KiB physical SPD | **94,115 B** | **100,403 B** |

These are bit-cell floors.  A base-relative tag could reduce the combiner
portion: one aligned logical16K FP64 operation has only 2,048 possible output
lines, so an 11-bit line offset plus one retained base could replace the
ledger's 64-bit tag.  That would save 10,176 B at 1,536 tags or 13,568 B at
2,048 tags.  It would not remove set match, base/range checks, ECC/parity,
ready queues, generation/epoch state, or periphery.  Conversely, the source's
optional page queues require per-slot links/state; a simpler full-line FIFO
still needs roughly one 11-bit slot index per tag (about 2.1/2.8 KiB) plus
pointers and ports.  Neither term is in the selected lower bound.

The minimum plausible peak obligations implied by the source are:

- insertion: four 16-way matches, four mask/reference read-modify-writes, four
  64-bit payload writes, and four allocator operations per cycle;
- drain width `W`: `8W` arbitrary 64-bit payload/reference reads, `8W` frees,
  `W` tag invalidations, and `64W` B/cycle of line staging before the coherent
  request queues;
- response receive/retire: at least one incoming 128-tag ownership match and
  as many as four response-pool reads per cycle; and
- ACK: up to the coherent response width of scoreboard retire operations plus
  page-count updates.

A banked design can reduce ports, but then bank conflicts and dependent
read-modify-write stalls must enter timing.  The selected zero-bank model
cannot choose between those implementations.

## Cheapest fast falsification experiments

| Priority | Experiment | Minimal acceptance rule | Why it is cheap/decisive |
|---:|---|---|---|
| 1 | **Current-binary width 1, then width 2** on the frozen XRAGE checkpoint; run 0/4/8 only if those pass. | Exact hash and 8,192 full issues/ACKs; zero partials/fallbacks; peak <= width; record stalls and identical non-treatment config. | The runner and sweep already exist. At the resolved 313 ticks/cycle, 8,192 serialized width-1 issues span at least 2,564,096 ticks, large against the old 5,043,995-tick native margin even before lookup cost. |
| 2 | **Offline associativity replay** of each logical operation at 16/8/4 ways, fixed tag/word capacity, round-robin, and the exact source set function. | No partial victim and exact word/write closure for every XRAGE window and all 14 FLAG gathers. | `experiments/scripts/analyze_virtual_combiner_reuse.py:135-287` already models set and shared-word pressure. Capture one insertion trace per input; reject failing geometries before gem5. |
| 3 | **Lookup timing/port sensitivity:** latency 1/2/3 cycles with throughput four, then throughput 2/1 or banks 4/2/1. | Exact output/mechanism closure; report lookup/bank stalls separately. | The old XRAGE margin is extremely fragile: one non-overlapped extra cycle per four-word batch over 65,536 words is 16,384 cycles = 5,128,192 ticks, already larger than that margin. This is a sensitivity bound, not a latency prediction. |
| 4 | **Ready selection and payload port test:** enable a bounded full-line FIFO/page queue and model one 512-bit line read or eight 64-bit reads at widths 1/2. | Same retirement order contract, exact hash, no queue overflow, measured FIFO/payload conflicts. | It replaces the impossible zero-time 1,536/2,048-slot walk with a small explicit structure. |
| 5 | **Reset/epoch sensitivity:** charge 1/4/16 tag and allocator clears per cycle, then test lazy epochs. | No generation alias at wrap, no live-entry reset, exact repeated-operation output. | A serial payload free-list rebuild is 2,560/3,072 cycles per operation; a counter-only model can falsify the margin before RTL. |
| 6 | **Competing coherence micro:** pre-dirty the backing line in another core, force eviction/retry, delay/reorder WriteResp, and attempt an overlapping second producer. | Post-ACK data exact; no early page-ready; stale/duplicate ACK rejected; overlap either serialized/rejected by contract or merged without lost update. | A few cache lines and one logical tail exercise the correctness boundary; no full application is needed. |
| 7 | **Credit/pool sensitivity:** 64/32/16 writes and 1,024/768/512 response words at the passing tag/way point. | Exact output and no unexplained deadlock; report HWM/stalls per operation, not summed “high water.” | It tests whether the expensive 64-entry/1,024-word maxima are necessary or merely inherited defaults. |

For every live comparison, follow the evidence order: current source and binary
hash, identical input/checkpoint/non-treatment knobs, terminal exit and final
stats, exact output before `simTicks`, predicted mechanism counters, and
repetitions.  Do not combine the old XRAGE checkpoint-mismatched native ratio
with a new drain-width arm.

## Promotion boundary

Supportable now:

- fixed source capacities and fail-closed complete-line/tail semantics;
- exact-output completion of the frozen older XRAGE point and older 2,048-tag
  FLAG all-14 campaign; and
- packed payload/control bit floors under the explicit assumptions above.

Not supportable now:

- a current-model XRAGE/FLAG speedup;
- four realizable insertions/cycle, any lookup latency, or any target Fmax;
- a physically implemented 1,536-tag knee being cheaper than 2,048 tags;
- iso-area/energy conclusions from the 4,096 useful-word check; or
- correctness under competing destination ownership.

## Terminal handoff

Treat **2,048 tags / 3,072 combiner words / 16 ways / 1,024 response words** as
the functional FLAG coverage point and **1,536 / 2,560 / 16 / 1,024** only as
the XRAGE-specific semantic knee.  Before selecting hardware, run current
width 1/2, exact-trace 8/4-way replay, a pipelined lookup/port sensitivity, and
the dirty-cache/overlapping-owner micro.  Any partial-victim failure,
correctness mismatch, or loss of the performance margin is a fast stop; only
after those gates is SRAM/CAM synthesis worth doing.
