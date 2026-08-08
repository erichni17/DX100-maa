# Smallest timing-legal logical SPD-cache vertical slice

Date: 2026-08-08
Source commit: `797977208beed05427c66feb676f08c14e6c1ebb`
Scope: design and source trace only; no mechanism source was changed and gem5 was
not run.

Review status: **STOP**. The third independent functional review at this exact
commit is REJECT. Its cache-port ownership and admission findings are Stage A
prerequisites below; timing implementation must not start until their repair is
independently reviewed and accepted.

## Decision

After Stage A acceptance, implement **Serial4K only**, SE only, one MAA, one
outstanding logical FP64 scalar operation, and four strictly ordered 4K pages.
Keep the existing fixed logical controller and 64-byte cache transport. Replace
the zero-time local operations with three modeled resource boundaries:

1. cache response -> private-slot fill commit through shared SPD write
   bandwidth;
2. private-slot scalar operation through the existing MAA ALU and shared SPD
   read/write timing resources; and
3. private-slot read -> write-request snapshot through shared SPD read
   bandwidth.

The functional C++ copies/loop may still mutate simulator storage, but only at
the corresponding completion event after finite resources have been reserved.
No result byte may become available to writeback before ALU completion. This is
the smallest honest timing model; replacing the host loop with RTL-like
per-element events is unnecessary because the page is unobservable while the
ALU owns it.

This slice is a correctness-and-timing-conservation milestone, **not** a speedup
claim. Comparative performance remains stopped until both arms use the same
payload bytes, packet throttle, cache ports, ALU/SPD resources, traffic, ROI,
and final-`WriteResp` completion boundary.

## 1. Exact bypasses at `7979772`

The external cache traffic is partly real today; the local SPD/cache datapath
is not. The distinction matters.

### Source

- The guest creates the entire 128 KiB source and initializes the destination
  before `m5_checkpoint` (`benchmarks/API/test_logical_spd_cache_live.cpp:55-76`).
  Restore starts from that pre-materialized image, and stats are reset only at
  lines 85-86. Input production, producer/consumer overlap, and producer
  generation handoff are therefore outside the measured operation.
- `registerSource` declares every logical source page ready immediately, without
  a timed producer transaction or generation callback
  (`LogicalSPDCacheSlice.hh:456-503`). This is a backing-valid declaration, not
  evidence that data resides in a physical SPD slot.
- A miss does issue 64-byte `ReadReq` packets through `sendPacketCache`, so
  cache/memory latency is not bypassed (`MAA.cc:1320-1381,1491-1512`). However,
  `recvTimingResp` copies each line into a Transport line buffer and the Bridge
  immediately calls `commitDelivery`; that second `memcpy` installs the line in
  the private page in the same callback with no SPD write-port reservation or
  event (`LogicalSPDCacheTransport.cc:705-715,779-826` and
  `LogicalSPDCacheGem5Bridge.cc:274-285`). Thus external fetch latency is timed,
  but cache-response-to-SPD fill bandwidth is zero-time.
- Per-line address translation calls `translateTiming`, then requires it to
  finish synchronously (`MAA.cc:1334-1347`). No TLB/walker/SE lookup delay is
  charged. V1 must be explicit that it is SE with a fixed positive lookup stage,
  not an FS/IOMMU model.

### Transform

- Once a page is resident, `serviceLogicalSPD` calls `driveCompute`
  (`MAA.cc:1514-1528`). `driveCompute` immediately calls `beginCompute` and
  `executeCompute` in the same C++ call
  (`LogicalSPDCacheRuntime.hh:485-567`).
- `executeCompute` invokes `LogicalSPDCacheDatapath::transform`, whose host loop
  reads and writes all 4096 FP64 elements (`LogicalSPDCacheDatapath.hh:46-111`),
  then publishes compute completion in the same event. It reserves no ALU lane,
  no SPD port, and no cycles. This is the direct reason current `simTicks`,
  overlap, throughput, and performance are invalid.
- `serviceLogicalSPD` may prepare/send several records in one invocation
  (`MAA.cc:1445-1532`; the bound is `ResponseCredits + 2`). Four response
  credits can therefore launch four lines at one tick. Transport capacity is
  finite, but issue bandwidth is not yet a per-cycle resource.

### Destination

- The host transform writes the private payload in zero time. For every
  writeback line, `prepare` snapshots 64 bytes with `memcpy` immediately
  (`LogicalSPDCacheTransport.cc:468-504`), and `Packet::setData` copies it into
  the gem5 Packet immediately (`MAA.cc:1357-1365`). Neither private-SPD read
  bandwidth nor a ready event gates the snapshot.
- The resulting `WriteReq` does traverse the coherent cache-side timing port,
  and a matching `WriteResp` is required before the line/page/operation is
  complete (`MAA.cc:1385-1433`; `LogicalSPDCacheTransport.cc:663-721,904-936`;
  `LogicalSPDCacheSlice.hh:697-755`). Therefore it would be inaccurate to say
  that backing-memory destination writes are wholly untimed. What is bypassed
  is local result production and SPD-to-packet bandwidth.
- CPU completion is scheduled one MAA cycle after all four page writebacks are
  acknowledged (`MAA.cc:1447-1464`), which is a usable ordering boundary.
- The current stats window also includes the guest's full source/destination
  validation and hash loop: `m5_dump_stats` occurs after lines 90-102
  (`test_logical_spd_cache_live.cpp:90-104`). Even after fixing mechanism
  timing, that placement would contaminate an operation-latency comparison.

## 2. Reuse, do not invent

Reuse these existing paths:

- **Transport below the port boundary:** `LogicalSPDCacheTransport` already has fixed
  64-byte lines, eight records/FIFO entries, four response credits, exact route
  tokens, retry states, issued/acked bitsets, and final completion identities
  (`LogicalSPDCacheTransport.hh:17-33,50-66,245-290`). Keep its fixed records,
  exact `sendPrepared` transitions, and response identities after repairing the
  live cache-port adapter.
- **Packet construction and inner response identity:** retain
  `MAA::makeLogicalSPDPacket`, `LogicalSPDSenderState`, Request/route
  incarnation, token epoch/action, address, command, and the Runtime's exact
  precommit completion comparison (`MAA.cc:1320-1433` and
  `LogicalSPDCacheRuntime.hh:430-482,885-909`). These inner identities are
  useful, but they do not by themselves prove that the shared cache port chose
  the correct pending owner.
- **Rejected live retry/provenance boundary:** `CacheSidePort` has one
  `blockReason`. Both downstream `recvReqRetry` and local
  `MAX_XBAR_PACKETS` response-credit release flow through `setUnblocked`, which
  unconditionally calls `notifyLogicalSPDRetry`
  (`CacheSidePort.cc:31-42,86-130`). There is no concrete per-port pending-owner
  arbitration between native and logical service. The retry/response boundary
  must be repaired and behaviorally reviewed before any timing reuse claim.
- **Rejected topology assumption:** Transport hard-codes four callback ports
  and hashes them with `(address >> 6) & 3`
  (`LogicalSPDCacheTransport.cc:168-182`), while MAA indexes the constructed
  cache-side-port vector. Admission currently has no fail-closed proof that
  those domains are exactly the same four live ports.
- **ALU semantics and bandwidth:** reuse the native ALU's FP64 scalar operation
  semantics and its finite formula: cache-line-granular SPD reads/writes plus
  `ceil(elements / num_ALU_lanes) * ALU_lane_latency`
  (`ALU.cc:34-61,738-812`). The logical page must occupy the same per-MAA ALU
  busy owner as a native instruction; it must not create a second ALU.
- **SPD contention:** reuse the actual `SPD` read/write `busy_until` port pools
  (`SPD.cc:29-75`) and configured `spd_read_latency`, `spd_write_latency`, port
  counts, ALU lanes, and ALU lane latency (`MAA.py:141-151`). Add only payload-
  agnostic reservation entry points; native wrappers keep their wakeup/status
  behavior unchanged.
- **Lifecycle and completion:** keep the Bridge callback token, one active
  execution per MAA, operation/page serials, final `WriteResp` authority, and
  fail-closed reset.

Do **not** route Serial4K through `TransparentSPDController` unchanged. Its
compute micro-op uses distinct visible input and output tiles
(`MAA.cc:1026-1080`), and its contract reserves two 4K payloads
(`TransparentSPDController.hh:10-18`). The logical Serial4K contract is one
32 KiB in-place slot. Mirroring the Runtime payload into visible SPD would
temporarily create two payload owners and break the storage/iso-area claim.

## 3. Minimum Serial4K state and event sequence

Precondition: Stage A below has repaired per-cache-port request ownership,
separated downstream retry permits from local capacity releases, added exact
four-port admission checks, refreshed the Python contract, and received an
independent APPROVE. Native MAA quiescence narrows the timing experiment but is
not a substitute for these correctness properties.

### Fixed state

Keep the existing Slice/Transport authority. Add to each
`LogicalSPDExecution` only:

- phase: `Idle`, `FillIssue`, `FillCommitWait`, `ComputeWait`, `ComputeActive`,
  `WriteReadWait`, `WriteIssue`, `FinalResponse`;
- one page index (0..3), one next-issue tick, and one phase-start tick;
- a fixed array of four pending fill-delivery tickets/ready ticks, matching the
  existing four response credits (no vector/map);
- one ALU ownership token and completion tick;
- the existing single retry Packet/port/permit; and
- fixed blocker accounting state.

`Tick`, `EventFunctionWrapper`, Packet pointers, and callback objects are
simulator scheduling/ownership containers, not additional hardware registers.
The modeled hardware information is the bounded phase/page identities,
transport records/credits/line buffers, one ALU owner, and existing SPD port
occupancy.

### Ordered sequence

1. **Admit.** Fail closed unless the live topology has exactly
   `LogicalSPDCacheTransport::PortCount == 4` usable cache-side ports and every
   callback/routing result is in range. Authenticate the held CPU instruction,
   capture scalar and exact source/destination spans, allocate the existing
   callback token, and enter `FillIssue(page=0)`. Require one MAA and native MAA
   quiescence for v1 even after the port repair.
2. **SE lookup.** Before each line request, spend one fixed, positive lookup
   cycle, revalidate range/owner/mapping, then construct the Packet. FS, faults,
   remapping, migration, and delayed walker callbacks remain unsupported and
   fail closed.
3. **Fill issue.** Start the existing fill PageAction (512 lines). Attempt at
   most **one new cache request per MAA cycle** through the repaired per-port
   owner arbiter. This intentionally conservative throttle is fixed, not a
   performance knob. If no Transport credit/record or local XBAR response
   credit is available, sleep until the corresponding local capacity-release
   notification; this is not `recvReqRetry`. If downstream `sendTimingReq`
   refuses the send, latch logical service as that port's exact pending owner,
   retain the exact Packet, and wait only for that port's downstream
   `recvReqRetry`. Do not advance the line or issue another request.
4. **Fill response/commit.** A matching `ReadResp` copies its 64-byte payload
   only into the existing credit line buffer and leaves the record in
   `Delivering`. Reserve one shared SPD write-port access. At its ready event,
   call `commitDelivery(ticket)` to copy the buffered line into the private
   slot, release the response credit, and possibly authenticate fill completion.
   The gem5 Packet may be deleted after the line-buffer copy because the fixed
   Transport record owns the response data until commit.
5. **Compute.** Only after all 512 fill commits, call the already-separated
   `beginCompute`. Acquire the existing per-MAA ALU owner and reserve shared SPD
   read/write ports for 512 FP64 cache-line accesses plus
   `ceil(4096 / num_ALU_lanes) * ALU_lane_latency`. Schedule completion at the
   maximum resource-ready tick, exactly as native `ALUUnit` does. At that event,
   run `executeCompute` once; the host loop is now a functional update at a
   modeled completion boundary, not a zero-time timing claim.
6. **Writeback read/snapshot.** After compute completion, start the existing
   writeback PageAction (512 lines). For one next line, reserve a shared SPD read
   port; only at its ready event may `prepare` snapshot the 64 bytes and form a
   `WriteReq`. Use the same repaired per-port owner arbitration,
   one-new-request-per-cycle throttle, credit limits, false-send retention, and
   distinct local-capacity/downstream-retry sequences as fill.
7. **Write response.** A matching `WriteResp` releases its exact record. The
   page cannot publish until all 512 line responses have matched the accepted
   action identity. No posted-write shortcut is allowed.
8. **Reuse/order.** Only after page `p` writeback completion may the sole slot
   become page `p+1` and return to FillIssue. Page order is 0,1,2,3; there is no
   Serial4K fill/compute/writeback overlap.
9. **Complete.** After page 3's final `WriteResp`, retire/reset the Runtime, then
   return the held CPU response one MAA cycle later. Move `m5_work_end` and an
   operation-only dump/reset to immediately after the blocking logical call
   returns; the runner must select that dump, and hash/readback occurs after the
   timed ROI.

The event handler must do one state transition or one issue attempt per wakeup,
never loop through a page at one tick. Response callbacks may wake it early but
must not steal phase ownership.

## 4. PingPong2K later, at iso-area

Both modes own exactly 32,768 private payload bytes:

| Mode | Physical payload | Logical pages | Opportunity |
|---|---:|---:|---|
| Serial4K | 1 x 4096 FP64 = 32 KiB | 4 x 4K | strictly fill -> in-place compute -> writeback |
| PingPong2K | 2 x 2048 FP64 = 32 KiB | 8 x 2K | after compute frees the source half, fill page `p+1` there while the result half writes page `p` |

PingPong2K keeps one ALU, the same SPD ports, cache ports, eight Transport
records, four response credits, line size, issue throttle, total elements, and
total backing traffic. It adds two independently tagged 2K slot owners and two
simultaneous memory-action contexts: at most one fill and one writeback, sharing
the existing record/credit pool fairly. Compute remains single-issue and waits
until the next source fill and prior result writeback have both completed.
Results retire in page order even if line responses reorder.

Merely selecting the current `PingPong2K` Runtime is not sufficient evidence:
the current Slice advances one page through fill, compute, and writeback before
the next page (`LogicalSPDCacheSlice.hh:722-755`), while Transport owns only one
active `PageAction` (`LogicalSPDCacheTransport.hh:61-66` and
`LogicalSPDCacheTransport.cc:260-335`). Without a second bounded action context,
2K only doubles page transitions; it does not create useful overlap.

## 5. Required instrumentation and matched experiment

Add gem5 Scalars with the `logical_spd_` prefix. All are resettable stats, not
hardware storage:

- `ops_admitted`, `ops_completed`, `pages_completed`;
- `fill_read_reqs`, `fill_read_resps`, `fill_bytes`;
- `write_reqs`, `write_resps`, `write_bytes`;
- per service class and cache port: `send_attempts`, `send_accepted`,
  `send_refused`, `downstream_retry_callbacks`, `retry_reissues`,
  `retry_accepts`, `local_capacity_blocks`, and `local_capacity_releases`;
- `pending_owner_conflicts`, `unrelated_responses`, and
  `response_owner_mismatches` (all must be zero in accepted runs);
- `no_record_events`, `no_credit_events`, `inflight_high_water`,
  `delivery_high_water`;
- `fill_commits`, `compute_pages`, `compute_elements`, `compute_lane_batches`;
- exclusive ticks in `fill_issue`, `fill_response_wait`, `fill_local_commit`,
  `compute_resource_wait`, `compute_active`, `write_local_read`,
  `write_issue`, `write_response_wait`, `retry_wait`, and `complete`; and
- `memory_overlap_ticks` (must be zero for Serial4K; meaningful only after the
  two-action PingPong2K extension).

Emit one schema-versioned trace record at operation admit/complete and at each
page's fill-start/fill-complete, compute-start/compute-complete, write-start/
write-complete. Include operation identity, page, slot, action/serial, first and
last tick, line counts, bytes, retry counts, and final completion identity.

Serial4K acceptance invariants for this smoke are exact:

- 1 admit/complete, 4 page completions, 16,384 compute elements;
- 2,048 read requests/responses and 131,072 fill bytes;
- 2,048 write requests/responses and 131,072 write bytes;
- 1,024 ALU lane batches at 16 lanes (parameterize the assertion if lanes
  change);
- no outstanding record, retry Packet, delivery ticket, event, ALU owner, or
  callback owner at completion; and
- positive compute/local-transfer ticks with exclusive phase ticks summing to
  operation elapsed ticks.

### Experiment

The Serial4K vertical slice first gets an A/A determinism and conservation run,
not a speedup comparison: identical checkpoint restored at least twice, exact
hash, exact traffic/counter invariants, identical phase totals, and no fatal
markers. This run is prohibited until the Stage A port repair has an independent
APPROVE.

The later attributable A/B is `Serial4K` versus `PingPong2K` at the same commit
and with the gem5 mode parameter as the only resolved difference. Use the same
checkpoint and one mode-independent binary (remove the current compile-time
`LOGICAL_SPD_CACHE_MODE` reporting dependency); 16K FP64 multiply;
source/destination virtual addresses and contents;
one MAA/four cores; O3 CPU; clocks; cache geometry/state; Ramulator config;
32 KiB private payload; visible SPD allocation; four cache ports; eight records;
four credits; one line/cycle throttle; ALU lanes/latency; SPD ports/latencies;
stats ROI; and final-`WriteResp` completion. Other cores and native MAA work stay
idle. Run three restores per arm and require bit-identical stats or explain any
non-determinism before comparing.

The only permitted traffic equality is 2,048 reads + 2,048 writes and 128 KiB in
each direction per arm. Attribute a timing delta to scheduling only when hashes,
traffic, cache/Ramulator request counts, ALU work, and all non-mode parameters
match. The claimed delta must reconcile as:

`elapsed delta = sum(exclusive phase deltas) - memory_overlap_ticks`.

Do not compare against the current zero-time loop, `native16`, or the existing
transparent two-4K-payload path as performance baselines.

## 6. Storage ledger and supported scope

Per MAA at this commit:

| Item | Bytes | Interpretation |
|---|---:|---|
| Private FP64 payload | 32,768 | bounded hardware state; 1x4K or iso-area 2x2K |
| Packed semantic metadata lower bound | 1,309 | bounded hardware-information lower bound, including fixed Transport records/credits and 4x64-byte line buffers; not synthesized area |
| Private subtotal | 34,077 | payload + packed semantic lower bound |
| Ordinary visible SPD | additive | unchanged `num_tiles * physical_tile_elements * 4`; the one-MAA/four-core smoke geometry is 524,288 bytes |

The 1,309-byte figure excludes `LogicalSPDCacheSlice::Counters` by construction
(`LogicalSPDCacheRuntime.hh:38-46,141-150`) and includes one 32 KiB payload
(`LogicalSPDCacheRuntime.hh:248-255`). It is not `sizeof(Runtime)`, allocator
overhead, checkpoint bytes, SRAM macro area, or energy.

Simulator-only containers include `std::unique_ptr`, per-MAA `std::vector`
shells, `Packet`, `RequestPtr`, `SenderState`, callback pointers, gem5 events,
`Tick` ready times, debug strings, and statistics. Fixed arrays inside Runtime
stand for bounded hardware only to the extent enumerated in the packed ledger.
The four 64-byte Transport line buffers are bounded response staging and are
already counted; the planned fill-commit scheduler must reference them, not add
another payload copy. Port `busy_until` times model existing physical ports and
are simulator scheduling state, not a new bank.

Supported v1: X86 SE, one MAA, FP64 scalar add/sub/mul/div/min/max already
accepted by the ABI, one operation, aligned disjoint or exactly in-place 128 KiB
backing spans, pre-materialized source, one Serial4K slot, strict page order,
and quiescent native MAA traffic **after** the accepted Stage A cache-port
repair. Quiescence does not waive response classification, exact pending-owner
arbitration, topology guards, or the behavioral harness. The first live
acceptance should exercise MUL only. Unsupported: FS/IOMMU, page
faults/remapping, producer generation handoff,
indirect/reordered producers, multiple MAAs/contexts/operations, native/logical
concurrency/fairness, checkpoint with live state, chained destinations, partial
pages, arbitrary data types, and timing/area/energy claims.

## 7. Staged implementation, write set, and gates

### Stage A: repair the rejected cache-port boundary; independent review

Write set:

- new `src/mem/MAA/LogicalSPDCachePortArbiter.hh`: fixed per-cache-port pending
  owner (`None`, `Native`, `Logical`), distinct downstream-retry and local-
  capacity permits, and fail-closed transitions;
- `src/mem/MAA/CacheSidePort.cc`: tag every send by service owner, latch only
  the owner whose downstream send was refused, separate `recvReqRetry` from
  response-credit release, and dispatch responses without stealing unrelated
  native/logical ownership;
- `src/mem/MAA/MAA.hh/.cc`: owner-tagged native/logical send interfaces,
  per-port pending state, exact topology/admission guard for four cache ports,
  and fail-closed response/retry routing;
- `src/mem/MAA/LogicalSPDCacheLiveAdapterState.hh` and
  `src/mem/MAA/LogicalSPDCachePortProvenance.hh`: encode the repaired authorities
  without treating a local capacity release as a downstream retry;
- `tests/maa/logical_spd_cache_live_adapter_state_test.cc` and
  `tests/maa/logical_spd_cache_port_provenance_test.cc`;
- new `tests/maa/logical_spd_cache_port_arbiter_test.cc`;
- new `tests/maa/logical_spd_cache_live_adapter_harness_test.cc`: behavioral
  unrelated-response, native/logical shared-service ordering, downstream retry,
  and local credit-release sequences;
- `tests/maa/logical_spd_cache_bridge_lifecycle_test.cc`;
- `experiments/scripts/run_logical_spd_cache_bridge_lifecycle_unit.sh`; and
- `experiments/tests/test_logical_spd_cache_bridge_lifecycle_contract.py`:
  replace the stale admission-closure assertions with the repaired topology,
  owner, retry, response, and drain contract.

No timing-state, ALU/SPD timing, benchmark, or gem5-run change belongs in this
stage. GO only when warning/sanitizer unit gates and the behavioral harness pass,
the admission guard rejects non-four-port topology, both response classes remain
owned under adversarial ordering, local credit release cannot manufacture a
downstream retry permit, the refreshed Python contract passes, and a fresh
independent review returns APPROVE. STOP on REJECT or any ambiguous pending
owner. Stage B must not begin while this gate is open.

### Stage B: split transitions and unit-test the scheduler

Write set:

- `src/mem/MAA/LogicalSPDCacheGem5Bridge.hh/.cc`: return
  `DeliveryPending`/ticket instead of auto-committing; expose authenticated
  `commitDelivery`, `beginCompute`, and `executeCompute` calls.
- `src/mem/MAA/LogicalSPDCacheRuntime.hh`: reuse its existing split
  begin/execute methods; no payload or controller redesign.
- new `src/mem/MAA/LogicalSPDCacheTimingState.hh`: fixed phase, four delivery
  slots, page and retry/ALU ownership state only.
- new `tests/maa/logical_spd_cache_timing_state_test.cc` and
  `experiments/scripts/run_logical_spd_cache_timing_unit.sh`.

GO only if adversarial unit tests cover out-of-order responses, full credits,
wrong-port retry, duplicate/stale tickets, retry-before-permit, event ordering,
final response ordering, and drain rejection, with no dynamic queue. STOP if a
response must retain a gem5 Packet beyond callback or requires a fifth line
buffer.

### Stage C: share real resources and integrate Serial4K

Write set:

- `src/mem/MAA/SPD.hh/.cc`: payload-agnostic read/write port reservation helpers;
  native accessors remain behavior-identical.
- `src/mem/MAA/ALU.hh/.cc`: one logical-page owner using the existing ALU event,
  FP64 scalar semantics, lane latency, and shared SPD reservations; it is
  mutually exclusive with native ALU ownership.
- `src/mem/MAA/MAA.hh/.cc`: fixed timing state/event service, one-line/cycle
  issue, SE lookup delay, blocker accounting, stats, final ordering, and native
  quiescence guard.
- new `tests/maa/logical_spd_cache_alu_timing_test.cc`: native/logical ALU
  exclusion, shared SPD-port contention, and exact completion-boundary tests.

GO after the Stage A independent APPROVE, warning-clean unit builds, and existing
logical SPD, ALU, SPD, transparent-controller, port-provenance, retry, abort,
drain, and sanitizer gates pass. STOP if logical compute can overlap a native
ALU owner, any local copy occurs before its ready tick, `serviceLogicalSPD`
issues more than one new line in a cycle, or a refused Packet is recreated.

### Stage D: repair the live ROI and run Serial4K gem5

Write set:

- `benchmarks/API/test_logical_spd_cache_live.cpp`: make the workload binary
  mode-independent; end and dump/reset the timed ROI immediately after the
  blocking operation, then validate outside it.
- `experiments/scripts/run_logical_spd_cache_live_smoke.sh`: require exact
  counters, hashes, resolved geometry, clean source, and repeated A/A results.
- new `experiments/tests/test_logical_spd_cache_timing_contract.py`: source-level
  gates for finite issue, split delivery, resource ownership, ROI placement, and
  exact counter checks.

GO only after the Stage A independent APPROVE and on exact counts above,
positive modeled phase time, zero Serial overlap,
final-`WriteResp` completion, clean drain, identical repeated restores, and no
fatal/error markers. STOP on traffic mismatch, validation inside ROI, a
zero-time page transform, unowned response/retry, or any comparative speed
claim.

### Stage E: PingPong2K, separately reviewed

Only after Stage D, extend the fixed Slice/Transport authority to two concurrent
memory actions sharing the same records/credits, add 2K slot-owner/ordering
tests, and run the matched A/B above. Write set:

- `src/mem/MAA/LogicalSPDCacheController.hh`;
- `src/mem/MAA/LogicalSPDCacheSlice.hh`;
- `src/mem/MAA/LogicalSPDCacheTransport.hh/.cc`;
- `src/mem/MAA/LogicalSPDCacheRuntime.hh`;
- `tests/maa/logical_spd_cache_controller_test.cc`;
- `tests/maa/logical_spd_cache_transport_test.cc`;
- `tests/maa/logical_spd_cache_vertical_slice_test.cc`;
- `experiments/scripts/run_logical_spd_cache_live_smoke.sh`; and
- new `experiments/analysis/analyze_logical_spd_timing_pair.py` with
  `experiments/tests/test_analyze_logical_spd_timing_pair.py`.

No payload byte, port, credit, ALU, or issue-width parameter may change.

GO for a scheduling claim only if the complete matched matrix and phase
reconciliation pass. STOP if PingPong adds a third slot, extra records/credits,
more cache ports, a wider throttle, relaxed completion, or still has only one
active memory action.

## Principal blockers

The immediate blocker is functional, not timing. At `7979772`, downstream
`recvReqRetry` and local `MAX_XBAR_PACKETS` response-credit release share
`setUnblocked` authority; no per-cache-port pending owner arbitrates native and
logical service; the live tests do not behaviorally inject unrelated responses
and shared-service reorderings; the four-port address hash has no fail-closed
admission guard; and the admission-closure Python contract is stale. The third
independent review therefore REJECTS this base. Native quiescence is not a
repair, because a timing implementation would otherwise be built on an
unaccepted response/retry ownership boundary.

The logical Runtime's private payload is not addressable by native `ALUUnit` or
`SPD`: native ALU reads/writes visible tile IDs, while Runtime owns a separate
private `std::array`. Reusing native `ALUUnit` unchanged would require a second
visible 4K destination or a mirror copy, violating one-slot Serial4K ownership.
The required payload-agnostic ALU/SPD reservation adapter is small but real; it
touches shared resource code and therefore needs contention tests. If that
adapter cannot be made mutually exclusive with native ALU ownership without
changing IF retirement, the vertical slice is larger than an hours-scale patch
and must STOP rather than introduce a magic fixed-delay datapath.

After Stage A repair, requiring native MAA quiescence still keeps the first
timing experiment narrow and avoids a concurrency-performance claim. Supporting
performance under concurrent native traffic requires a separately reviewed
fairness policy and is not part of Serial4K v1, but exact functional ownership
for both service classes is non-optional in Stage A.
