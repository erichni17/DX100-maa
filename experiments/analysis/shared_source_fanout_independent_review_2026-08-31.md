# Shared-source fanout independent review — 2026-08-31

## Findings

### BLOCKER — the advertised shared payload bound excludes live source-line storage

Promotion is blocked. With `virtual_shared_result_payload=true`, the constructor passes `packedResponse=false` to `VirtualResponsePayloadStore::configure` (`IndirectAccess.cc:196-200`). That store consequently allocates one full 64-byte line per response slot (`VirtualResponsePayloadStore.hh:26-35`). On a source response, the shared path copies the full cache line into that store (`IndirectAccess.cc:10015-10022`), while its bounded credit is only `fanout.payloadWords()` (`IndirectAccess.cc:2856-2879`, `2906-2922`).

The accepted r3 case has four response slots, so the simulator allocates 256 bytes (64 32-bit words) of host payload backing for those slots. Its claimed unified source-plus-combiner pool is only 15 + 1 = 16 words (64 bytes), and the one active all-fanout response is charged one word despite retaining the whole 16-word source line. The observed `IND_VirtSharedPayloadHighWater=16` therefore proves conservation of the new counters, not conservation of all live payload bytes. Either pack/store only the fanout's unique words, or explicitly model and account the line buffers as additional hardware storage before promotion.

### BLOCKER — fanout metadata remains bounded host-only state without a hardware-storage contract

`VirtualSourceFanout` contains sixteen 16-bit per-word use counters plus scalar state. Copies live in each `VirtualResponseSlot`, each `VirtualSourceReservation`, the pending-source state, and the bounded-global source state (`IndirectAccess.hh:105-160`, `476-478`). The response and reservation counts are bounded, and the per-word count is correctly capped at 16,384, but none of this metadata is included in the 4,096-word physical-tile bound or in a separate metadata/area budget. The use of fixed arrays instead of an unbounded vector is an improvement, but it is still simulator-host storage standing in for unaccounted hardware.

### BLOCKER — the scan latency is serialized, but its completed result is available to admission at zero simulated time

`buildVirtualSourceFanout` walks the complete OffsetTable chain synchronously and returns the final unique-word count in the same call (`IndirectAccess.cc:2790-2830`). The code correctly chains `virtual_fanout_scan_finish_tick` and delays the source read until that ready tick (`2831-2851`, `2945-2951`), so source data does not arrive for free. However, `virtualSourceCreditAvailable` and `issueVirtualSource` immediately use the completed count to accept/reject and reserve exact compressed credit before the modeled scan has completed (`2960-2998`, `7427-7460`, `7550-7601`). Thus the timing token is serialized but the decision-producing work is still performed at zero time. This needs an explicit post-scan event/state transition, or a justified conservative reservation scheme that cannot inspect the final histogram early.

The r3 artifact has only one fanout scan event, so it confirms the 4-wide latency for one maximum-length scan but does not dynamically exercise ordering between multiple serialized scans.

## Promotion decision

**BLOCK.** Commit `035cf130` repairs the two concrete failures exposed by r1 and r2 and produces a functionally correct, bounded run. It does not yet support a claim that the shared payload is fully hardware-bounded or that all decision-producing scan work is timed. No speedup or architecture-performance conclusion is made from these runs.

## Commit review

- `2f997802` introduces a fixed 16-word fanout histogram, a 16,384-logical-use limit, four-descriptor scan accounting, final-use transfer/rollback logic, and terminal shared-credit assertions. The maximum counter value and `ceil(16384 / 4) = 4096` scan-cycle calculation are representable in the chosen 16-bit fields. The unit test covers duplicate final use, rollback, overflow at the maximum, and exact empty closure.
- `c676a0f0` adds the shared-mode live gate and artifact fields. Its r1 run is a valid rejection, not completion: checkpoint exit 0, restore exit 134, empty final stats, and panic `source response needs 16384/1 pooled words`.
- `44230fa7` removes the invalid logical-word-versus-compressed-pool admission check. Its r2 run is also a valid rejection: checkpoint exit 0, restore exit 134, empty final stats, and panic `shared payload insertion exceeded 16+1/16`.
- `035cf130` includes outstanding source reservations in combiner pressure and rechecks capacity before allocation. This resolves the r2 overcommit in the tested path. Its r3 run completes exactly and all terminal counters close, but the host-only payload and metadata findings above remain.

The commits form the expected ancestor chain (`2f997802` → `c676a0f0` → `44230fa7` → `035cf130`, with intervening `8cb43baa` before `c676a0f0`). Each run's recorded `IndirectAccess.cc` SHA-256 matches the named source commit. All three record empty `source_status.txt` and `source.diff`.

## Accepted-candidate evidence audit

Immutable root: `/data1/nier/dx100-runs/2026-08-31-shared-fanout-035cf130-r3`

| Check | Evidence | Result |
|---|---|---|
| Terminal completion | `checkpoint.exit=0`, `restore.exit=0`, one exact result line, one `ROI Ended`, final `m5_exit`, two nonempty closed stats sections | Pass |
| Source and binary identity | manifest commit `035cf130a70a600633d6290ce7dceecde3af0768`; clean source snapshot; recorded source hashes equal commit contents; current gem5 binary matches recorded SHA-256 `16921eae...b75` | Pass, with archival caveat below |
| Maximum fanout bound | `n=16384`, one source line, `IND_VirtFanoutScanWords=16384`; unit test accepts 16,384 and rejects the next observation | Pass |
| Serialized scan latency | 1 event, 16,384 words, 4-wide, exactly 4,096 charged cycles; source-flight interval is 4,185 cycles | Pass for single-event latency; multi-event serialization unexercised; zero-time admission finding remains |
| Exact output | `errors=0`; observed FNV-1a hash `7221120122736935811` independently recomputes from 16,384 copies of `source[13] = 224`; guards are included in the benchmark error count | Pass |
| Source traffic | 1 source read (`1026` total indirect memory reads − `1025` index-line reads); no SPD read cycles | Pass |
| Shared credit counters | response-word HWM 1; combine-word HWM 16; shared HWM 16/16; final transfer 1; rollback 1,023; response slots HWM 1/4 | Counter conservation passes; physical storage claim fails |
| Retirement and ACKs | 1 full-line + 2,046 partial writes = 2,047 issues; 2,047 completions; outstanding-write HWM 2; terminal exit implies zero outstanding writes | Pass |
| Combiner/storage closure | exact output, terminal assertions, zero retained response/combine/spill state; line HWM 2/64 | Pass for modeled counters |
| Host-only storage | full response-line array plus fanout/reservation metadata are outside the shared word budget | Fail |

The rollback/final-transfer ordering is internally sound in both the direct and lookup paths: a fanout use is consumed before insertion; a failed insertion restores the use count and restores response credit only if that attempt was the final-use transfer; a successful final use moves one word of credit from response to combiner before committing. The r3 counts (one ultimate transfer and 1,023 restored attempts under a 16-word adversarial pool) exercise this retry path heavily without losing output.

## Evidence limitations

- The live runner hashes `IndirectAccess.cc/.hh`, `MAA.cc/.hh/.py`, configs, benchmark, binary, and source diff/status, but omits the newly introduced `VirtualSourceFanout.hh` and the relevant `VirtualResponsePayloadStore.hh`. The recorded clean commit and binary hash partly recover provenance, but successor evidence should hash every treatment source directly.
- The gem5 binary is referenced at a mutable external build path rather than archived beneath the immutable run root. r3 still revalidates against that path at review time; the r1 and r2 binary hashes no longer match the overwritten external binary. This does not change their explicit failure classification, but weakens long-term reproduction.
- There is one candidate observation and no matched non-shared control. This is enough for the functional adversarial checks above, not for a performance claim.

## Validation commands

No simulator was launched. Review validation used the existing unit/contract tests, shell syntax checking, immutable artifact/hash inspection, independent expected-hash recomputation, and Git source-history comparison.
