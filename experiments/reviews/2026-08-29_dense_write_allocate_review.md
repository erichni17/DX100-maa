# Dense backing no-read allocation: independent correctness review

**Disposition: reject for promotion until the coherence/corruption bug is fixed and adversarially tested.** The accepted raw micro pair is intact and does demonstrate a simulator timing change under its exact configuration. It does *not* establish that the mechanism is legal for coherent backing memory, nor does it substantiate an area/timing claim.

## Findings first

### F1 — blocking correctness: a partial first line is written as an unmasked 64 B line, and its non-semantic words are known zero placeholders

`copyLine()` zero-fills the entire 64 B staging buffer and overwrites only its semantic-mask words ([`VirtualCombinePayloadStore.hh:164`](../../src/mem/MAA/VirtualCombinePayloadStore.hh:164)). The partial-victim drain passes that buffer plus the non-full `valid_words` mask to retirement ([`IndirectAccess.cc:10815`](../../src/mem/MAA/IndirectAccess.cc:10815)).

Dense mode turns that exact partial write into `full_line_transport`: it omits `Request::setByteEnable()` whenever the line is not yet initialized ([`DenseBackingLineTracker.hh:62`](../../src/mem/MAA/DenseBackingLineTracker.hh:62), [`IndirectAccess.cc:10210`](../../src/mem/MAA/IndirectAccess.cc:10210), [`IndirectAccess.cc:10223`](../../src/mem/MAA/IndirectAccess.cc:10223)). A new `Request` has every byte enabled by default ([`request.hh:480`](../../src/mem/request.hh:480)); an unmasked packet copies all bytes to the cache block ([`packet.hh:1295`](../../src/mem/packet.hh:1295)). Therefore the invalid words are concretely written as zero, not merely unmodelled/unknown data.

This violates the partial-write contract. Page readiness does not make the intermediate physical line private: it is only marked after the relevant WriteResp accounting ([`IndirectAccess.cc:9975`](../../src/mem/MAA/IndirectAccess.cc:9975), [`IndirectAccess.cc:10125`](../../src/mem/MAA/IndirectAccess.cc:10125)). A different coherent requester can read the cache line after the first write and before later fragments arrive. A dirty eviction can also propagate the zeroed bytes. The exact-output micro does not expose either event because it writes all logical result words before its final check.

The raw dense trace proves this is exercised, not a dead branch: all 2,048 `dense_initialize=1` events have a non-full semantic mask (for example the first is `valid_words=0xf0`). No event has `valid_words=0xff`.

### F2 — semantic and transport masks are tracked separately internally, but the transport transformation is not legal

The scoreboard retains `valid_words` as `backingWordMask` and uses it for the line-ready callback ([`IndirectAccess.cc:10103`](../../src/mem/MAA/IndirectAccess.cc:10103), [`IndirectAccess.cc:10158`](../../src/mem/MAA/IndirectAccess.cc:10158)); semantic-byte accounting also continues to count only set mask bits ([`IndirectAccess.cc:10264`](../../src/mem/MAA/IndirectAccess.cc:10264)). That is the right *metadata* separation. It is not a correct separation at the coherent transport boundary: the same partial payload is sent with all byte enables true. The semantic mask cannot undo bytes already installed in a cache.

The tested pair has `direct_retirement_line_handoff=false`, so it also does not test the handoff path. That path does at least reject a partial mask as a full authoritative payload ([`MAA.cc:6666`](../../src/mem/MAA/MAA.cc:6666)), but it does not repair the backing-memory corruption.

### F3 — exact ACK identity/reuse looks fail-closed in the reviewed path, but the lifecycle edge is untested

This is a positive finding, not a promotion waiver. The retirement scoreboard does not reset while nonempty, carries address + page generation + a monotonic non-recycled transaction, and rejects a mismatched response ([`VirtualRetirementScoreboard.hh:74`](../../src/mem/MAA/VirtualRetirementScoreboard.hh:74), [`VirtualRetirementScoreboard.hh:86`](../../src/mem/MAA/VirtualRetirementScoreboard.hh:86), [`VirtualRetirementScoreboard.hh:127`](../../src/mem/MAA/VirtualRetirementScoreboard.hh:127)). The response path verifies the sender identity before completing it ([`IndirectAccess.cc:11519`](../../src/mem/MAA/IndirectAccess.cc:11519)).

Commit `e16aff43` correctly moved the dense-tracker reset to `initializeVirtualPageTracking()` after range/page decoding, and explicitly initializes tracking before the first retirement write. That removes the pre-decode `my_max`/backing-range ordering error in `5bdfc682`. Normal reuse is guarded indirectly because the scoreboard reset fails if any ACK remains ([`IndirectAccess.cc:6177`](../../src/mem/MAA/IndirectAccess.cc:6177)).

However, there is no integration test that delays a WriteResp across an operation-reuse attempt, forces the same physical line/key to be reused, and proves both page generation and tracker state remain isolated. The current unit test only calls a standalone bitmap's `reset()`/`acknowledge()` and boolean helper ([`dense_backing_line_tracker_test.cc:26`](../../tests/maa/dense_backing_line_tracker_test.cc:26)).

### F4 — 256 B is only the bitmap at the exercised 16K × 8 B point, not a complete hardware/area or timing account

For this micro, `16,384 * 8 / 64 = 2,048` lines, so a 2,048-bit bitmap is indeed 256 B. The implementation contains exactly a fixed 32×64-bit bit array ([`DenseBackingLineTracker.hh:19`](../../src/mem/MAA/DenseBackingLineTracker.hh:19), [`DenseBackingLineTracker.hh:78`](../../src/mem/MAA/DenseBackingLineTracker.hh:78)). The report adds only `ceil(logical * word_bytes / 64)` bits to control state ([`report_maa_storage.py:523`](../scripts/report_maa_storage.py:523)) and the test verifies only a 256-B delta ([`test_report_maa_storage.py:422`](../tests/test_report_maa_storage.py:422)).

It omits, at minimum, the line-count and initialized-count state, bitmap read/write ports (issue tests initialization; ACK sets it), address/index logic, a 2,048-bit reset/clear mechanism, arbitration with the response path, and their latency/energy. It also reports a configuration-sized bitmap while the implementation instantiates a fixed maximum array. Existing scoreboard state accounts for one live transaction per line, but it does not account for the added bitmap ports or clear timing. Thus 256 B is a narrow lower-bound data-bit delta—not an iso-area, synthesized-area, frequency, port, or timing claim.

## Independent raw-pair reconstruction

Raw root: `/data1/nier/dx100-runs/2026-08-29-hybrid-dense-write-allocate-pair-r3`.

I ran `sha256sum -c artifacts.sha256` from that root: **36/36 artifacts verified**. The ledger ties both arms to gem5 SHA-256 `30c5fd721f5e265b73f43ad052d0e732ca45880ba3240c2e3821cd2a8d3955fd` and source commit `e16aff4306968668b4d5e13136138e19d7cd1661`. Both captured process records have return code 0 and absent registered PID; both logs end in `m5_exit`. They use the same checkpoint, binary, workload, restore number, and all captured config fields except `virtual_dense_write_allocate`.

| Independent check | Control | Dense | Delta / interpretation |
| --- | ---: | ---: | --- |
| Result/output hash | 7228541527853630339 | 7228541527853630339 | Exact output agreement |
| Semantic backing bytes | 131,072 | 131,072 | Equal requested semantic work |
| Strict descriptors / B words / pages ready | 16,384 / 16,384 / 4 | 16,384 / 16,384 / 4 | Equal logical work and completion |
| Retirement issues = completions | 8,668 = 8,668 | 8,659 = 8,659 | Terminal write accounting holds |
| Dense initialization writes | 0 | 2,048 | Mechanism activation exactly equals 16K×8B/64B lines |
| MAA cache reads / writes | 4,097 / 10,716 | 4,097 / 10,707 | 0 / −9 |
| LLC MAA misses / miss latency | 4,097 / 433,536,613 | 2,049 / 321,691,384 | −2,048 / −111,845,229 ticks |
| Ramulator reads | 26,874 | 24,828 | −2,046 (−7.61%) |
| ROI `simTicks` | 56,868,031 | 47,265,504 | dense is 16.8856% lower; control/dense = **1.203161×** |

The measured effect is therefore consistent with avoided read-for-ownership traffic in this simulator configuration. It is one matched micro observation, not a general performance result, and cannot validate an unsafe data transformation or establish hardware cost.

## Commit audit

`5bdfc682` introduced the feature and initially reset its line tracker while instruction setup still preceded the later range/page decode. `e16aff43` fixes that ordering by deferring the reset until page tracking initializes and calling that initializer from `createRetirementWrite()`. The raw pair was compiled from `e16aff43`, not the earlier commit. The ordering correction is necessary, but neither commit addresses F1/F2.

## Required adversarial closure before reconsideration

1. Add a coherent-memory regression with a sentinel-filled backing line, one partial dense first write, a second requester read before the remaining fragments, and a forced dirty eviction. It must demonstrate that non-semantic bytes retain the sentinel; it should fail on the reviewed implementation.
2. Test the same sequence with `direct_retirement_line_handoff=true`, with an inactive consumer/materializer, and with two consumers. Check both physical memory and token/page readiness rather than only the final application checksum.
3. Exercise delayed/out-of-order WriteResp, operation reuse, repeated backing physical keys, the 32-credit boundary, and reset/abort behavior. Assert generation/transaction rejection and no dense-tracker state leaks.
4. Define a legal replacement mechanism: retain byte enables for partial writes, read/merge old bytes, or allocate a private, non-coherent initialization buffer that cannot be observed or evicted until all bytes are authoritative. Then rerun an exact matched pair.
5. Replace the 256-B label with a ported/timed lower-bound resource vector, parameterize or explicitly charge the fixed 2,048-bit implementation, and keep any area/frequency assertion separate until synthesis/RTL evidence exists.

## Validation performed

No production source was changed and no gem5 run was launched for this review. The raw ledger was rehashed; captured commands/configuration, terminal logs, stats, and trace events were independently parsed. Focused non-simulation source tests are recorded with the review checkpoint.
