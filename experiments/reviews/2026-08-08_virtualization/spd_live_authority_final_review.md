# Final review: SPD live cache authority repair

Date: 2026-08-08
Final commit: `d8bb8e9df0e0b828fa92ffa961ff007fb0745481`
Reviewed series: `1ab77780^..d8bb8e9d` (`4e6539e066e77973406b48820bbd881bdd27344a` through `d8bb8e9df0e0b828fa92ffa961ff007fb0745481`)

## Verdict and findings

**ACCEPT for promotion, with the exact narrow scope stated below.** I found no
promotion-blocking correctness, ownership, lifecycle, native-service, or
evidence-boundary defect in the final commit or complete requested series.

This acceptance does not promote the broader design described in historical
planning documents. It accepts one MAA, exactly four cores and ordinary cache
ports, one live logical execution, 64-byte cache lines, pre-materialized backing,
and uninterrupted functional fill/scalar-transform/writeback behavior in the
two committed private-payload modes. Drain is deliberately fail-stop for live
logical state, and no live-state serialization is claimed.

## Falsification results

1. **Retry and capacity authorities remain distinct.** A refused send records
   either `LocalResponseCapacity` or `DownstreamRequestRetry`; port callbacks
   carry the correspondingly typed `ResponseCapacityReleased` or
   `DownstreamRequestRetry` event. The boundary's exact matcher is the only
   permit-minting transition, and an already-pending permit rejects another
   notification
   (`src/mem/MAA/LogicalSPDCacheLiveAdapterState.hh:25-36,68-79,138-144`;
   `src/mem/MAA/CacheSidePort.cc:96-133,152-162`). The production retry path
   calls `resumeLocalCapacity` and `recvReqRetry` through separate APIs; neither
   path manufactures the other authority
   (`src/mem/MAA/MAA.cc:1478-1493`;
   `src/mem/MAA/LogicalSPDCacheGem5Bridge.cc:265-280`).

2. **One concrete logical owner exists per actual port.** The four finite slots
   reject zero/out-of-range owners, a second owner on an occupied port, the same
   owner on another port, and re-arm while a permit is pending. Consumption
   authenticates owner, port, and authority and is one-shot
   (`src/mem/MAA/LogicalSPDCacheLiveAdapterState.hh:44-65,68-105`). Production
   notification additionally resolves the returned owner to the active
   execution and exact retry port, panicking on missing or divergent state
   (`src/mem/MAA/MAA.cc:1605-1626`). Wrong response ports fail before transport
   mutation, while unrelated native responses never enter the logical response
   path (`src/mem/MAA/CacheSidePort.cc:31-45`;
   `src/mem/MAA/MAA.cc:1397-1444`).

3. **Shared native/logical ordering is explicit and finite.** On an unblock,
   native cache work is scheduled before the typed logical notification
   (`src/mem/MAA/CacheSidePort.cc:152-162`;
   `src/mem/MAA/Port.cc:380-384`). The logical permit remains latched until the
   exact execution consumes it. The retry branch performs one downstream send
   attempt, then either releases the owner on acceptance or re-arms that same
   owner with the newly observed refusal kind before breaking out of service
   (`src/mem/MAA/MAA.cc:1478-1529`). A refused `Packet` stays in
   `execution.retryPacket`; an accepted packet transfers to the port and is not
   reused. A returned logical packet is authenticated and disposed by the
   transport, then its sender state, data, and packet are deleted exactly once
   (`src/mem/MAA/MAA.cc:1397-1444`;
   `src/mem/MAA/CacheSidePort.cc:31-46`).

4. **The production port path carries exact event kind and port.** The real
   `CacheSidePort` passes `core_id` into response authentication and reports its
   unblock reason as the typed event (`src/mem/MAA/CacheSidePort.cc:31-45,90-93,152-162`).
   `MAA` stores actual routed port and refusal authority in the active execution,
   checks translated physical routing against the transport callback hash on
   first send and retry, and resolves notifications by owner and port
   (`src/mem/MAA/MAA.cc:1494-1527,1531-1569,1605-1626`). The behavioral harness
   drives refusal, response-capacity release, downstream retry, native-first
   ordering, same-port contention, different-port isolation, wrong and unrelated
   events, one-shot consumption, re-refusal, and completion transitions; it is
   not limited to provenance equality checks
   (`tests/maa/logical_spd_cache_live_adapter_state_test.cc:14-194`).

5. **Incompatible live topology fails before callback claim.** Submission checks
   the MAA index, exactly one MAA, exactly four cores, exactly four ordinary
   cache-side ports, and 64-byte system lines before `claimCallback`
   (`src/mem/MAA/MAA.cc:1198-1213,1270-1274`). The wire ABI validates logical
   descriptor and register indices before dispatch
   (`include/gem5/maa_logical_spd_cache_abi.hh:180-229`;
   `src/mem/MAA/CpuSidePort.cc:413-470`). Transport construction fixes four
   ports and 64-byte lines, its route hash masks to `[0,3]`, response handling
   rechecks both callback port and address hash, and the live path checks the
   physical route is in range and equals the transport-selected port
   (`src/mem/MAA/LogicalSPDCacheTransport.cc:83-90,178-182,655-691`;
   `src/mem/MAA/MAA.cc:1538-1545`).

6. **Admission close is live state, and drain makes no serialization claim.**
   `claimCallback` observes the mutable closed-admission bit; close/reopen are no
   longer a constant false contract
   (`src/mem/MAA/LogicalSPDCacheGem5Bridge.hh:74-78`;
   `src/mem/MAA/LogicalSPDCacheGem5Bridge.cc:195-224`). `MAA::drain` closes
   admission and panics unless every logical runtime is quiescent; resume also
   requires quiescence. The diagnostic explicitly says serialization is
   unsupported (`src/mem/MAA/MAA.cc:1635-1651`). No `serialize` or `unserialize`
   implementation for live logical state was added or claimed.

7. **The evidence boundary remains narrow.** The benchmark identifies ordinary
   visible SPD payload as additive and prints `isoarea_timing_claim=0`
   (`benchmarks/API/test_logical_spd_cache_live.cpp:61-76`). The runner records
   the two explicit modes, private payload and packed-metadata lower bound, the
   additive visible-SPD fact, and no iso-area timing claim
   (`experiments/scripts/run_logical_spd_cache_live_smoke.sh:19-22,61-74`). The
   milestone states that the scalar transform is an untimed host loop and
   disallows `simTicks`, throughput, overlap, area, and performance conclusions
   (`experiments/analysis/logical_spd_functional_milestone_2026-08-08.md:7-19`).
   The source is pre-materialized before the pristine-input checkpoint; there is
   no producer handoff or reorder-survival evidence.

8. **No leak, stale-permit, deadlock/livelock, disposal, reset/drain, or native
   regression was found in the accepted scope.** A logical retry packet always
   remains correlated to its bridge owner until acceptance; each accepted line
   response releases its finite transport record/credit; operation completion
   retires and resets the runtime before clearing the execution; boundary state
   is released on retry acceptance and completion. Native callers retain the
   default no-refusal-output interface, and no logical notification mutates a
   native queue or response map (`src/mem/MAA/MAA.hh:265-310`;
   `src/mem/MAA/MAA.cc:1453-1592`; `src/mem/MAA/Port.cc:499-699`).

## Validation

All validation ran at exact `d8bb8e9d`; no gem5 link or simulation was run.

- `run_logical_spd_cache_controller_unit.sh`: optimized and ASan/UBSan C++
  passed; 12 Python contracts passed.
- `run_logical_spd_hidden_payload_unit.sh`: hidden-payload, transport, and
  vertical-slice C++ passed in optimized and ASan/UBSan builds; 9 hidden-payload
  and 7 accounting Python contracts passed. The reported values remained
  34,077 packed-semantic lower-bound bytes and 35,328 host-runtime bytes, with
  the latter explicitly not a synthesized hardware size.
- `run_logical_spd_cache_bridge_lifecycle_unit.sh`: bridge lifecycle, port
  provenance, and live-adapter-state C++ passed in optimized and ASan/UBSan
  builds; 7 Python contracts passed.
- `run_logical_spd_cache_abi_unit.sh`: optimized C++ passed; its 8 ABI and 11
  transparent-controller Python contracts passed. A separate build of the same
  ABI test with ASan/UBSan also passed because this runner does not include a
  sanitizer invocation itself.
- `git diff --check 1ab77780^ d8bb8e9d`: passed.
- `git diff --check` on the initially clean review worktree: passed.
- Targeted changed-object builds were not applicable: no `build/X86` generated
  state existed in this worktree. No full build was started.

## Exact supported scope

Promotion supports only the committed functional mechanism: one MAA, four cores
and four ordinary cache ports, 64-byte lines, one active logical FP64 scalar
execution, exactly 16K logical elements, 4K visible physical elements, either
Serial4K (four 4K pages over one 32-KiB private slot) or PingPong2K (eight 2K
pages over two 16-KiB private slots), pre-materialized source backing, exact
timed fill/writeback response ownership, and uninterrupted execution to
completion. Ordinary visible SPD remains allocated in addition to the 32-KiB
private payload.

This review does **not** support multiple MAAs, concurrent logical executions,
an indirect producer or reorder-survival claim, producer/consumer overlap,
modeled scalar-compute or cache-control timing, speedup, throughput, performance,
checkpoint/restore of live state, graceful live drain, synthesized total area,
replacement of ordinary visible SPD, total-area equivalence, or any iso-area
claim.
