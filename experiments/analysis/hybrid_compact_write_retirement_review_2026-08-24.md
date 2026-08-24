# Independent review of compact SoA/JIT A-write retirement

Commit reviewed: `f35f9111ae49d8f6d48ebb715ccaf8ec3f5f3835`

Review date: 2026-08-24

Scope: code review and focused contract/unit tests only; no simulator was launched and no mechanism source was changed.

## Recommendation

**Reject `f35f9111` as an integration/cherry-pick candidate. Fix and re-review.** The compact-enabled path has a confirmed terminal panic before it can publish terminal statistics or produce valid performance evidence. Independently, the claimed 146 persistent bytes/unit and three transient tag bits do not yet form a complete, non-double-counted hardware contract.

## Findings, ordered by severity

### High — the terminal checker finishes the tracker on its first call and rejects it on the second

The SoA/JIT request path calls `checkSoaJitTerminal()` after all work and responses drain, then changes state to `Response` (`src/mem/MAA/IndirectAccess.cc:6623-6669`). The `Response` path calls the same checker again before recording any SoA/JIT statistics (`src/mem/MAA/IndirectAccess.cc:6999-7002`). This was harmless while the checker was observational, but this commit makes it mutate lifecycle state: the first call invokes `soa_jit_write_retirement.finish()` (`src/mem/MAA/IndirectAccess.cc:5390-5394`), and `finish()` sets `active = false` (`src/mem/MAA/SoaJitWriteRetirement.hh:129-136`). The second call requires `activeRun()` and `assertInvariants()` to remain true (`src/mem/MAA/IndirectAccess.cc:5317-5328`); the tracker explicitly considers an inactive tracker with retained nonzero counters invalid (`src/mem/MAA/SoaJitWriteRetirement.hh:188-198`). Therefore every nonempty compact-enabled operation reaches a deterministic terminal panic.

This is confirmed by read-only inspection of the implementation session's already-running A/B, not by a simulation launched for this review:

- compact SSSP `gem5.rc` is `134`;
- compact `stats.txt` is empty;
- `run.err` reports `SoA/JIT terminal accounting failed` from `IndirectAccess.cc:5273` at tick `4923936338`;
- the copied binary is SHA-256 `0c0a2e55e633ece51a856dde10ebd02130ee5ca318a0915ae199098e80bf889f`.

The focused tests miss the state-machine composition. `tests/test_soa_jit_write_retirement.cpp:88-108` exercises one `finish()` followed by `reset()` but never a second terminal check, while `tests/test_hybrid_rmw_soa_contract.py:79-124` only searches source text for terminal predicates.

Required fix: make terminal validation observational and perform `finish()` exactly once at the ownership transition, or make the lifecycle operation idempotent with an explicit terminal state. Add a C++ or live-path unit that executes the actual `Request -> Response` sequence and checks both checker invocations, statistics publication, reset, and next-generation begin.

### Medium — 146 bytes and a three-bit response tag are not a complete hardware implementation ledger

`PersistentStateBits` charges only two 64-bit globals plus eight `(64-bit sequence, 64-bit address, 2-bit state)` entries (`src/mem/MAA/SoaJitWriteRetirement.hh:22-36`), yielding 1168 bits/146 bytes. The implemented persistent object also contains three 64-bit counters, a high-water field, and an active bit (`src/mem/MAA/SoaJitWriteRetirement.hh:246-253`). The counters are not merely printed: `finish()` and `assertInvariants()` consume them to decide functional acceptance (`src/mem/MAA/SoaJitWriteRetirement.hh:129-136,188-198`). If these are hardware-visible functional state, the 146-byte total omits them. If they are simulator-only checkers that hardware derives or does not implement, that derivation and the smaller synthesized state machine must be specified before 146 bytes can be called complete.

Conversely, the 146-byte formula may double-count state already resident in the request/response machinery. Each slot charges a 64-bit address while the queued packet and MAA outstanding map already retain the request address (`src/mem/MAA/Port.cc:53-81,173-176`), and the response supplies the address again (`src/mem/MAA/Port.cc:781-793`). A sound incremental ledger needs to state whether this address is a new tracker copy, an existing MSHR/queue field, or reconstructed response metadata.

The three-bit claim has the same missing boundary. The actual packet sender state carries the full compact `Identity` (`generation`, `issueSequence`, `address`, and `credit`) (`src/mem/MAA/SoaJitWriteRetirement.hh:55-61`; `src/mem/MAA/IndirectAccess.hh:571-576`), and the response path passes all of it back to `acknowledge()` (`src/mem/MAA/IndirectAccess.cc:10206-10215`). `acknowledge()` uses generation, sequence, and address to reject stale, reused-credit/ABA, and wrong-address responses (`src/mem/MAA/SoaJitWriteRetirement.hh:216-231`). A bare three-bit credit cannot reproduce those checks after credit reuse unless an existing transaction identifier and reliable no-duplicate response contract supply the incarnation. The terminal trace instead discounts the duplicated fields as validation metadata while claiming three bits (`src/mem/MAA/IndirectAccess.cc:7183-7223`); validation metadata is still required state unless its existing owner is identified.

The gem5 stat ledger also mixes installed capacity with cumulative per-operation exposure. Persistent bits/bytes and provisioned credits are added once per completed instruction (`src/mem/MAA/IndirectAccess.cc:7098-7132`), so two operations report twice the hardware capacity. The transient response-tag high water appears only in a debug trace, not a registered statistic (`src/mem/MAA/MAA.hh:1230-1243`; `src/mem/MAA/MAA.cc:7507-7558`). The payload statistic is likewise the sum of per-operation high waters despite its `HighWaterBytes` name.

Required fix: publish a bit-level incremental hardware table separating (1) existing packet/MSHR address and payload, (2) new persistent tracker state, (3) transient request/response tag state, and (4) simulation-only validation/statistics. Either charge the incarnation metadata needed for the implemented stale/ABA checks or explicitly rely on and cite an existing transaction identity/no-duplicate contract. Report installed capacity separately from per-instruction sums.

### Medium — compact result-pipeline region instrumentation loses the originating region

Compact identities retain no context index or result-pipeline region. Instrumentation assigns region using the credit number (`src/mem/MAA/IndirectAccess.cc:4213-4219`), but all eight credits are numbered 0 through 7 and `regionForLine()` divides by 32 (`src/mem/MAA/SoaJitResultPipeline.hh:24-44`). Consequently every compact write is reported in region 0, even when a context from region 1 (contexts 32-63) handed off the packet. This makes `compact_write_hwm_r0/r1` and `dual_region_overlap_ticks` false for 64-context operation; depending on concurrent reads it can create either a false dual-region overlap or miss a real one (`src/mem/MAA/SoaJitResultPipeline.hh:95-125`).

The added C++ test uses only `{8,0}` compact occupancy and therefore cannot expose the problem (`tests/maa/soa_jit_result_pipeline_test.cc:63-85`). The A/B runner fixes active contexts at eight (`experiments/scripts/run_hybrid_compact_write_retirement_ab.sh:129-141`), while SimObject plumbing permits compact retirement with 8, 16, 32, or 64 active contexts.

Required fix: retain an explicit origin region/context bit in the tracker, or make compact occupancy regionless and remove region-attributed claims. Test context 63 handing off to a reused credit while region-0 reads remain active.

### Low — the experiment records source and binary identities but does not bind the binary to that source

The A/B runner accepts an arbitrary executable argument, separately records the current clean source commit/tree/archive, and hashes the executable (`experiments/scripts/run_hybrid_compact_write_retirement_ab.sh:8-45,63-67,343-368`). It never checks an embedded build ID/manifest or a known binary hash tied to the recorded tree. The inspected implementation worktree is clean at `f35f9111`/tree `290c380e131e5de0254342f80da395367e5c224c`, but its `build/X86/gem5.opt` mtime is 08:15:32, before the 08:17:31 commit, so exact archive-to-binary provenance is plausible but not proven. The observed panic does prove that the binary contains the new compact terminal logic; it does not establish a reproducible exact-source build certificate.

Required fix: have the build emit a source-tree/dependency manifest and require the runner to compare it with the recorded tree, or freeze a trusted binary hash produced by a documented clean build.

## Audited behavior without an additional finding

- Reserve/send/commit/context-clear ordering is textually correct (`src/mem/MAA/IndirectAccess.cc:5074-5111`): the packet copies the 64-byte context payload before `sendPacket()`, the sender state is pushed before handoff, tracker commit follows handoff, and the context is cleared last. `sendPacket()` synchronously adopts a new packet into either its outstanding map or deferred FIFO (`src/mem/MAA/Port.cc:48-81,173-249`). A same-address response-bearing `WriteReq` is not coalesced; it fail-stops as a duplicate write (`src/mem/MAA/Port.cc:118-121`). A focused packet-level ownership test is still absent.
- On full credits, the context is retained, temporary packet/sender are deleted, the stall is counted, and a one-cycle retry is scheduled (`src/mem/MAA/IndirectAccess.cc:5077-5085`). The request loop stops after the failed issue (`src/mem/MAA/IndirectAccess.cc:4866-4884`), and a WriteResp forces a wakeup (`src/mem/MAA/IndirectAccess.cc:10206-10225`). This is live if the downstream response/retry contract is live, though it polls every modeled cycle while full.
- Credit reuse is protected within practical run lengths by generation, monotonically increasing issue sequence, address, credit state, and fail-stop validation (`src/mem/MAA/SoaJitWriteRetirement.hh:75-127,216-231`). Generation exhaustion is guarded before begin (`src/mem/MAA/IndirectAccess.cc:6066-6078`). `nextIssueSequence` itself has no wrap guard (`src/mem/MAA/SoaJitWriteRetirement.hh:91-99`), leaving a theoretical 2^64-reservation ABA residual.
- Epoch reset and instruction completion wait for both context and compact-credit drain because `soaJitContextsEmpty()` includes tracker emptiness (`src/mem/MAA/IndirectAccess.cc:4182-4192,4421-4437,6562-6575`). This preserves correctness but prevents compact writes from surviving across an epoch reset. `hasLiveSoaJitState()` separately includes `activeRun()` (`src/mem/MAA/IndirectAccess.cc:4227-4237`).
- Old-result and A-write responses are distinguished by sender-state type before compact/legacy A-write discrimination (`src/mem/MAA/IndirectAccess.cc:10162-10225`). Old-result selection/finish and both A-write ledgers are terminal-gated (`src/mem/MAA/IndirectAccess.cc:5314-5362`). A pre-existing residual remains: `validateSoaJitAddressSpans()` checks mutable A, values, indices, and predicate but not the old-result span (`src/mem/MAA/IndirectAccess.cc:1368-1410`), so an adversarial result/A physical alias is not rejected by that full-span prewalk.
- Legacy behavior is default-off in the SimObject (`src/mem/MAA/MAA.py:205-212`) and CLI (`configs/common/Options.py:480-491`), propagated through `MAAConfig.py:199-206`, `MAA.cc:184-189,708-716`, and `IndirectAccess.cc:119-224`. When disabled, compact begin/commit/ack paths are not entered and terminal checks require a zero/inactive tracker (`src/mem/MAA/IndirectAccess.cc:5329-5334`).

## Validation and evidence

- `tests/maa/run_soa_jit_result_pipeline_unit.sh`: PASS in optimized and ASan/UBSan modes.
- `tests/test_soa_jit_write_retirement.cpp`: PASS in an optimized standalone build.
- Same tracker test: PASS with ASan/UBSan.
- `pytest` is not installed (`python3 -m pytest` reports `No module named pytest`). All 26 zero-argument contract functions from `tests/test_hybrid_rmw_soa_contract.py`, `tests/test_soa_jit_result_pipeline.py`, and `experiments/tests/test_hybrid_compact_write_retirement_ab.py` were therefore executed directly with `runpy`; all passed.
- No gem5 rebuild was needed for diagnosis. Read-only provenance inspection found clean source commit `f35f9111`, tree `290c380e131e5de0254342f80da395367e5c224c`, binary SHA-256 `0c0a2e55e633ece51a856dde10ebd02130ee5ca318a0915ae199098e80bf889f`, compact exit 134, empty final stats, and the exact terminal panic described above.
- The active implementation-side baseline was left running; no process, evidence root, checkpoint, or lead worktree was modified.

## Fix gate before reconsideration

Do not cherry-pick this commit. A successor should, at minimum:

1. make terminal checking/lifecycle transition single-shot or explicitly idempotent and add an actual request-to-response lifecycle test;
2. define and test the minimal hardware identity path for stale/duplicate/ABA responses;
3. replace the 146-byte/three-bit claims and stats with a complete non-double-counted capacity ledger;
4. repair or remove region-attributed compact instrumentation for context counts above 32;
5. bind the tested gem5 binary to the exact source tree and rerun focused tests before any simulation gate.
