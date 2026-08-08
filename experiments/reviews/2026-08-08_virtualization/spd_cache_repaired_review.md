# Independent review: repaired logical SPD cache

Date: 2026-08-08
Reviewed range: `4e6539e0^..cf067e72004a9a136f0f559423b9a5c1e62bcc4b`
Reviewed HEAD: `cf067e72004a9a136f0f559423b9a5c1e62bcc4b`

## Verdict and findings

**REJECT the live gem5/cache-port integration and any production,
checkpoint, producer/reorder, timing, performance, total-area, or multi-MAA
claim. ACCEPT only the bounded standalone one-MAA functional scope described
below.**

### High — Retry provenance still becomes sender-state self-attestation

`CacheSidePort::setUnblocked` now passes its actual `core_id` to
`notifyLogicalSPDRetry` (`src/mem/MAA/CacheSidePort.cc:125-130`), but the actual
port identity is not latched as an authority for one execution. The callback
only schedules the shared event (`src/mem/MAA/MAA.cc:1539-1547`). When that
event runs, `serviceLogicalSPD` processes every execution with a retry packet
and calls `recvReqRetry` using that execution's stored `retryPort`, not the port
that fired (`src/mem/MAA/MAA.cc:1440-1468`). A response on any logical line
also schedules the same event (`src/mem/MAA/MAA.cc:1422-1430`), so an unrelated
response can enter this stored-port retry transition; with multiple MAAs, one
port's retry can do so for other executions as well.

The port-provenance test checks only four calls to a pure equality helper
(`tests/maa/logical_spd_cache_port_provenance_test.cc:8-17`). It never drives
`CacheSidePort`, event scheduling, unrelated responses, multiple pending
ports, or `serviceLogicalSPD`. Standalone transport wrong-port tests are
behavioral and fail closed, but they receive the mock's supplied callback port
directly and therefore do not close this live-adapter gap.

Response provenance itself is correctly improved by passing the responding
`CacheSidePort::core_id` through `recvLogicalSPDTimingResp` and into the
transport (`src/mem/MAA/CacheSidePort.cc:31-35`,
`src/mem/MAA/MAA.cc:1383-1423`). That actual path still lacks a behavioral
wrong-port adapter test; the equality-helper test is not evidence that a wrong
live response is non-mutating/fail-stop at the integration boundary.

### High — Native drain, checkpoint, and teardown remain unintegrated

The bridge still explicitly reports `admissionClosed() == false` and
`nativeDrainIntegrated() == false`
(`src/mem/MAA/LogicalSPDCacheGem5Bridge.hh:74-76`). `MAA` has no drain,
serialize, or unserialize override for its callback tokens, response-owned
packets, retry packet, completion packet, or scheduled logical event. Its
destructor deletes cache ports without first closing admission or draining
logical executions (`src/mem/MAA/MAA.cc:371-387`); the bridge destructor then
terminates if an owner/action remains live
(`src/mem/MAA/LogicalSPDCacheGem5Bridge.cc:96-114`). The live benchmark
checkpoints only before logical registration/admission
(`benchmarks/API/test_logical_spd_cache_live.cpp:74-88`), so it cannot test any
of these states.

### Medium — The live producer/reorder label is still false

`registerSource` immediately declares all source pages ready and creates a
local producer transaction (`src/mem/MAA/LogicalSPDCacheSlice.hh:455-503`). The
live benchmark supplies a fully materialized backing array, yet the admit
trace still says `reorder_contract=producer_supplied`
(`src/mem/MAA/MAA.cc:1294-1303`). No indirect producer generation handoff or
reorder survival is exercised. The newer milestone correctly calls the smoke
a pre-materialized transform, but the emitted trace contradicts that scope.

### Low — Full-range whitespace check is not clean

`git diff --check 4e6539e0 cf067e72` passes for the repair commits. Including
the range's first review commit with
`git diff --check 4e6539e0^ cf067e72` reports three pre-existing Markdown
trailing-space lines in
`spd_isoarea_commits_f6f0a29_04b0990_review.md`. No implementation whitespace
error was found.

## Accepted bounded scope

The previous Serial4K functional blocker is repaired and behaviorally tested.
The vertical test drives exact in-place aliasing over all 16K FP64 elements
with scalar `+2.5`, four 4096-element pages, all `4 * 512` fill responses, all
`4 * 512` response-acknowledged writebacks, every output bit, and guard words
on both sides (`tests/maa/logical_spd_cache_vertical_slice_test.cc:88-177`,
`:323-359`, `:1002-1008`). PingPong2K also completes its full 8-page disjoint
transform.

Shifted backing overlap is unreachable after valid equal-size 128-KiB
alignment: two valid distinct bases are disjoint. Admission rejects a shifted
span before mutation, and the separately reachable pointer-level datapath
defense rejects a one-double overlap before mutation
(`src/mem/MAA/LogicalSPDCacheSlice.hh:421-435,519-550`,
`tests/maa/logical_spd_cache_vertical_slice_test.cc:515-535,703-784`).

Stale generation/action/slot/serial, duplicate, wrong-command, wrong-address,
wrong-size, wrong-request, and wrong-port transport responses have behavioral
C++ coverage. Completion requires exact finite issued/ack sets; vertical and
bridge tests count every fill and writeback response. The runtime has exactly
two finite modes and one `std::array<double, 4096>` payload bank (32,768 private
payload bytes) per MAA, with fixed controller/transport records and no second
private payload container. Ordinary visible SPD remains a separate additive
allocation; the corrected ledger reports the 1,309-byte packed metadata lower
bound separately. Acceptance is limited to one MAA because the live runner is
explicitly `--maa_num_maas=1`, no multi-MAA live behavior is established, and
the retry finding is more exposed with multiple executions.

## Validation

- `run_logical_spd_cache_controller_unit.sh`: optimized and ASan/UBSan C++
  passed; 12 Python contracts passed.
- `run_logical_spd_hidden_payload_unit.sh`: hidden-payload, transport, and
  vertical C++ passed optimized and ASan/UBSan; 15 Python contracts passed.
- `run_logical_spd_cache_bridge_lifecycle_unit.sh`: bridge C++ passed optimized
  and ASan/UBSan; provenance helper passed both builds; 7 Python contracts
  passed.
- `run_logical_spd_cache_abi_unit.sh`: C++ ABI test and 19 Python contracts
  passed.
- Repair-only `git diff --check` passed; full reviewed range has only the three
  review-document whitespace diagnostics above.
- Per instruction, gem5 was not run. No committed fresh live Serial4K or
  PingPong2K artifact was treated as evidence.

## Required handoff

Latch the actual retrying cache-port identity (or an execution-specific retry
permit) at the callback boundary and consume only that authority in service;
test wrong-port retry, unrelated-response scheduling, same/different-port
pending retries, and multi-execution isolation through the real adapter. Add a
real wrong-response-port integration test. Separately integrate admission
closure with gem5 drain and either serialize all live state or forbid
checkpoint until quiescent, and remove the producer/reorder trace claim until
an authenticated producer handoff exists.
