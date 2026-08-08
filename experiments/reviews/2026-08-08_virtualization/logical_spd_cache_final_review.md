# Logical SPD-cache repaired-series review — 2026-08-08

**Scope.** Reviewed `1ab77780`, `dadf3bd9`, `f05fa75d`, and `ddba0d15`
against integration base `4e6539e`.  This is a source and host-test review;
no gem5 simulation was run.

## Findings (highest priority first)

### F1 — REJECT: the live integration does not authenticate the actual cache port

`CacheSidePort::recvTimingResp` has the physical port object but calls
`MAA::recvLogicalSPDTimingResp(pkt)` without its `core_id`
([`CacheSidePort.cc:30`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/CacheSidePort.cc:30)).  The latter supplies the callback value copied from the
outgoing sender state, not the responding port
([`MAA.cc:1417`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/MAA.cc:1417)).
Transport therefore verifies only that this stored/address-derived value matches
itself ([`LogicalSPDCacheTransport.cc:652`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/LogicalSPDCacheTransport.cc:652)).

The lifecycle host test similarly passes `request.callbackPort` back into both
retry and response paths ([`logical_spd_cache_bridge_lifecycle_test.cc:256`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/tests/maa/logical_spd_cache_bridge_lifecycle_test.cc:256),
[`logical_spd_cache_bridge_lifecycle_test.cc:320`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/tests/maa/logical_spd_cache_bridge_lifecycle_test.cc:320)), so it cannot catch a response or retry from the wrong cache-side port.  This is consistent with—rather than cured by—the milestone's explicit blocker on actual responding/retrying-port authentication.  The integration must not be accepted as authenticated until the physical port identity is threaded through and tested.

### F2 — missing end-to-end Serial4K exact-alias regression

The implementation permits same-base source/destination and reserves the sole
slot for an in-place update ([`LogicalSPDCacheSlice.hh:546`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/LogicalSPDCacheSlice.hh:546),
[`LogicalSPDCacheController.hh:498`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/LogicalSPDCacheController.hh:498)).  The direct datapath test establishes only a 4096-element, scalar-zero alias
([`logical_spd_hidden_payload_test.cc:73`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/tests/maa/logical_spd_hidden_payload_test.cc:73)).
The vertical full-16K path tests each mode with distinct backing spans.  Add a
Serial4K full-operation same-backing test with a non-zero scalar and verify all
four writebacks plus source/destination guard integrity.

### F3 — partial-overlap test proves no mutation, but not the overlap branch

The test supplies `base + 64` and observes unchanged state
([`logical_spd_cache_vertical_slice_test.cc:716`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/tests/maa/logical_spd_cache_vertical_slice_test.cc:716)).
That backing is first rejected by the mandatory 128-KiB alignment check
([`LogicalSPDCacheSlice.hh:421`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/LogicalSPDCacheSlice.hh:421)), before the explicit overlap condition
([`LogicalSPDCacheSlice.hh:546`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/LogicalSPDCacheSlice.hh:546)).
Thus the externally observable guarantee—partial spans are rejected before
mutation—holds, but the test does not isolate the stated overlap check.  This
is a coverage gap, not evidence of mutation.

## Verified properties

* One runtime owns exactly `std::array<double, 4096>` (32,768 bytes)
  ([`LogicalSPDCacheRuntime.hh:951`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/LogicalSPDCacheRuntime.hh:951)).  Serial4K configures 4×4096-element pages and one slot; PingPong2K configures 8×2048-element pages and two slots
  ([`LogicalSPDCacheSlice.hh:321`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/LogicalSPDCacheSlice.hh:321)).  The source tests assert both exposed layouts.
* The logical knob is separately parsed and forwarded
  ([`Options.py:238`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/configs/common/Options.py:238),
  [`MAAConfig.py:26`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/configs/common/MAAConfig.py:26)); its two values map independently of `transparent_spd_mode`
  ([`MAA.cc:247`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/MAA.cc:247)).  The live runner selects both modes and pins `--maa_physical_tile_elements=4096`
  ([`run_logical_spd_cache_live_smoke.sh:19`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/experiments/scripts/run_logical_spd_cache_live_smoke.sh:19),
  [`run_logical_spd_cache_live_smoke.sh:55`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/experiments/scripts/run_logical_spd_cache_live_smoke.sh:55)).
* The ordinary SPD allocation remains a separate visible allocation
  ([`MAA.cc:241`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/MAA.cc:241)); the private bank is added by the bridge/runtime construction at
  [`MAA.cc:253`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/MAA.cc:253).
* Host unit coverage exercises stale callback token, a refused-send/retry,
  delayed/out-of-order line completions, abort at queued/pending/inflight/
  delivering/reserved/computing/dirty/writeback stages, drain/reset/teardown,
  and invalid geometry.  It is sound coverage for the host controller and
  transport contracts, but it is not a gem5-port integration test.
* No timing, overlap, synthesized-area, iso-area, producer-generation, or
  reorder-survival result is established by the host transform.  The compute is
  called synchronously from `driveCompute` ([`LogicalSPDCacheRuntime.hh:563`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/src/mem/MAA/LogicalSPDCacheRuntime.hh:563)), and the milestone correctly declares
  `isoarea_timing_claim=0` and lists producer/reorder/port authentication as
  blockers ([`logical_spd_functional_milestone_2026-08-08.md:14`](/data1/nier/worktrees/codex-sessions/spd-repair-final-review-20260808-20260808-111030-9ce99124/DX100-integrate-virtualization-week-20260803/experiments/analysis/logical_spd_functional_milestone_2026-08-08.md:14)).  The accounting's `sizeof` output is explicitly marked host-only.

## Test evidence

* PASS: `experiments/scripts/run_logical_spd_cache_controller_unit.sh` — optimized and ASan/UBSan C++ gate, plus 12 Python contract tests.
* PASS: optimized and ASan/UBSan `logical_spd_hidden_payload_test`; optimized `logical_spd_cache_transport_test`.
* PASS: optimized `logical_spd_cache_bridge_lifecycle_test`.
* The remaining sanitizer compiles were contended by an unrelated active gem5 build and were not used as acceptance evidence.  No gem5 simulation was started.

## Recommendation

**REJECT for integration acceptance.**  The two-mode payload/geometry repair is
functionally coherent at the reviewed host-contract level and its documentation
does not make performance or area claims.  However, actual responding/retrying
cache-port authentication is still absent from the gem5 boundary, and the two
targeted alias/overlap coverage gaps should be closed before this series is
represented as a complete live integration.
