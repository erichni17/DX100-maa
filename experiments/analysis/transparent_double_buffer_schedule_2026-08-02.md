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

The trace was supplied as a successful run.  The parser proves that the named
trace contains one internally complete submit/page-ready/issue/complete/retire
lifecycle.  It does not independently prove the wrapper return code, final
stats, benchmark output, or run comparability.  No gem5 run was performed for
this analysis.

At the recorded commit, `TransparentSPDController.hh:14-16` declares one 4K
mapping and one in-flight native micro-op; lines 152-153 retain one current and
one mapped page; and lines 253-260 release the mapping only after store
completion.  `MAA.cc:835-866` lowers the stages to stream-load, out-of-place
ALU, and stream-store operations.  `MAA.cc:1156-1159` returns the lifetime tile
credits after the final store is accepted by the memory hierarchy.  The
schedule below preserves those data dependencies while making slot ownership
and concurrent lanes explicit.

## Fail-closed parser contract

`transparent_double_buffer_trace_analysis.py` ignores unrelated trace lines
but rejects the entire analysis for any of the following:

- a malformed target line, a duplicate or missing field, an unexpected field,
  or an unknown `transparent_*` event (including backpressure, which this
  timing model does not account for);
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
while the current output computes/stores, but deliberately does not overlap
two pages' compute/store chains.  Two complete input/output slot pairs would
cost four page spans and are outside this schedule.

## Finite two-input-slot controller

The controller has fixed storage only:

- one immutable active descriptor and generation;
- four page records with `ready` and
  `unseen/fill_pending/filling/filled/computing/output/store/done` phase;
- two input records `{owner=(generation,page), phase}`;
- one output record `{owner=(generation,page), phase}`;
- `next_fill`, `next_output`, and `done_pages` counters in `[0,4]`;
- one stable fill request latch, one stable output request latch, and one
  round-robin arbitration bit; and
- at most one accepted fill plus one accepted compute-or-store action in
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
3. **Fill admission.** When page `next_fill` is ready, fill lane is free, and
   input slot `next_fill % 2` is free, latch
   `(generation,page,Fill,slot,offset,elements)` and atomically reserve that
   slot owner.  An IF rejection leaves the latch and owner unchanged.  Only an
   accepted push changes `fill_pending` to `filling` and increments
   `next_fill`.
4. **Fill completion.** Require an exact matching accepted action and slot
   owner, then change the input to `filled`.  Duplicate, stale, or mismatched
   completion fails closed.
5. **Compute admission.** Page `next_output` may compute only when its exact
   input owner is `filled` and the shared output is free.  Latch the compute
   without changing either payload owner; change to `computing` only after IF
   acceptance.
6. **Compute completion.** Require the exact action/page/generation.  The ALU
   has finished reading the input, so release that input slot.  The output
   becomes owned by the page and enters `output`; the page cannot bypass its
   store.
7. **Store admission and completion.** Latch store from the exact output owner;
   IF rejection is side-effect free.  Acceptance changes it to `store`.  Its
   matching native completion releases the output, marks the page done, and
   increments `next_output` and `done_pages`.  This permits page k+1 compute
   and page k+2 fill while preserving output and input hazards.
8. **Retire.** Retire exactly once only when `done_pages == 4`, both request
   latches and both in-flight lanes are empty, both input slots and the output
   are free, and the producer generation still matches.  Then consume that
   generation and release descriptor-lifetime tile/register credits.

The arbitration bit changes only on a successful push.  A rejected latch
remains byte-for-byte stable, while the other lane may be tried on the next
issue opportunity; finite round robin prevents either lane from starving.
Completion callbacks carry `(generation,page,action,slot)` rather than relying
on whichever page is currently at the head.

### Tile and data hazards

- A fill may write a slot only after the old owner was released by matching
  compute completion; issue is not sufficient because the ALU may still read
  the input.
- Compute reads exactly one filled input owner and writes only the free shared
  output.  Store reads that output until its matching completion, so the output
  cannot be reused at store issue.
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

## Conditional critical-path lower bounds

The executable recurrence in the parser assigns input slot `page % 2`, keeps
one in-order fill lane, releases an input only on compute completion, and
serializes compute+store on one shared output.  It fixes page-ready ticks and
every observed per-page stage duration, while assuming zero dispatch cost and
that fixed stage durations do not inflate when the fill and output lanes
overlap.  Dispatch gaps and cross-lane IF, cache, memory, and functional-unit
contention are absent.  These are ideal-resource assumptions.  The results are
conditional critical-path lower bounds for that recurrence, not predicted
gem5 results and not gem5 speedups.

| Quantity | Observed trace | Conditional two-input-slot bound | Distance to bound |
|---|---:|---:|---:|
| Submit -> retire | 44,828,485 | 44,182,453 | 646,032 |
| First page ready -> retire | 15,020,870 | 14,374,838 | 646,032 |
| Completion tick | 3,170,484,672 | 3,169,838,640 | 646,032 |

Under this recurrence, pages 2 and 3 fill during page 1's output-store chain;
their two 323,016-tick fills account for the 646,032-tick distance.  Page 1's
fill cannot be hidden because page 1 becomes ready 5,476,874 ticks after page
0's store completion.  The bound does not claim those observed stage durations
would remain unchanged in a modified controller, that fill and store traffic
will be contention-free, or that gem5 would realize the bound.

The ideal page schedule is:

| Page | Input | Fill [issue, complete) | Compute [issue, complete) | Store [issue, complete) |
|---:|---:|---:|---:|---:|
| 0 | 0 | [3,155,463,802, 3,155,787,131) | [3,155,787,131, 3,155,947,387) | [3,155,947,387, 3,158,706,169) |
| 1 | 1 | [3,164,183,043, 3,164,506,372) | [3,164,506,372, 3,164,666,628) | [3,164,666,628, 3,166,230,689) |
| 2 | 0 | [3,164,746,130, 3,165,069,146) | [3,166,230,689, 3,166,390,945) | [3,166,390,945, 3,167,836,379) |
| 3 | 1 | [3,165,069,146, 3,165,392,162) | [3,167,836,379, 3,167,996,635) | [3,167,996,635, 3,169,838,640) |

Regenerate the machine-readable analysis with:

```sh
python3 experiments/analysis/transparent_double_buffer_trace_analysis.py \
  /data1/nier/dx100-runs/2026-08-02-transparent-spd-premeeting/transparent_4k_retry1/run/virtual_trace.log
```
