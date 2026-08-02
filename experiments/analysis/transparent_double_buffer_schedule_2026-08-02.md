# Transparent controller trace and finite double-buffer schedule

Date: August 2, 2026

## Scope and provenance

This report reconstructs one transparent-controller lifecycle from:

`/data1/nier/dx100-runs/2026-08-02-transparent-spd-premeeting/transparent_4k_retry1/run/virtual_trace.log`

- trace SHA-256: `fca7bbda3020b80411688e1c0d380d8487654d4d325f80817c59c2f2dfa220fe`
- controller source commit: `288aef2f821803179a8b69878bebb49d41a8e52f`
- `TransparentSPDController.hh` SHA-256:
  `f4f3209e79ebf0e002ff12cb5d836ca5d197675e964ca6463e978e2a03358f06`
- `MAA.cc` SHA-256:
  `0d697f1d66701f479526ef458f72aa151db4446ec5b6e80503602a4db002fc13`
- `IF.cc` SHA-256:
  `1ff465fd5f398cbcc920fb12b1fffd1155d98ed0974c67bda26546aaf9e7528e`

The trace was supplied as a successful run.  The parser proves that the named
trace contains one internally complete submit/page-ready/issue/complete/retire
lifecycle.  It does not independently prove the wrapper return code, final
stats, benchmark output, or run comparability.  No gem5 run was performed for
this analysis.

At the recorded commit, `TransparentSPDController.hh:14-16` declares one 4K
mapping and one in-flight native micro-op; lines 152-153 retain one current and
one mapped page; and lines 253-260 release the mapping only after store
completion.  `MAA.cc:835-866` lowers the stages to stream-load, out-of-place
ALU, and stream-store operations.  Critically, `IF.cc:323-347` maps both
`STREAM_LD` and `STREAM_ST` to `FuncUnitType::STREAM`, while `MAA.cc:158-164`
constructs one `StreamAccessUnit` per MAA and `MAA.cc:513-527` admits only one
instruction while that unit is idle.  The ALU is distinct.  `MAA.cc:1156-1159`
returns the lifetime tile credits after the final store is accepted by the
memory hierarchy.  The schedule below preserves those dependencies and actual
functional-unit serialization while making slot ownership explicit.

## Fail-closed parser contract

`transparent_double_buffer_trace_analysis.py` ignores unrelated trace lines
but rejects the entire analysis for any of the following:

- a malformed target line (including tab-delimited or whitespace-disguised
  `event = transparent_*`, `event transparent_*`, and `page_ready` forms), a
  duplicate or missing field, an unexpected field, or an unknown
  `transparent_*` event (including backpressure, which this timing model does
  not account for);
- a component mismatch (`system.maa` for controller events and `global` for
  page readiness), decreasing target-event ticks, a second submit, an event
  after retirement, or missing retirement;
- invalid fixed submit geometry/generation/FP64 tile spans, incomplete or duplicate
  readiness accounting, or readiness outside the submitted page range;
- an invalid action/offset/length, a completion without its exact issue,
  duplicate action, non-positive interval, or a per-page order other than
  fill -> compute -> store; or
- fill admission outside logical page order, fill before that page's ready
  tick, retirement before every store completes, or any incomplete page.

Page 0's fill issue is printed immediately before its page-ready event at the
same tick.  The parser therefore checks causal tick order rather than requiring
same-tick log lines to appear in callback order.  Readiness may arrive out of
page order: page 3 is ready 939 ticks before page 2, while fills remain ordered.

## Reconstructed observed intervals

Action values are 1 = fill, 2 = compute, and 3 = store.  All values below are
raw trace ticks, not host time and not converted to cycles.

| Page | Ready | Fill [issue, complete), duration | Compute [issue, complete), duration | Store [issue, complete), duration |
|---:|---:|---:|---:|---:|
| 0 | 3,155,463,802 | [3,155,463,802, 3,155,787,131), 323,329 | [3,155,787,131, 3,155,947,387), 160,256 | [3,155,947,387, 3,158,706,169), 2,758,782 |
| 1 | 3,164,183,043 | [3,164,183,043, 3,164,506,372), 323,329 | [3,164,506,372, 3,164,666,628), 160,256 | [3,164,666,628, 3,166,230,689), 1,564,061 |
| 2 | 3,164,746,130 | [3,166,230,689, 3,166,553,705), 323,016 | [3,166,553,705, 3,166,713,961), 160,256 | [3,166,713,961, 3,168,159,395), 1,445,434 |
| 3 | 3,164,745,191 | [3,168,159,395, 3,168,482,411), 323,016 | [3,168,482,411, 3,168,642,667), 160,256 | [3,168,642,667, 3,170,484,672), 1,842,005 |

The stage-duration sums are 1,292,690 fill ticks, 641,024 compute ticks, and
7,610,282 store ticks.  Every fill-complete -> compute-issue and
compute-complete -> store-issue gap is zero; final store-complete -> retire is
also zero.

The cross-page dependency columns below distinguish producer readiness from
the current controller's physical-slot dependency.  `prior store -> ready` is
signed: positive means the controller waited for readiness after the prior
store; negative means the ready page waited for the one physical mapping.
`dispatch gap` starts only after both dependencies are satisfied.

| Page | Submit -> ready | Prior store -> ready | Readiness wait | One-slot wait after ready | Ready -> fill | Dispatch gap |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 29,807,615 | n/a | 0 | 0 | 0 | 0 |
| 1 | 38,526,856 | +5,476,874 | 5,476,874 | 0 | 0 | 0 |
| 2 | 39,089,943 | -1,484,559 | 0 | 1,484,559 | 1,484,559 | 0 |
| 3 | 39,089,004 | -3,414,204 | 0 | 3,414,204 | 3,414,204 | 0 |

Thus the observed controller is already gap-free after its declared
dependencies.  Page 1 exposes a producer-readiness gap.  Pages 2 and 3 are
ready early but remain blocked behind the single mapped page through store
completion.

## Why exactly two total page spans are insufficient

The current micro-op lowering fills `physicalTile`, computes from
`physicalTile` into a distinct `outputTile`, and stores from `outputTile`.
During compute both page-sized tile spans are live.  Therefore a budget of
exactly two total 4K page spans cannot also hold a prefill of page k+1.  That
request is feasible only if the ALU becomes safely in-place, or if "two-slot"
means two input slots in addition to the existing shared output span.

The minimal schedule below uses the latter definition: two 4K input slots and
one 4K shared output span.  For FP64, each page span covers two adjacent SPD tile
IDs, so all ownership and hazard tests are span-aware.  This design can prefill
while the ALU computes, but the one STREAM unit prevents a prefill from
overlapping any store.  The shared output deliberately prevents two pages'
compute/store chains from overlapping.  Two complete input/output slot pairs
would cost four page spans and are outside this schedule.

## Finite two-input-slot controller

The controller has fixed storage only:

- one immutable active descriptor and generation;
- four page records with `ready` and
  `unseen/fill_pending/filling/filled/computing/output/store/done` phase;
- two input records `{owner=(generation,page), phase}`;
- one output record `{owner=(generation,page), phase}`;
- `next_fill`, `next_output`, and `done_pages` counters in `[0,4]`;
- one stable STREAM request latch tagged as fill or store and one stable ALU
  compute request latch; and
- at most one accepted STREAM fill-or-store plus one accepted ALU compute in
  flight.  There is no unbounded request queue.

The ordered transition schedule is:

1. **Submit.** Validate the fixed geometry, generation, backing/destination
   ranges, two non-overlapping input spans, distinct output/token spans, and
   register spans.  Reject while active.  Reserve all input, output, token, and
   register spans for the descriptor lifetime.
2. **Page ready.** Accept exactly one `(token,generation,page)` readiness event
   for each page.  A duplicate, stale generation, wrong token, or out-of-range
   page fails closed.  Out-of-order bits may accumulate, but `next_fill`
   prevents page bypass.
3. **Fill admission.** When page `next_fill` is ready, the shared STREAM unit
   and input slot `next_fill % 2` are free, and no completed output is waiting
   to store, latch
   `(generation,page,Fill,slot,offset,elements)` and atomically reserve that
   slot owner.  An IF rejection leaves the latch and owner unchanged.  Only an
   accepted push changes `fill_pending` to `filling` and increments
   `next_fill`.
4. **Fill completion.** Require an exact matching accepted action and slot
   owner, then change the input to `filled`.  Duplicate, stale, or mismatched
   completion fails closed.
5. **Compute admission.** Page `next_output` may compute on the ALU only when
   its exact input owner is `filled` and the shared output is free.  Reserve the
   output for that page at admission; a simultaneous STREAM fill is legal only
   when it owns a different free input slot.  IF rejection is side-effect free.
6. **Compute completion.** Require the exact action/page/generation.  The ALU
   has finished reading the input, so release that input slot.  The output
   becomes owned by the page and enters `output`; the page cannot bypass its
   store.
7. **Store admission and completion.** A completed output has priority for the
   one STREAM unit; it cannot issue until any active fill finishes.  Latch the
   store from the exact output owner; IF rejection is side-effect free.
   Acceptance changes it to `store`.  Its matching native completion releases
   the output, marks the page done, and increments `next_output` and
   `done_pages`.  This permits page k+1 compute and page k+2 fill while
   preserving output, input, and STREAM hazards.
8. **Retire.** Retire exactly once only when `done_pages == 4`, both request
   latches and both in-flight lanes are empty, both input slots and the output
   are free, and the producer generation still matches.  Then consume that
   generation and release descriptor-lifetime tile/register credits.

A rejected latch remains byte-for-byte stable.  ALU admission is independent
of STREAM occupancy, but a ready store takes STREAM priority over a later fill;
the fixed four-page descriptor makes this policy finite.  Completion callbacks
carry `(generation,page,action,slot)` rather than relying on whichever page is
currently at the head.

### Tile and data hazards

- A fill may write a slot only after the old owner was released by matching
  compute completion; issue is not sufficient because the ALU may still read
  the input.
- Every fill and store occupies the same STREAM unit for its complete observed
  interval.  No `STREAM_LD` may overlap any `STREAM_ST` (or another load).
- Compute reads exactly one filled input owner and writes only the free shared
  output.  Store reads that output until its matching completion, so the output
  cannot be reused at store issue.  ALU compute may overlap STREAM only when
  these exact input/output owners remain disjoint.
- External instructions and register writes remain excluded from every
  descriptor-owned FP64 span, including both adjacent tile IDs, for the same
  lifetime rules used by the current controller.
- Backing and destination ranges remain non-overlapping.  Page readiness is
  tied to the submitted producer generation, and late events cannot authorize
  a reused slot or descriptor.

### Completion meaning

The trace's `transparent_complete action=3` is the current native stream-store
completion.  Current source describes final retirement as occurring after the
store is accepted by the memory hierarchy.  It is not evidence of persistence
at DRAM.  The schedule may release the output only if that callback guarantees
the store unit will no longer read SPD data.  If the architectural contract
requires a write acknowledgement beyond that point, an explicit bounded ACK
counter must keep the output owned and delay page/descriptor completion; the
provided trace cannot quantify that stronger semantic.

## Conditional resource-feasible fixed-duration schedule

The executable list schedule assigns input slot `page % 2`, releases an input
only on compute completion, and releases the single output only on store
completion.  One STREAM resource serializes every ordered fill and store; a
ready store has priority.  The distinct ALU may overlap STREAM when the exact
owners permit it.  Page-ready ticks and every observed stage duration remain
fixed, with zero dispatch cost and no duration inflation from legal overlap.
The ordering is finite and resource-feasible under those assumptions, but the
ticks are still a conditional projection—not a critical-path lower bound, a
gem5 prediction, or a gem5 speedup.

| Quantity | Observed trace | Feasible fixed-duration schedule | Reduction | Reduction vs. observed |
|---|---:|---:|---:|---:|
| Submit -> retire | 44,828,485 | 44,668,229 | 160,256 | 0.357487% |
| First page ready -> retire | 15,020,870 | 14,860,614 | 160,256 | 1.066889% |
| Completion tick | 3,170,484,672 | 3,170,324,416 | 160,256 | n/a |

Page 2 fills only after page 1's store releases STREAM.  Page 3 then fills from
3,166,553,705 through 3,166,876,721 while page 2 computes from 3,166,553,705
through 3,166,713,961.  That legal ALU/STREAM overlap hides exactly one
160,256-tick compute interval.  Page 2's store waits for page 3's fill to
release STREAM.  No fill overlaps a store.

The resource-feasible page schedule is:

| Page | Input | Fill [issue, complete) | Compute [issue, complete) | Store [issue, complete) |
|---:|---:|---:|---:|---:|
| 0 | 0 | [3,155,463,802, 3,155,787,131) | [3,155,787,131, 3,155,947,387) | [3,155,947,387, 3,158,706,169) |
| 1 | 1 | [3,164,183,043, 3,164,506,372) | [3,164,506,372, 3,164,666,628) | [3,164,666,628, 3,166,230,689) |
| 2 | 0 | [3,166,230,689, 3,166,553,705) | [3,166,553,705, 3,166,713,961) | [3,166,876,721, 3,168,322,155) |
| 3 | 1 | [3,166,553,705, 3,166,876,721) | [3,168,322,155, 3,168,482,411) | [3,168,482,411, 3,170,324,416) |

### Relaxed independent-STREAM counterfactual (not implementable)

The superseded recurrence completed at tick 3,169,838,640: 44,182,453 ticks
from submit and 14,374,838 ticks from first readiness.  Its 646,032-tick
reduction was 1.441119% and 4.300896% of those respective observed intervals.
It overlaps both page 2 and page 3 fills with page 1's `STREAM_ST`, despite all
three mapping to the one `StreamAccessUnit`.  Those values are retained only to
identify the relaxed independent-STREAM counterfactual; they are not a feasible
schedule, implementable bound, prediction, or speedup.

Regenerate the machine-readable analysis with:

```sh
python3 experiments/analysis/transparent_double_buffer_trace_analysis.py \
  /data1/nier/dx100-runs/2026-08-02-transparent-spd-premeeting/transparent_4k_retry1/run/virtual_trace.log
```
