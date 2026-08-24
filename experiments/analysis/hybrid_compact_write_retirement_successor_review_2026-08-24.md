# Successor review of compact SoA/JIT A-write retirement

Commit reviewed: `32017c355f72db9b7be325a071b467df5b2c4e8a` only
Parent: `f35f9111ae49d8f6d48ebb715ccaf8ec3f5f3835`
Source tree: `6330cf2af3b01e76611da951f962ba38a773021d`
Review date: 2026-08-24

Scope: independent source review and focused tracker/result-pipeline/contract tests. No mechanism source was changed and no simulation was launched. The starting findings were read from the prior review at commit `2dfa906c` (`experiments/analysis/hybrid_compact_write_retirement_review_2026-08-24.md`).

## Findings, ordered by severity

### Medium — remaining: the claimed 146-byte persistent ledger omits the functional active-state bit

`PersistentStateBits` charges two 64-bit globals and eight `(64-bit sequence, 64-bit address, 2-bit state)` slots, exactly 1168 bits/146 bytes (`src/mem/MAA/SoaJitWriteRetirement.hh:29-43`). The successor now explicitly classifies `reservations`, `issues`, `responses`, and `highWater` as simulator-only validation counters (`src/mem/MAA/SoaJitWriteRetirement.hh:17-24`), and the renamed registered statistics correctly describe cumulative sums of per-operation capacity/high-water observations rather than installed capacity (`src/mem/MAA/MAA.cc:7507-7562`). Those parts of the prior accounting finding are closed.

The tracker nevertheless has a distinct functional `active` bit (`src/mem/MAA/SoaJitWriteRetirement.hh:258-265`). `begin()` rejects a second active generation based on that bit (`src/mem/MAA/SoaJitWriteRetirement.hh:70-79`), while `finish()` clears it (`src/mem/MAA/SoaJitWriteRetirement.hh:136-143`). It cannot currently be derived from `activeGeneration`: `finish()` leaves `activeGeneration` nonzero, and only the later `reset()` clears it. The bit is neither included in `PersistentStateBits` nor identified as an existing enclosing-controller bit. Charging the implemented lifecycle therefore requires at least 1169 bits, which rounds to 147 bytes if this tracker is byte-accounted independently.

The three-bit response boundary itself is now explicit: the contract requires reliable exactly-once response delivery and forbids credit reuse before acknowledgement (`src/mem/MAA/SoaJitWriteRetirement.hh:12-24`; `src/mem/MAA/IndirectAccess.cc:7167-7217`; `experiments/scripts/run_hybrid_compact_write_retirement_ab.sh:382-393`). The full generation/sequence/address sender identity remains simulator validation metadata, while the hardware contract maps the returned three-bit credit to the charged persistent slot. This closes the prior ambiguity about how a bare tag avoids duplicate/ABA acceptance under the stated reliability contract, but it does not close the missing active-state charge.

Required fix: either add the active bit and report the resulting bit/byte total, or explicitly derive activity from already-charged/enclosing state and change the implementation/model so that derivation is true. Keep the simulator-only counters and cumulative `*Sum` statistics separate from installed capacity.

### Low — remaining: post-commit timestamps and hashes still do not bind the binary to the recorded source tree/archive

The runner accepts an arbitrary executable argument (`experiments/scripts/run_hybrid_compact_write_retirement_ab.sh:6-8`), independently requires a clean worktree and records its commit/tree/archive hash (`experiments/scripts/run_hybrid_compact_write_retirement_ab.sh:26-33`), then copies and hashes the supplied binary (`experiments/scripts/run_hybrid_compact_write_retirement_ab.sh:43-56`) and emits both identities into the manifest (`experiments/scripts/run_hybrid_compact_write_retirement_ab.sh:354-372`). No assertion relates the binary hash to the source commit, tree, archive, or a trusted build certificate. The contract test checks only that all fields exist (`experiments/tests/test_hybrid_compact_write_retirement_ab.py:26-46`).

Read-only inspection of the independently running successor gate found a clean implementation worktree at `32017c355f72`, tree `6330cf2af3b0`, archive SHA-256 `2d9d4293a90e0856d7bf59af03babc95cb83771462fee065677b7034b0f7b39c`, and a post-commit `build/X86/gem5.opt` mtime of `2026-08-24 09:01:51 -0400`. The built binary and frozen evidence copy both hash to `c6eee0340f8f7f8e75c8172372cd3912b743a86db869fcfcb3b93468f0aac68e`. This establishes clean-source identity, post-commit temporal ordering, and exact copy equality, but not that the binary was produced from that tree; an unrelated or touched executable would satisfy the runner.

Required fix: emit a build certificate containing the exact commit/tree/archive and dependency identities alongside the binary, then have the runner fail closed unless that certificate and the binary hash match. A separately established trusted mapping from this exact binary hash to a documented clean build would also close the blocker.

## Closed prior blockers

### Closed — both terminal checks are observational and ownership finishes once in `Response`

`checkSoaJitTerminal()` no longer calls either `finish()` or `clearGeneration()`; it performs the same invariant checks on both calls and uses the const `canClearGeneration()` predicate (`src/mem/MAA/IndirectAccess.cc:5264-5384`). The request path performs the first check and transitions to `Response` (`src/mem/MAA/IndirectAccess.cc:6652-6657`). The response path performs the second check before publishing statistics (`src/mem/MAA/IndirectAccess.cc:6988-6991`), then clears the value generation and performs the tracker's sole ownership-consuming `finish()` after publication (`src/mem/MAA/IndirectAccess.cc:7616-7627`).

The tracker unit now executes two `complete()` observations, verifies that the tracker remains active, performs one successful `finish()`, resets, begins the next generation, and closes it (`tests/test_soa_jit_write_retirement.cpp:97-115`). The source contract additionally checks both call sites and the `check -> publish -> clear -> finish -> Idle` order (`tests/test_hybrid_rmw_soa_contract.py:163-182`). The deterministic double-finish panic from `f35f9111` is closed in the successor source.

### Closed — `canClearGeneration` is observational, fail-closed, and compatible with next-instruction liveness

`canClearGeneration()` rejects a generation with a filling line, any waiter, or any live prefetch credit without mutating state; `clearGeneration()` first requires that predicate and only then removes ready/no-waiter cache entries (`src/mem/MAA/SoaJitOverlapState.hh:439-465`). The 128-owner unit checks failure while fills or waiters remain, two repeatable successful observations after delivery, and the later destructive clear (`tests/maa/soa_jit_overlap_state_test.cc:148-185`).

After the sole response-side finish, the next instruction's decode resets the empty tracker and value coalescer (`src/mem/MAA/IndirectAccess.cc:5713-5727`), then allocates a new nonzero generation and begins the tracker (`src/mem/MAA/IndirectAccess.cc:6055-6067`). There is no new next-instruction reset or liveness blocker in the reviewed paths. The independently running exact A/B remains the required live-path confirmation.

### Closed — compact instrumentation is explicitly regionless for every supported active-context count

Live observation now counts compact awaiting responses as one total rather than mapping credit numbers to result regions (`src/mem/MAA/IndirectAccess.cc:4195-4218`). `SoaJitResultPipeline` states that compact credits have no region attribution, records only total high water and outstanding ticks, and excludes compact occupancy from read/write and dual-region overlap metrics (`src/mem/MAA/SoaJitResultPipeline.hh:12-22,84-125`). Its supported active geometries remain 8, 16, 32, and 64 (`src/mem/MAA/SoaJitResultPipeline.hh:39-42`), and its invariant no longer assigns compact state to an inactive region (`src/mem/MAA/SoaJitResultPipeline.hh:128-142`).

The trace publishes `compact_write_hwm_total` plus `compact_region_attribution=none` (`src/mem/MAA/IndirectAccess.cc:7318-7346`). The focused C++ test uses active-context count 64 with reads in both regions and compact occupancy, verifying that compact state changes neither read/write overlap nor dual-region overlap (`tests/maa/soa_jit_result_pipeline_test.cc:78-86`). This closes the false region-0 attribution from `f35f9111`.

## Remaining risks that are not new blockers

- The focused lifecycle coverage composes a tracker unit with a source-order contract rather than instantiating the full `IndirectAccessUnit` request/response state machine. The independent exact A/B must therefore complete without panic, publish all terminal ledgers, and prove the next instruction begins successfully before integration can be reconsidered.
- The exactly-once response guarantee is an explicit hardware/environment contract rather than a modeled retry/deduplication protocol. Any target transport that permits response loss or duplication invalidates the three-bit-only response mapping and must either add an incarnation identity or provide equivalent reliable delivery.
- The theoretical 64-bit issue-sequence wrap residual and the pre-existing old-result/A-span alias-validation residual from the prior review are unchanged by `32017c35`.

## Validation

- `tests/test_soa_jit_write_retirement.cpp`: PASS in optimized and ASan/UBSan standalone builds.
- `tests/maa/run_soa_jit_result_pipeline_unit.sh`: PASS in optimized and ASan/UBSan modes.
- `tests/maa/run_soa_jit_overlap_state_unit.sh`: PASS in optimized and ASan/UBSan modes, including the new `canClearGeneration` coverage.
- `python3 -m pytest ...`: unavailable because `pytest` is not installed.
- Direct `runpy` execution of all 27 zero-argument contract functions from `tests/test_hybrid_rmw_soa_contract.py`, `tests/test_soa_jit_result_pipeline.py`, and `experiments/tests/test_hybrid_compact_write_retirement_ab.py`: PASS.
- `bash -n experiments/scripts/run_hybrid_compact_write_retirement_ab.sh`: PASS.
- `git diff --check 32017c35 --`: PASS before this report was added.
- Registered review worktree status after validation: clean at `32017c35`; no test caches or reports were left behind.

The independent exact A/B had not yet published `manifest.txt` or `gate.complete` when inspected. Its processes/evidence were not modified or controlled by this review.

## Recommendation, conditional on the independent exact A/B

**If the A/B fails correctness, terminal closure, non-regression, or its stated meaningful-improvement threshold: reject `32017c35`.**

**If the A/B passes: fix and re-review; do not integrate `32017c35` yet.** A passing result would close the live lifecycle/correctness/performance risk, but it cannot repair the incomplete 146-byte ledger or create a source-to-binary binding. After the active-state accounting is made complete and the exact tested binary is bound to the recorded source/dependency certificate, the commit may be reconsidered for integration using that independently validated A/B result.
