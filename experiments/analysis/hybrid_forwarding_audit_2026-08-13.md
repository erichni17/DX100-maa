# General-hybrid producer/materializer forwarding audit — 2026-08-13

## Verdict

The observed **370 forwarded / 1,678 coherent cache-read** split is exact and
is not evidence, by itself, of an LLC-port bottleneck. Of the 2,048 FP64
backing lines:

- 1,666 lines were retired only through masked 64-byte writes; no single
  producer response contained an authoritative full-line payload.
- 382 lines had one authoritative full-line response. Of those, 370 arrived
  while their producer page was the materializer's active page and were
  forwarded. The other 12 page-1 responses arrived before page 1 became
  active, so their payloads were not retained.
- Therefore `370 + (1666 + 12) = 2048`, exactly matching the materializer's
  closure counters.

This change adds a narrow, default-off active-page masked-fragment accumulator.
It reuses at most 16 already charged materializer line buffers, tracks exact
word completeness, and exposes nothing to SPD until a whole line passes the
existing delayed commit. It does not attempt inactive-page retention or
concurrent page materialization.

## Frozen evidence and acceptance

Primary evidence is the completed API matrix at
`/data1/nier/dx100-runs/2026-08-13-general-hybrid-api-page-ready-823deb45-r2`.
Its manifest records clean source commit
`823deb4519c40a6c68850f54e1ef17c055a8ba8e`, one replica, API workload, two
memory channels, four L3 ports, and the exact restore command. The frozen
artifacts include:

- gem5 SHA-256
  `bbdabe0d0f4f4b54e55f33f79336d70bc46d7a9542681672ea63aba0e22a6ccb`;
- hybrid binary SHA-256
  `e806c2c1de0f48bf709a62dfbc4aaec5c29d88b814a0952a9b6c07b4e0912bee`;
- hybrid checkpoint identity
  `00cb13b5f3e588564670420c1f06176ad112436fce75b638d74dd93feb0637ad`.

`campaign.exit` and the token arm's `restore.exit` are both zero. The restore
log contains one final `m5_exit`, reports exact output hash
`7228541527853630339` with `errors=0`, and has no panic/fatal marker. The
analyzer classifies the complete matrix PASS. The token arm's ROI statistics
are `simTicks=23,738,859`, submissions/pages/retirements `4/4/1`, producer line
ACKs/page fallback lines `2048/0`, and forwarded/cache-read lines `370/1678`.
This is a correctness control, not a performance-promotion result.

## Exact live path

1. `MAA::resetVirtualPageReady()` binds token, generation, backing address,
   range, and word size, then opens the bounded early-line ledger
   ([MAA.cc](../../src/mem/MAA/MAA.cc)).
2. The indirect producer drains a full combiner line as an ordinary 64-byte
   write when its mask is complete. Under `--maa_virtual_masked_writes`, a
   partial victim is instead sent as a 64-byte packet with byte enables
   (`IndirectAccessUnit::drainVirtualCombiner()` and
   `createRetirementWrite()` in
   [IndirectAccess.cc](../../src/mem/MAA/IndirectAccess.cc)).
3. `trackVirtualRetirementWrite()` records the exact generation, backing line,
   and semantic word mask before issue. On the sole exact `WriteResp`,
   `MAA::recvTimingResp()` passes the response packet payload and size through
   `retirementWriteComplete()` to `completeVirtualRetirementWrite()`
   ([Port.cc](../../src/mem/MAA/Port.cc),
   [IndirectAccess.cc](../../src/mem/MAA/IndirectAccess.cc)).
4. `MAA::setVirtualLineWordsReady()` rejects stale generation/backing identity,
   routes the ACK to exactly one live consumer authority, and calls
   `HybridConsumerPipeline::notifyProducerLineWriteAck()`. That method rejects
   duplicate/overlapping word masks and marks a line readable only after all
   words have exact WriteResp authority
   ([HybridConsumerPipeline.hh](../../src/mem/MAA/HybridConsumerPipeline.hh)).
5. The original fast path captures the response only when the current mask is
   full, the response is exactly 64 bytes, the materializer has that producer
   page active, and a charged line buffer is available. It then reserves the
   ordinary SPD latency and commits at `commit_tick`.
6. Otherwise the ACK makes the line eligible but does not manufacture payload.
   `servicePageMaterialization()` issues an exact coherent `ReadReq` for the
   backing line. Its `ReadResp` fills the same charged line buffer, incurs the
   same SPD latency, and reaches the same commit loop. Page-ready and retirement
   require `forwarded + cache_read_fallback == 2048` and
   `producer_line_acks + page_fallback == 2048`.

The producer transaction used in the trace is the physical write key. The
audit joined each `page_materialization_producer_line_ready.transaction` to
the latest matching `backing_write_issue.key`; a line was classified full only
for `bytes=64, valid_words=0x0`. This yields 5,055 exact WriteResp events:
382 full-line events and 4,673 masked-fragment events. Across unique lines,
there are 382 full-only, 1,666 masked-only, and zero mixed lines.

## Timing decomposition

Absolute ticks below are from the frozen token trace. Forwarded and cache-read
columns are cumulative at each materialized page completion.

| page | active interval | producer page authority | full lines in window | full lines outside | masked-only lines | forwarded / cache-read |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 3,447,834,286–3,459,737,362 | 3,459,732,980 | 365 | 0 | 147 | 365 / 147 |
| 1 | 3,459,882,282–3,467,065,631 | 3,467,034,331 | 5 | 12 | 495 | 370 / 654 |
| 2 | 3,467,115,086–3,467,652,193 | 3,467,007,100 | 0 | 0 | 512 | 370 / 1,166 |
| 3 | 3,468,006,197–3,468,533,914 | 3,467,038,087 | 0 | 0 | 512 | 370 / 1,678 |

Page 0 was admitted before the first backing write and captures every one of
its 365 full-line responses. Page 1 had 11 full responses while page 0 was
active and one in the 140,638-tick gap before page-1 admission; only its five
later full responses are captured. Producer authority for pages 2 and 3
predates their materializer admission, so their already-acknowledged lines are
read coherently from backing. The page-2 authority preceding page 1 is legal:
producer completion order is not page order.

For masked-only lines, all fragments are in the active interval for 147 page-0
lines and only three page-1 lines. No page-2/3 masked line has an active-page
fragment sequence. Thus an unlimited active-page-only mechanism has a
trace-derived ceiling of 150 additional forwarded lines; the bounded 1–16
buffer implementation may capture fewer. Eliminating the remaining 1,516
masked fallbacks requires inactive-page payload storage, materially different
producer write formation, or true concurrent page contexts.

## History checked before the change

- `54b1e47b` introduced the exact-ACK materializer with bounded contexts,
  coherent fallback, and unchanged downstream instructions.
- `35089b08`, `823deb45`, and `9692859d` progressively removed mutable register
  dependencies. They improve admission legality but do not turn masked payload
  into full-line authority.
- `4f866ae1` added page-zero prearm and `8a85c8d6` made activation nonblocking.
  In the completed `186355e4` matrix, ordinary and prearmed token arms both
  remain exactly `370/1678`; prearm changes instruction timing, not producer
  write granularity.
- `31a89bdc` woke dependents after every line commit. It was reverted by
  `127e9413` after a matched direct-baseline regression of 0.96%. The bounded,
  default-off replacement in `186355e4` wakes at selected committed-line
  milestones. Completed batch 1/2/4/8 controls show 367–368 forwarded lines,
  not an increase. Wakeups are downstream of capture eligibility.
- The API ping-pong guest submits two alternating destination pages, but the
  live materializer still has one `pageActive` field. `submitPageMaterialization()`
  returns Retry while it is set. In the completed ping-pong trace, page 1 is
  admitted at the exact tick page 0 becomes ready, and later pages are likewise
  serialized. That arm is exact-correct but records `370/1678` and
  `24,321,352` simTicks versus `23,461,228` for its serial token control; it is
  not evidence for concurrent-page promotion.
- `f1974ebe` adds a matched 2/4/8 LLC-port sensitivity contract. Its audited
  four-port evidence motivates the test but explicitly does not establish an
  LLC bottleneck or a cross-control speedup.

## Opt-in masked-fragment contract

`--maa_page_materialization_fragment_buffers=N` accepts `0..16` and defaults
to zero. With zero, the existing full-line forwarding and coherent fallback
path is unchanged. With `N>0`:

- Only the active materialization page is eligible. No early-ledger or inactive
  page payload is retained.
- A fragment is consumable only immediately after the existing token,
  generation, line, nonzero transaction, non-overlapping word-mask ACK accepts
  that exact identity. The eligibility is single-use; duplicates cannot mutate
  a buffer.
- Each retained line occupies one existing 64-byte materializer buffer and a
  word-completeness mask. The configured bound cannot exceed the existing 16
  line buffers; the added mask is included in conservative control-byte
  accounting. Full-line responses may reclaim an incomplete fragment buffer,
  preserving priority for already complete payload.
- Only bytes selected by the authenticated word mask are copied. A line is
  promoted only when its retained mask is complete. Missing payload, a missed
  fragment, exhaustion, or final page authority abandons incomplete private
  bytes and leaves the line on coherent cache fallback.
- Promotion produces the same exact `ReadBacking`-shaped buffer state and the
  same reserved SPD `commit_tick` as existing full-line forwarding. No SPD word
  is written and no dependent is woken before the whole-line commit.
- `page_materialization_fragment_accumulated_lines` counts successful complete
  masked lines; `page_materialization_fragment_buffer_stalls` counts bounded
  retention failures. Accumulated lines remain part of
  `page_materialization_forwarded_lines`, preserving exact closure.

No gem5 workload was launched for this audit. Focused optimized and ASan/UBSan
C++ tests cover split-fragment assembly, exact single-use duplicate rejection,
the delayed no-early-visibility boundary, missing-payload fallback, finite
buffer exhaustion, and page-authority cleanup. The Python contract test covers
CLI/SimObject/config propagation, the `0..16` bound, and default-off behavior.
Promotion still requires an independently completed, exact-correct matched
matrix with the knob as the sole treatment and the new counters reconciled.
