# Shared-checkpoint hybrid critical-path audit (2026-08-13)

## Scope and evidence

Read-only audit of `/data1/nier/dx100-runs/2026-08-13-general-hybrid-api-shared-checkpoint-d0206296-r1`, source commit `d02062968235c49d93dba1b65b4951d057a7a0e9`, one completed replica per arm.  The frozen manifest identifies the same hybrid checkpoint for `native16` and `hybrid_token_stream_ld`; both logs end in `m5_exit`, the final stats window is present, and the campaign report records the common exact correctness key `7228541527853630339`.  This report uses **only `simTicks`** for performance; it neither changes implementation nor launches gem5.

`native16 = 21,406,070 simTicks`; `hybrid_token_stream_ld = 23,603,017 simTicks`.  Thus the hybrid control is slower by **2,196,947 simTicks (10.263%)** (`23,603,017 / 21,406,070 - 1`), or native/hybrid speedup 0.906921.  It is still a correctness control, not a performance treatment.

## Reconstructed coarse timing

All times below are trace ticks (`simTicks`), and deltas are simple differences; they are not host time or a sum of potentially overlapped work.

| event / exposed interval | tick | duration | interpretation |
|---|---:|---:|---|
| hybrid indirect operation begins | 3,445,969,118 | -- | producer begins the virtual result generation |
| early producer-line ledger begins | 3,445,968,806 | -- | fixed ledger accepts pre-admission producer line ACKs |
| materializer submits page 0 | 3,448,057,455 | 2,088,649 after ledger begin | first consumer-page admission |
| page 0 producer ACK / page ready | 3,459,896,992 / 3,459,901,374 | 11,843,919 from submit to ready | producer-limited, with only 365 forwarded lines by this point |
| page 1 submit / producer ACK / ready | 3,460,040,034 / 3,467,126,666 / 3,467,157,966 | 7,117,932 submit-to-ready | producer availability overlaps the page-0-to-page-1 handoff |
| page 2 submit / producer ACK / ready | 3,467,207,421 / 3,467,099,435 / 3,467,744,528 | 537,107 submit-to-ready; ACK is 107,986 before submit | cache-read delivery after producer completion |
| page 3 submit / producer ACK / ready | 3,468,090,394 / 3,467,130,422 / 3,468,616,233 | 525,839 submit-to-ready; ACK is 959,972 before submit | cache-read delivery after producer completion |
| materializer retirement | 3,468,616,233 | 20,558,778 page-0-submit to final ready | all four 512-line pages close exactly |

The producer's stage interval is 21,161,304 ticks in hybrid versus 20,208,532 in native: **952,772 ticks** of extra producer-side execution.  The hybrid producer summary happens at 3,467,130,422, but final page-3 readiness is **1,485,811 ticks later**.  That is direct, trace-visible exposed tail.  The remaining difference between the whole-run gap and this producer-stage delta is **1,244,175 ticks**: the stage summary is not the whole ROI, so it must not be attributed to a materializer substage without another timestamped counter.

## What creates the exposed interval

The final exact closure is 2,048 lines: **370 (18.1%) forwarded** from a full producer `WriteResp`, and **1,678 (81.9%) ACK-gated coherent cache reads**.  There are exactly 2,048 producer line ACKs, zero page-fallback lines, zero materializer admission/dispatch fallbacks, four submissions/pages, and one retirement.  Therefore this is not a loss of ACK authority or an ABI fallback.

The evidence separates latency/ordering from bandwidth/serialization:

- Page 0 and most of page 1 are producer-latency limited: page 0 takes 11.844M ticks after submission before its final page condition; page 1's final producer ACK is only 31,300 ticks before its ready event.
- Pages 2 and 3 are **not** producer-latency limited. Their final producer ACKs predate their submissions, yet each still takes 537,107 / 525,839 ticks to make 512 lines ready (about 1,049 / 1,027 ticks per line). The consumer permits one active materialization page, so those two ready pages cannot drain together.
- This is principally bounded cache-read *serialization/latency hiding*, not demonstrated cache-link saturation. The trace has no materializer `retry=1` issues; four cache-side ports are used. The cache-read packet counter is unchanged (3,074) between arms, while hybrid cache writes rise from 2,048 to 7,101, matching the 5,053 virtual producer writes. Consequently the frozen statistics do not establish a cache-bandwidth ceiling. They do establish that arrival-before-active-page loses payload forwarding and forces the later `ReadBacking` path.
- Producer-side virtual retirement is itself pressured: 5,053 writes (383 full-line, 4,670 partial), outstanding-write high water 64, and 873 STREAM request-table-full events (versus 592 native). This can explain the page-0/page-1 producer interval, but cannot explain pages 2/3 after their ACKs are already present.

The current trace cannot validly apportion all 2.197M ticks among the two causes, because it lacks per-page read-credit occupancy, issue-to-response latency histograms, and consumer wait attribution. It does falsify the claim that raw producer latency alone explains the full gap.

## Source-function grounding

- `MAA::submitPageMaterialization` ([`src/mem/MAA/MAA.cc`](../../src/mem/MAA/MAA.cc:1343)) admits the exact token/page and calls `beginMaterializationPage`; it returns `Retry` while `pageActive` is true. This is the page-level serialization point.
- `HybridConsumerPipeline::beginMaterializationPage` ([`HybridConsumerPipeline.hh`](../../src/mem/MAA/HybridConsumerPipeline.hh:481)) stores one `activeMaterializationPage`; `pendingRead` therefore serves only that page. `completeMaterialize` ([same file](../../src/mem/MAA/HybridConsumerPipeline.hh:498)) clears it only after all 512 lines commit.
- `MAA::setVirtualLineWordsReady` ([`MAA.cc`](../../src/mem/MAA.cc:4177)) accepts exact line WriteResp authority. Its forwarding predicate requires a full-word line, payload, *and the matching page currently active* ([`MAA.cc`](../../src/mem/MAA/MAA.cc:4277)); otherwise it preserves readiness but not payload, causing cache-read fallback.
- `HybridConsumerPipeline::notifyProducerLineWriteAck` and `notifyProducerWriteAck` ([`HybridConsumerPipeline.hh`](../../src/mem/MAA/HybridConsumerPipeline.hh:295), [`HybridConsumerPipeline.hh`](../../src/mem/MAA/HybridConsumerPipeline.hh:271)) respectively release exact full lines or conservatively release a page after its final ACK. The observed zero page fallback confirms the line path closed every line.
- `MAA::servicePageMaterialization` ([`MAA.cc`](../../src/mem/MAA/MAA.cc:2574)) issues the fallback `ReadBacking` requests, waits for the SPD-ready tick, and commits one line. `MAA::setVirtualPageReady` ([`MAA.cc`](../../src/mem/MAA/MAA.cc:4309)) supplies the final page ACK; final accounting/retirement is in `finishPageMaterialization` ([`MAA.cc`](../../src/mem/MAA/MAA.cc:2440)).

## Ranked legal bounded-hardware candidates

These are proposals, not measured gains. Each keeps fixed, explicitly provisioned state and exact WriteResp/transaction authority.

1. **Bounded early-payload capture for materializer pages (highest predicted impact).** Add a fixed 64-byte payload store plus valid/full-mask metadata for a bounded number of early producer lines/pages, so a line that arrives before its page becomes active can later commit without `ReadBacking`. A useful first legal point is two 512-line pages = 32 KiB payload plus fixed metadata; scale only after accounting it. It targets 1,678 fallback reads and the 1.486M exposed post-producer tail. Falsify with `early_payload_forwarded_lines`, `cache_read_fallback_lines`, page-ready ticks, and unchanged correctness/ACK closure; reject it if payload captures do not replace fallback reads or if `simTicks` does not fall.
2. **Two independently bounded active-page windows (medium impact).** Replace the single `activeMaterializationPage` with two disjoint page states, each with its own fixed line credits/commit records and destination ownership. This can overlap already-ACKed page 2 and page 3 drains; the direct serial span is 1,062,946 ticks, so the maximum attributable saving is below that (below 4.50% of hybrid `simTicks`). Falsify with per-page active overlap, credit high-water, page-2/page-3 ready ticks, retry counts, and exact 2,048-line closure. Reject if port/credit contention merely moves the same tail.
3. **Increase the fixed materializer line-credit/commit pool (lower, conditional impact).** Grow the charged 16-line buffer/commit pool only to a predeclared small bound (for example 32 lines) while retaining four ports and exact owners. It can hide response/commit latency within each page but cannot remove single-page ordering or the cache-read traffic. Falsify with peak credit occupancy, issue-to-response/response-to-commit latency, `retry=1` count, and page-ready `simTicks`; reject if occupancy never reaches 16 or page service slope is unchanged.

Before any promotion, run matched repetitions from the same checkpoint and add the missing per-page waiting/credit counters.  The one-replica result supports the causal classification above, not a precise projected speedup.
