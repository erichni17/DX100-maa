# Third independent review: SPD functional repair

Date: 2026-08-08
Reviewed HEAD: `797977208beed05427c66feb676f08c14e6c1ebb`
Reviewed implementation series: `1ab777806e5d7957a18c6bdc20fd35a07ad99abf^..797977208beed05427c66feb676f08c14e6c1ebb`

## Verdict

**REJECT** promotion of the live gem5/cache-port integration. The standalone
controller, transport, bridge, and two functional cache modes remain useful
bounded evidence, but the repair does not establish the requested live retry
provenance and behavioral-test closure. One committed focused contract gate
also fails at the reviewed HEAD.

The exact supported evidence is: a bounded standalone runtime with one
32,768-byte private FP64 payload bank per runtime, Serial4K and PingPong2K
full-range functional transforms, finite controller/transport ownership, and
authenticated mock-peer responses. The most that source inspection supports
for the live adapter is an experimental **four-core/four-cache-port, one-MAA,
one-logical-execution-at-a-time, 64-byte-line, immediate-translation,
uninterrupted pre-materialized-backing transform**. This review does not accept
checkpointing during live logical state, multi-MAA operation, indirect
producer/reorder behavior, compute timing, performance, total-area, or iso-area
claims. Because the live retry boundary is not closed below, even that narrow
live envelope is not promoted as validated behavior.

## Findings

### High — Retry authority is not sourced exclusively by the retry callback

The repair correctly passes the actual `CacheSidePort::core_id` on both timing
responses and request-retry callbacks, and `LogicalSPDCacheLiveAdapterState`
holds a one-shot per-execution permit. `serviceLogicalSPD` cannot retry before
that permit and consumes it once (`src/mem/MAA/MAA.cc:1466-1488`). A wrong
different port does not match.

However, `CacheSidePort::recvTimingResp` also calls `setUnblocked` when an
ordinary response releases the local `MAX_XBAR_PACKETS` block
(`src/mem/MAA/CacheSidePort.cc:31-39`). `setUnblocked` unconditionally calls
`notifyLogicalSPDRetry`, regardless of whether it was entered from
`recvReqRetry(CACHE_FAILED)` or from the response-side
`MAX_XBAR_PACKETS` release (`src/mem/MAA/CacheSidePort.cc:86-90,125-130`). The
per-execution state therefore cannot substantiate its stated contract that
authority is latched only by a cache retry callback: a same-port response-side
capacity event can mint the indistinguishable permit. The response may be
relevant to local capacity, but it is not an authenticated downstream request
retry callback, and the code records neither the refusal reason nor the event
kind in the permit.

There is also no single port-level arbiter between this logical wakeup and the
pre-existing native `unblockCache` wakeup: `setUnblocked` schedules native
cache service first and then schedules logical service. The cache port can
reject the later attempt again, so the one-MAA path can recover, but the claim
that one accepted callback grants exactly one matching retry opportunity is
not demonstrated at the shared live boundary.

Impact: actual response/retry port identity is improved, and unrelated
different ports cannot advance the execution, but same-port response-capacity
events and shared native service remain conflated with logical retry authority.

### High — The required live behavioral matrix is not present

The new tests do not drive `CacheSidePort`, `MAA::notifyLogicalSPDRetry`, or
`MAA::serviceLogicalSPD`:

- `logical_spd_cache_port_provenance_test.cc` calls only a pure equality
  helper. Its wrong-response check does not prove that the actual response
  port reaches the live adapter without mutation.
- `logical_spd_cache_live_adapter_state_test.cc` proves local one-shot
  consumption and no consumption before a permit. Its comment claims a
  same-port isolation case, but the two adapters are armed on ports 1 and 2
  (`tests/maa/logical_spd_cache_live_adapter_state_test.cc:12-30`). The only
  multi-object case is therefore different-port isolation.
- No behavioral test covers two same-port pending executions, the first-match
  `return` in `notifyLogicalSPDRetry`, a wrong live retry port, response-driven
  `MAX_XBAR_PACKETS` release, native/logical shared-service ordering, or a
  scheduled logical event making no progress before a permit.

Multi-execution live behavior is currently unreachable because admission
panics unless `num_maas == 1`, which is a sound scope restriction, but it does
not satisfy the requested regression coverage and cannot justify deleting
that restriction later.

The focused payload runner also fails a committed source contract. The bridge
now correctly returns `admissionsClosed`, while
`test_logical_spd_hidden_payload_contract.py:91-93` still requires
`admissionClosed()` to return constant false. Thus
`run_logical_spd_hidden_payload_unit.sh` exits nonzero after all of its C++
optimized/sanitizer tests pass.

### Medium — The live port-count scope is implicit and accepts incompatible configurations

Transport port selection is permanently four-way
(`LogicalSPDCacheTransport::PortCount == 4`) and uses address bits 6-7
(`src/mem/MAA/LogicalSPDCacheTransport.hh:17-24`,
`src/mem/MAA/LogicalSPDCacheTransport.cc:178-182`). The live packet is instead
sent through `core_addr`, whose width comes from `log2(num_cores)`
(`src/mem/MAA/MAA.cc:163`, `src/mem/MAA/CacheSidePort.cc:117-120`). Admission
rejects multi-MAA but does not require four cores/cache ports
(`src/mem/MAA/MAA.cc:1197-1215`).

The committed live runner happens to use four cores and 64-byte lines, for
which virtual-to-physical translation preserves the page-offset routing bits.
Other otherwise accepted one-MAA core counts can select a different actual
port than the transport token and fail only after work is live. The live
admission boundary should either derive transport routing from the actual
cache-port topology or reject every non-four-port configuration before
claiming a callback owner.

## Questions resolved without a blocking finding

- **Drain:** `MAA::drain` closes bridge admission before checking state and
  panics on any non-quiescent logical runtime; `drainResume` reopens only after
  another quiescence check (`src/mem/MAA/MAA.cc:1560-1575`). There is no
  logical `serialize`/`unserialize`, `nativeDrainIntegrated()` remains false,
  and the diagnostic explicitly says live serialization is unsupported. This
  is an appropriate fail-stop boundary for uninterrupted-only scope. The tests
  exercise bridge closure, not the actual MAA drain override.
- **Producer provenance:** the false `reorder_contract=producer_supplied` live
  trace was removed. The trace now says
  `source_contract=pre_materialized_backing`, and the milestone/runner retain
  the no-producer/reorder-evidence limitation. The only remaining occurrence
  of the old phrase is historical text in the earlier rejection report.
- **Functional ownership:** no new unbounded response owner or payload alias
  was found. Full Serial4K in-place and PingPong2K disjoint transforms, exact
  finite fill/writeback acknowledgements, stale identities, wrong transport
  ports, abort stages, guard regions, and packed accounting are exercised by
  the standalone suites.

## Validation at exact HEAD

- `run_logical_spd_cache_controller_unit.sh`: PASS, optimized and ASan/UBSan;
  12 Python contracts PASS.
- `run_logical_spd_cache_bridge_lifecycle_unit.sh`: PASS, bridge,
  provenance-helper, and adapter-state tests optimized and ASan/UBSan; 7 Python
  contracts PASS.
- `run_logical_spd_cache_abi_unit.sh`: PASS; 8 ABI and 11 transparent-controller
  Python contracts PASS.
- `run_logical_spd_hidden_payload_unit.sh`: hidden-payload, transport, and
  vertical-slice C++ tests PASS optimized and ASan/UBSan; FAIL in the stale
  admission-closure Python assertion described above. The accounting test was
  run separately and all 7 cases PASS.
- `git diff --check 1ab77780^..79797720`: PASS.
- Focused `MAA.o`, `CacheSidePort.o`, bridge, and transport object build:
  INCOMPLETE. The fresh build directory spent the review window generating
  gem5 Python/header prerequisites and was stopped before reaching the requested
  objects once the independent REJECT findings were established. No gem5
  binary was linked and no simulation was run.

## Required repair gate

Represent downstream `recvReqRetry` and local response-capacity release as
distinct authorities, and arbitrate one concrete pending owner per actual
cache port before shared native/logical service. Add a behavioral live-adapter
harness covering wrong response and retry ports, same-port and different-port
pending owners, unrelated responses, shared-service ordering, finite permit
consumption, and no progress before the matching authority. Make live
admission reject non-four-port topology (or remove the hard-coded topology),
fix the stale committed contract, and rerun all focused gates plus the changed
translation units before reconsidering promotion.
