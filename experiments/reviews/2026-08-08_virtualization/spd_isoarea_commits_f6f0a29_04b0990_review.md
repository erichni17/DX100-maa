# Independent review: iso-area logical SPD cache

Date: 2026-08-08  
Reviewed commits: `f6f0a295cb00a562320b756815e092112fe6a26a`,
`04b0990a9e4516f9eb03b16e43fa831ba36f921c`  
Reviewed branch HEAD: `0d1d3ee395bf0e2eb4733a3e20aba51defe17b47`  
Intended payload contract: exactly 32 KiB of private FP64 data per MAA,
exposed as one in-place 4096-element slot in Serial4K or two disjoint
2048-element slots in PingPong2K.

## Verdict and findings

**Reject for cherry-pick.** The two requested commits are not safe to
cherry-pick before the small `0d1d3ee` correction: Serial4K necessarily reaches
the datapath with exact source/destination alias and 4096 elements, while the
committed two-commit state rejects every alias and caps the span at 2048
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:514-555`, corrected at
`src/mem/MAA/LogicalSPDCacheDatapath.hh:14-15,61-83`). The worker's diagnosis
of those two datapath defects, and of the test's stale pre-NaN snapshot, is
correct but not complete. Even with `0d1d3ee`, the integration/evidence blockers
below remain.

### High — The committed live runner cannot reach the operation and describes the wrong mode

The runner requests `physical_tile_elements=2048`
(`experiments/scripts/run_logical_spd_cache_live_smoke.sh:48-50`), while live
admission unconditionally requires `Slice::SerialPageElements == 4096`
(`src/mem/MAA/MAA.cc:1202-1206`). The run therefore panics before admission.
Independently, the runner leaves `transparent_spd_mode` at its default zero,
which selects Serial4K (`src/mem/MAA/MAA.py:23-26`,
`src/mem/MAA/MAA.cc:243-248`), but the benchmark and manifest declare eight
2048-element pages and two 16-KiB slots
(`benchmarks/API/test_logical_spd_cache_live.cpp:18-21,61-66`,
`experiments/scripts/run_logical_spd_cache_live_smoke.sh:54-59`). Thus even a
geometry-only runner edit would leave the evidence mislabeled.

Impact: the committed live smoke is not evidence for either advertised arm,
and there is no committed exact-output gem5 run for Serial4K or PingPong2K at
this HEAD.

### High — The 32-KiB private payload is additive to ordinary visible SPD, and the live hardware accounting is false

The private runtime array is correctly fixed at 4096 doubles / 32768 bytes
(`src/mem/MAA/LogicalSPDCacheRuntime.hh:951-955`,
`src/mem/MAA/LogicalSPDCacheSlice.hh:28-35`). Serial4K exposes that bank as one
32-KiB span; PingPong2K exposes it as two adjacent 16-KiB spans. No second
private payload bank or unbounded payload container was found.

However, MAA still constructs the ordinary visible `SPD` independently
(`src/mem/MAA/MAA.cc:236-248`), and `SPD` still allocates
`visible_tile_count * physical_tile_elements * 4` payload bytes plus visible
metadata (`src/mem/MAA/SPD.cc:238-293`). Nothing in these commits replaces or
aliases that allocation with the private bank. At the accepted 4K visible
configuration with 32 lane tiles, that is 512 KiB visible payload plus 32 KiB
private payload per one-MAA system, before control metadata. The accounting
script itself adds visible and private payloads
(`experiments/analysis/spd_hardware_accounting.py:135-169`).

Nevertheless, the live benchmark and manifest print
`hardware_bytes=32768 metadata_bytes=0`
(`benchmarks/API/test_logical_spd_cache_live.cpp:61-66`,
`experiments/scripts/run_logical_spd_cache_live_smoke.sh:54-59`). That
contradicts both the retained visible allocation and the runtime's own
34,077-byte packed semantic lower bound, of which 32,768 bytes are payload and
1,309 bytes are metadata (`src/mem/MAA/LogicalSPDCacheRuntime.hh:249-267`). The
Python ledger also contains stale prose saying "two ... slots x 4096 elements"
while its arithmetic uses two times 2048
(`experiments/analysis/spd_hardware_accounting.py:65-67,139-143,207-214`).

Impact: 32 KiB is proven only as the private FP64 payload capacity. These
commits do not prove a 32-KiB total hardware cache or an iso-area total design.

### High — Live drain/checkpoint lifecycle is explicitly not integrated

The standalone runtime has bounded abort/drain/reset guards, but the bridge
explicitly reports `admissionClosed() == false` and
`nativeDrainIntegrated() == false`
(`src/mem/MAA/LogicalSPDCacheGem5Bridge.hh:74-76`). MAA owns live callback
tokens, external response-owned packets, retry packets, a completion packet,
and a scheduled service event (`src/mem/MAA/MAA.hh:532-573`), yet MAA supplies
no drain, serialize, or unserialize handling for this state. A checkpoint
requested with a fill, refused retry, delivery, compute, or writeback active is
therefore neither held until quiescence nor reconstructible. The existing live
benchmark checkpoints before MAA registration and admission
(`benchmarks/API/test_logical_spd_cache_live.cpp:68-82`), so it does not exercise
this hazard.

Impact: checkpointing or simulator teardown during a live logical operation can
lose external ownership/correlation or terminate on the bridge/runtime
destruction guards. This blocks production lifecycle acceptance.

### Medium — The logical mode is silently coupled to a three-valued transparent-controller mode

The public knob defines `0=Serial4K, 1=Serial2K, 2=PingPong2K`
(`src/mem/MAA/MAA.py:23-26`, `configs/common/Options.py:235-240`), but the new
logical-cache mapping treats zero as Serial4K and every nonzero value as
PingPong2K (`src/mem/MAA/MAA.cc:243-248`). A legal mode-1 configuration thus
silently runs a different logical-cache mechanism. The logical cache has no
Serial2K mode, so mode 1 must be rejected for logical descriptors or the
logical cache must receive a separate, two-valued configuration parameter.

### Medium — The live path self-reports callback ports instead of authenticating the gem5 port that fired

Transport correctly binds each line to a port and rejects a wrong callback
port (`src/mem/MAA/LogicalSPDCacheTransport.cc:603-625,643-679`). The gem5
adapter, however, stores the transport-selected port in sender state and passes
that stored value back on response; `CacheSidePort::recvTimingResp` does not
pass its actual `core_id` (`src/mem/MAA/MAA.cc:1356-1369,1375-1411`,
`src/mem/MAA/CacheSidePort.cc:30-41`). Retry notification is likewise global:
an unblock on any cache port schedules every logical retry, after which each
execution calls `recvReqRetry` with its stored expected port
(`src/mem/MAA/CacheSidePort.cc:124-130`, `src/mem/MAA/MAA.cc:1452-1469,1527-1534`).

Impact: the standalone wrong-port tests do not prove the live adapter's port
provenance, and unrelated port retry events can cause premature retry probes.
Token epoch/action/request identities still bound stale and duplicate line
responses; no unbounded response state was found.

### Medium — The live scalar operation proves backing-memory transformation, not an indirect producer/reorder path

The benchmark initializes all source values before its checkpoint and later
passes that materialized array directly to the logical scalar instruction
(`benchmarks/API/test_logical_spd_cache_live.cpp:51-58,68-82`). Admission calls
`registerSource` on that address (`src/mem/MAA/MAA.cc:1251-1276`), and
`registerSource` immediately marks every source page ready and invents a local
producer transaction (`src/mem/MAA/LogicalSPDCacheSlice.hh:455-503`). Timed
reads then materialize pages from backing memory. No indirect unit publishes a
generation, no producer retirement writes are correlated, and no reorder path
is exercised. The trace's `reorder_contract=producer_supplied`
(`src/mem/MAA/MAA.cc:1286-1303`) is therefore not supported by this live path.

Impact: exact output here can validate fill/transform/writeback behavior only;
it cannot validate producer handoff, reorder survival, stale producer
publication, or producer/consumer concurrency.

### Medium — Functional timing responses do not constitute compute-timing or area evidence

The fill/writeback packets use gem5 timing requests and response-bearing writes,
which is useful transport evidence. The FP64 transform itself executes as a
host loop when `NoWork` is observed
(`src/mem/MAA/MAA.cc:1494-1508`,
`src/mem/MAA/LogicalSPDCacheRuntime.hh:504-560`), and the service event defaults
to zero modeled cycles (`src/mem/MAA/MAA.hh:571`,
`src/mem/MAA/MAA.cc:1517-1523`). It does not reserve the MAA ALU, charge lane
latency, contend for compute resources, or model a synthesized cache lookup.
The committed benchmark does print `isoarea_timing_claim=0`, which is the
correct limitation; no `simTicks` speedup or area conclusion may be promoted
from this implementation.

### Low — The follow-up mode test is too weak to be the acceptance test

`0d1d3ee` tests Serial4K with an in-place Add of +0 and checks only the last
element; PingPong2K checks geometry but not output
(`tests/maa/logical_spd_hidden_payload_test.cc:71-88`). The vertical runtime
test covers a full nontrivial PingPong2K operation, but not Serial4K. This did
not conceal another controller/datapath defect in the temporary full-operation
probe described below, but both explicit arms need a committed regression.

## Functional proof obtained

The following clean-HEAD focused gates passed:

- `experiments/scripts/run_logical_spd_cache_controller_unit.sh`: optimized,
  ASan/UBSan, and 12 Python contract tests passed.
- `experiments/scripts/run_logical_spd_hidden_payload_unit.sh`: hidden-payload,
  transport, and vertical-slice tests passed in optimized and ASan/UBSan
  builds; 14 Python contract/accounting tests passed. Reported private payload
  was 32 KiB, packed semantic lower bound 34,077 bytes, and host runtime size
  35,328 bytes.
- `experiments/scripts/run_logical_spd_cache_bridge_lifecycle_unit.sh`:
  optimized, ASan/UBSan, and seven Python contract tests passed.
- `experiments/scripts/run_logical_spd_cache_abi_unit.sh`: C++ ABI test and 19
  Python ABI/transparent-controller tests passed.
- `git diff --check f6f0a29^..04b0990`: passed.

A temporary, uncommitted C++ probe drove the complete authenticated runtime
sequence (fill, nontrivial multiply by -3.25, response-acknowledged writeback)
over all 16K FP64 elements for each explicit mode. Both `Serial4K` and
`PingPong2K` matched every output bit exactly. This establishes that the two
`0d1d3ee` datapath corrections are sufficient for the standalone functional
mode paths tested. It does not cure or test the live integration, drain,
producer, timing, or area findings above.

The live runner was not presented as a passed gate: source inspection proves
its 2048-versus-4096 admission contradiction, and the available binary was not
claimed as a clean rebuild of this source HEAD.

## Exact acceptance gates

1. Land the exact-alias/partial-overlap and 4096-element datapath correction
   from `0d1d3ee`, plus committed full 16K exact-output runtime tests for both
   Serial4K and PingPong2K using a nontrivial scalar and guard regions.
2. Give the logical cache an explicit two-valued mode contract, or reject
   logical descriptors when the shared transparent mode is Serial2K. Add a
   negative mode-1 test and assert runtime mode/page/slot geometry in each live
   manifest.
3. Make admission geometry and both live runners agree. Run separate clean,
   fresh-gem5 Serial4K (4 x 4096, one slot) and PingPong2K (8 x 2048, two slots)
   cases with exact 16K output hashes, one terminal marker, final nonempty
   stats, resolved config, source commit, binary hash, and raw artifact paths.
4. Integrate MAA drain with admission closure and exact external packet/retry
   ownership. Either serialize all logical runtime/correlation/event state or
   forbid checkpoint until every runtime is quiescent. Test checkpoint requests
   during fill, refused retry, delivery, compute, dirty writeback, and abort
   drain, plus restore or explicit deferral.
5. Pass the actual responding/retrying cache-port identity into the adapter;
   keep wrong-port, stale generation, duplicate, cross-line, and post-reset
   callbacks fail-closed and non-mutating. Add live multi-port retry/reorder
   tests rather than relying only on the mock peer.
6. If producer/reorder behavior is claimed, replace immediate all-pages-ready
   registration with an authenticated indirect-producer generation/transaction
   handoff and test late, duplicate, wrong-generation, and out-of-order page
   publication. Otherwise label the live test explicitly as a pre-materialized
   backing transform with no producer/reorder evidence.
7. Correct all accounting labels and prose. Report exactly 32,768 private
   payload bytes per MAA, the 1,309-byte packed metadata lower bound separately,
   ordinary visible SPD payload as additive, and host/runtime allocation
   caveats. Remove `hardware_bytes=32768 metadata_bytes=0`; make no total-area
   or iso-area claim without an explicit replacement boundary and synthesis or
   a defensible hardware-cost model.
8. Charge scalar compute and cache-control timing through modeled resources
   before any performance claim. Until then, preserve
   `isoarea_timing_claim=0` and do not use `simTicks` to claim speedup,
   throughput, overlap, or timing closure.
9. Re-run all four focused gates above under optimized and sanitizer builds,
   then run the two live cases from a clean source tree and a binary whose hash
   is bound to that exact source commit. Correctness must precede reading any
   performance metric.

## Handoff

Do not cherry-pick `f6f0a29` and `04b0990` alone. `0d1d3ee` repairs the
standalone Serial4K datapath blocker, but the series still requires the live
geometry/mode, additive accounting, drain/checkpoint, port-provenance, and
evidence-boundary gates above before acceptance. The safe current claim is:
bounded standalone controller/transport functionality with exactly 32 KiB of
private FP64 payload per MAA and exact tested outputs in both modes; no accepted
live gem5 result, producer/reorder proof, total-area proof, or compute-timing
result.
