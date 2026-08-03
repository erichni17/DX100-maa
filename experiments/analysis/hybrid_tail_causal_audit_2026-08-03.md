# Hybrid overhead tail causal audit

## Outcome

The accepted pair remains 39,971,978 versus 45,449,165 simTicks (+5,477,187). All pages became ready 298,915 ticks after the native ROI endpoint, and 5,080,303 ticks remained to the hybrid endpoint. This is an aligned timeline observation, not a causal decomposition.

| Hypothesis | Fail-closed result |
|---|---|
| Lost A-request reordering | `not_supported_by_request_geometry_order_quality_unresolved` |
| Page fill/backpressure | `fill_present_controller_backpressure_absent_causality_unisolated` |
| Per-page consumer serialization | `mechanism_present_4k_causal_magnitude_unresolved` |
| Producer backing-store writes | `no_direct_post_ready_outstanding_write_tail_indirect_perturbation_unresolved` |
| Final retirement | `controller_bookkeeping_gap_zero_final_store_transport_present` |

The post-ready interval reconciles exactly to clipped controller actions, the zero-tick controller retirement gap, and the ROI epilogue. Additive accounting does not imply eliminable latency.

## Run and provenance boundary

Audited accepted gem5 arms: **5** (2 pair + 3 ping-pong). New gem5 arms launched: **0**. Every arm has one raw observation; no repetition-based noise claim is made.

The accepted ping-pong 2K schedule treatment recovered 376,226 simTicks relative to serial 2K. It does not estimate a legal 4K double-buffer treatment because that would require more visible payload.

## Falsifiable next test

Add treatment-neutral blocker-residency counters to the existing controller schedule points (producer-not-ready, STREAM busy, ALU busy, slot-owned, IF-full), plus first/last consumer STREAM packet acceptance ticks; run native16K and transparent4K from one new shared deferred checkpoint with one instrumented binary and the same exact-output oracle.

If serial consumer service dominates, post-ready blocker residency must reconcile to STREAM/ALU/slot ownership and consumer packet acceptance; producer-not-ready and producer backing-write outstanding counters must remain zero after 4/4 readiness. A nonzero unexplained residual or post-ready producer-write count falsifies that explanation.
