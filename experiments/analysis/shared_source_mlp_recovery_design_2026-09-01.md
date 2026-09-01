# Shared-source MLP recovery design

Date: 2026-09-01

Disposition: read-only diagnosis and bounded design; no production source was
edited and no gem5 workload was launched.

## Executive finding

The current strict result is correct, complete, and 66.257% slower than sealed
r6 in this one historical cross-binary comparison. The regression is not in B
fetch or the page consumer. It is caused by a control dependency introduced by
the timed fanout scan:

1. Build claims one source line, constructs its complete fanout, records a
   four-cycle scan, stores that line in `virtual_pending_source`, sets
   `virtual_build_incomplete`, and transitions to Request.
2. Request defines `responses_complete = virtual_sources_drained` whenever
   `virtual_build_incomplete` is true.
3. It therefore cannot return to Build when the pending scan becomes ready. It
   waits until the previously issued source response has arrived and its
   response slot has drained.
4. The next Build issues the pending line and immediately repeats the sequence
   for the next line.

This turns a nominal four-cycle scan into a one-source response epoch. The raw
trace independently derives response in-flight HWM 128 -> 1, and all 1,024
successive current issue pairs have the prior response before the next issue.

The smallest repair is **not a new queue**. Retain the existing single pending
scan/source latch, but let Request return to Build as soon as that latch is
scan-ready and has an exact response-slot/unified-pool reservation, even while
older responses or writes remain in flight. Build atomically issues that line,
clears the latch, and starts the next scan. The latch then behaves as a
one-entry scan pipeline. A fixed ready queue is a fallback only if a controlled
micro shows material scan-engine idling behind unified-pool credit stalls.

## Evidence identity and limits

Sealed authority:

- `/data1/nier/dx100-runs/2026-08-31-ume-gzz-matched-consumer-r6`
- source commit `f331383f158d37f06c2e2d4c6a859c9b48801845`
- gem5 SHA-256
  `d3885ab0f0b84be5bce64c0fa81af97c3d1b84638e0e23bdcff95e25fcf493cc`
- strict guest SHA-256
  `7e90552703cfa14dba3167a92b71e36427dba324b6333e3ba349e079823b9b11`
- strict checkpoint identity
  `a50607bc0632eef8683eac94febe7d7fe7b84966e7dfdf7cdc875475c3b3e997`

Current candidate:

- `/data1/nier/dx100-runs/2026-09-01-ume-gzz-current-shared-candidate-r1`
- exact captured MAA source matches both recorded source commit
  `dffa557381637cbb1a34411d737c7a83b5e493ae` and build-worktree head
  `545baa0bc06abc5ff5110ddf52a638f879779647` for the five audited files
- gem5 SHA-256
  `45206b3433449e10b26bbd8ff32281c06e533c101213097a27d50c364ca3c267`
- strict guest SHA-256
  `289954b0668d1b15274dda1944a7de2ba169508251a9d47b1c67bb90d0198647`
- strict checkpoint identity
  `f91400d4f867596a60f371070e3747bb42f180a67e4d9e1159538b2f17318c02`

Both complete artifact SHA-256 manifests verify. Both wrappers returned zero,
record a now-absent PID identity, contain nonempty final stats, and terminate
with `m5_exit`. Both produce fingerprint `7602200327591349891`, 196,384 checked
elements, zero gradient errors, and zero volume errors. Normalizing only run and
source-root paths, the strict restore command lines are identical. Ramulator
binary/config hashes and all architecture command-line knobs are identical.

This is still a historical cross-binary comparison: simulator, guest, and
checkpoint hashes differ, and there is one observation per treatment. It is
valid for mechanism diagnosis because the raw trace and exact source agree; it
is not promotion-quality causal performance attribution. A fresh isolated pair
is required by the acceptance gates below.

## Independent phase and work attribution

All numbers below were extracted again from raw `strict_two_phase_timing`,
event streams, and final stats rather than copied only from `result.json`.
Ticks are simulated ticks. A and backing windows overlap and must not be added.

| Metric | sealed r6 strict | current strict | current / r6 |
|---|---:|---:|---:|
| total `simTicks` | 25,470,375 | 42,346,396 | 1.6626 |
| B fetch | 2,228,560 | 2,228,560 | 1.0000 |
| Row/Offset admission | 2,214,475 | 2,157,509 | 0.9743 |
| A first issue through last response | 2,239,202 | 19,086,114 | 8.5237 |
| backing first write through last ACK | 2,133,721 | 15,533,877 | 7.2803 |
| page-ready spread | 37,560 | 8,138 | 0.2167 |
| consumer | 7,183,975 | 7,183,036 | 0.9999 |

The total increase is 16,876,021 ticks. The A window grows by 16,846,912
ticks. B is bit-for-bit equal in duration and the consumer differs by only 939
ticks. This localizes the total regression to source/backing production rather
than input fetch or consumption.

Semantic work remains closed:

| Counter | sealed r6 strict | current strict |
|---|---:|---:|
| descriptors | 16,384 | 16,384 |
| A issues / responses | 1,025 / 1,025 | 1,025 / 1,025 |
| shared transfers / rollbacks | 16,384 / 0 | 16,384 / 0 |
| backing semantic bytes | 65,536 | 65,536 |
| pages ready | 4 | 4 |
| exact B once / raw B retained / replay | 1 / 0 / 0 | 1 / 0 / 0 |

Current improves write shape: 1,024 full-line writes and zero partial writes,
versus r6's 1,011 full plus 26 partial requests (1,037 total), so the slowdown
cannot be extra semantic or transport work. Current transports 65,536 backing
bytes versus r6's 66,368.

## Why response HWM falls from 128 to 1

The exact current-source path is:

- `buildVirtualSourceFanout` peeks the entire Offset chain, seals 16 exact
  per-word use counters, and serializes the modeled scan behind
  `virtual_fanout_scan_finish_tick` at four descriptors per cycle.
- `deferVirtualSourceFanout` schedules the execute event at scan readiness.
- Build commits the RowTable claim into the single `virtual_pending_source`
  latch on every scan wait and sets `virtual_capacity_full`.
- Build copies that condition to `virtual_build_incomplete` and enters Request.
- Request first drains response work. At the current lines 7953--7969 it sets
  `responses_complete = virtual_sources_drained` for an incomplete build and
  only then returns to Build.

The event sequence proves the resulting dependency, not merely correlation:

- all 1,024 next-line scans are ready before the prior issued line responds;
  mean lead is 15,041 ticks and maximum lead is 390,311 ticks;
- all 1,024 prior responses arrive before the next source issue;
- r6 has only 45 nonzero issue-tick transitions and can issue 128 lines at one
  tick; current has 1,024 transitions and never issues two at one tick;
- source issue HWM independently reconstructed from issue/response events is
  exactly 128 for r6 and 1 for current, matching the stats;
- r6 issue span is 2,068,930 ticks (2,020 ticks per interval on average);
  current issue span is 19,070,777 ticks (18,624 ticks per interval);
- mean matched issue-to-response latency actually falls from 175.8 cycles in
  batched r6 to 52.0 cycles in current. The current run is slower despite lower
  individual latency because it eliminates memory-level parallelism.

Mechanism counters agree:

| Counter | sealed r6 strict | current strict |
|---|---:|---:|
| response-slot HWM | 128 | 1 |
| response-word HWM | 2,048 | 16 |
| virtual build rounds | 59 | 1,029 |
| Build cycles | 53 | 1,018 |
| Request cycles | 41,105 | 96,252 |
| fanout scans / words / cycles | not present | 1,025 / 16,384 / 4,096 |
| shared unified-pool HWM | 4,096 | 3,484 |

Only 4,096 cycles are explicitly charged to fanout scanning. The much larger
A expansion is the exposed per-line response and drain latency created by the
Request completion rule.

## Why backing grows

Backing lines become legal only after all 16 destination words for a line have
reached the combiner. r6's 128-line source batches let unrelated source
responses populate many destination lines concurrently, so first backing write
follows first A issue by 178,410 ticks and up to 13 backing writes share one
tick. Its average backing issue gap is 2,030 ticks.

Current delivers and drains one source line before admitting the next. With the
GZZ permutation, a destination line's 16 words are spread over the serialized
source stream. First backing write is therefore delayed 3,593,240 ticks after
first A issue, no two backing writes share a tick, and average backing issue
gap rises to 15,155 ticks. The last backing issue tracks the last serialized A
response. This expands the backing window from 2,133,721 to 15,533,877 ticks
even though current issues 13 fewer writes and no partial writes.

## Smallest bounded fix: reuse the single pending latch

Add a Request-side progress condition after `drainVirtualResponses()` and
before the existing `responses_complete` decision:

1. If `virtual_pending_source` exists and its fanout is not ready, arm exactly
   the fanout-ready execute event with `account=false`; remain in Request.
2. If it is ready, service legal combiner progress/spill once and evaluate
   `virtualSourceCreditAvailable`, which already checks the 128 response slots
   and the exact unique-word reservation against the unified pool.
3. If credit is available, account the Request interval, transition to Build,
   and schedule the same-cycle/next-edge execution. Do **not** require
   `virtual_source_received == virtual_source_expected` and do not flush the
   combiner.
4. Build issues the pending source atomically, inserting its address/fanout
   reservation before packet creation; clear the latch only after that succeeds.
   It can then claim a new line, seal its fanout, and refill the latch.
5. Retain the existing full terminal predicates. `boundedSourceResponsesComplete`
   already requires no pending source, no address reservation, no occupied
   response slot, no response words, and all packets received.

No source can issue before its complete Offset-chain scan is sealed, so the
post-scan legality rule is preserved. Multiple older sources may remain in
flight, which is already supported by the address-keyed
`virtual_source_reservations`, 128 response slots, out-of-order responses, and
per-slot fanout state.

### Replay result

A scheduling replay places each issue after its recorded scan duration (1,023
16-use lines at four cycles plus 12-use/three-cycle and 4-use/one-cycle edge
lines) and reuses each observed matched response latency without claiming that
memory latency remains unchanged under added load. It yields:

- current low-load latency vector: response HWM 15, terminal response at cycle
  4,145;
- r6 loaded latency vector: response HWM 83, terminal response at cycle 4,640.

Both are below the existing 128 response slots. The replay is a feasibility
test, not a performance forecast, but it proves that the one-entry latch can
create useful MLP without an additional ready queue. Its scan-limited issue
rate is 0.25 source lines/cycle while exact credit is available, versus the
observed current 0.0168 line/cycle. r6's response stream averages about 0.145
line/cycle, below the scan engine's production rate.

### Storage and timing cost

The fix adds no queue entry beyond the logically existing pending entry. A
hardware-minimized encoding at this exact geometry is approximately 345 bits:

- 25-bit physical line number for 2 GiB / 64-byte lines;
- 14-bit Offset head and 15-bit scan cursor including end sentinel;
- 15-bit expected and 15-bit observed logical-use counts;
- sixteen 15-bit exact per-word counters (240 bits);
- 5-bit unique payload-word count;
- 5-bit RT slice + 6-bit row + 3-bit entry claim token (14 bits);
- 2 state bits.

The C++ model already retains equivalent pending fanout/claim information. The
incremental hardware delta is a ready comparator, a Request-to-Build condition,
and at most one retry-armed bit. Timing remains one four-descriptor-wide scan
engine and at most one source issue per cycle; interior GZZ lines produce one
sealed source every four cycles, with the two aligned-envelope edge scans taking
one and three cycles.

## Exact unified-pool credit contract

Keep one 4,096-word payload RAM and no per-line data shadow. Queue/pending and
response entries hold metadata and word references only.

For a sealed source line, let `u` be the count of per-word fanout counters that
are nonzero. Issue is atomic only if:

`combine_words + outstanding_and_returned_source_reservations + u <= 4096`.

At response, allocate exactly `u` words from that already reserved capacity.
On a word's final logical use, transfer its existing word reference from the
response owner to the combiner owner; do not copy or allocate another word.
The response reservation decrements as the combiner ownership increments, so
total pool occupancy does not change. Backing issue/spill releases combiner
references exactly as in the current implementation. Preserve:

- `pool.used == combine_words + returned_source_payload_words`;
- `combine_words + reserved_response_words <= 4096` before and after every
  issue, response, transfer, rollback, spill, and write;
- one address reservation per issued line until response ownership is bound;
- zero line-shadow bytes;
- exact per-line reservation `u`, not 16 unless all 16 source words are used.

## Retry and event-safety audit

The no-extra-queue path is safe only with these rules:

- **One owner:** committing a RowTable claim transfers it to the pending latch.
  Scan-ready retries never re-claim it. Clearing occurs only after reservation
  insertion and packet creation are committed.
- **Exact wake:** while scan is incomplete, schedule the ready tick once (later
  calls may coalesce earlier events) and do not account the same scan wait more
  than once.
- **Drain first:** Request drains returned response work before checking the
  pending resume, respecting the four-word/cycle combiner budget and lookup
  ordering.
- **Credit retry:** a ready pending line that lacks credit remains pending.
  First attempt legal full-line drain or one existing partial spill. Recheck
  credit after a successful release. Otherwise wait for a source response,
  backing completion/port wake, or an explicitly armed one-cycle retry.
- **No silent deadlock:** if credit is unavailable but there is no response,
  write, staged drain, port retry, or spill capable of freeing credit, fail a
  progress assertion rather than poll forever.
- **Slot retry:** response-slot exhaustion waits for response-slot drain; it
  does not alter or duplicate the pending fanout.
- **Same-tick coalescing:** `scheduleExecuteInstructionEvent` already keeps the
  earliest scheduled event. New logic must not deschedule a response or write
  callback, and callbacks must continue to call `scheduleNextExecution(true)`.
- **Attribution:** close/account the Request interval before the early Build
  transition and add an explicit `fanout_overlap_resume` event with ready tick,
  in-flight sources, slot occupancy, reserved words, combine words, and cause.
- **Terminal closure:** terminal response remains governed by
  `boundedRetirementComplete`, never by the overlap-resume predicate.

Suggested focused counters are overlap resumes, scan-ready slot stalls,
scan-ready unified-credit stalls/cycles, retry wakes by cause, and pending-latch
HWM (which must remain one).

## Alternatives

### Fixed scan/ready queue

A one-scanner plus 32-entry ready FIFO also preserves legality: only sealed
fanouts enter Ready, and issue still performs the exact pool reservation. A
ready entry needs about 314 bits at this geometry (line, head, expected count,
16 counters, unique count, claim token, valid state). Thirty-two entries plus
one 345-bit scan context cost about 10,393 bits, or 1.27 KiB, plus roughly 20
bits of FIFO control. Using a conservative 58-bit physical line number instead
of the configured 25-bit line number raises the total to about 1.40 KiB.

With one scan engine it does not improve the unconstrained dense-GZZ issue rate
beyond 0.25 line/cycle. It can continue scanning while the ready head is blocked
by pool/slot credit, then issue a burst after credit returns. That robustness is
the only material advantage over the single latch, at a nonzero metadata and
multi-claim lifetime cost. Adopt it only if the no-queue micro records material
scanner idle time specifically behind ready-credit stalls.

### Incremental fanout construction

Updating fanout counters as descriptors enter Row/Offset state removes the
post-close scan, but strict legality still forbids A issue before global
admission closes unless a new per-line closure proof is added. It also requires
an accumulator for every simultaneously live source line and four counter RMW
updates/cycle with tag/hazard arbitration.

The irreducible accumulator is at least 261 bits/source (sixteen 15-bit
counters, observed count, unique count, sealed state), or 276 bits if expected
count is not already available. For this GZZ trace's 1,025 source lines that is
32.7--34.5 KiB. At the configured RowTable maximum of
32 slices x 64 rows x 8 entries it is 522--552 KiB. This is much larger than
the existing one-entry latch and does not address the erroneous Request drain
dependency by itself. It is rejected for the bounded first repair.

## Acceptance gates

### Focused micro gates

Use optimized and ASan/UBSan component tests; no application claim comes from
these alone.

1. Captured GZZ geometry (1,023 16-use chains plus 12-use and 4-use edge
   chains), four scan descriptors/cycle: every issue occurs after seal, each
   credit-free issue gap equals that line's `ceil(uses/4)` scan time,
   scans/words/cycles are exactly 1,025/16,384/4,096, and the single pending HWM
   is one.
2. Replay the captured current latency vector: response HWM must be 15 and all
   1,025 responses close exactly. Replay the r6 vector: HWM must be 83 and stay
   below 128. These are scheduler-model gates, not gem5 forecasts.
3. Duplicate fanout including one word used 16,384 times: reserve only the
   number of unique words; transfer each word exactly once on final use; a
   rollback restores its exact counter/reference.
4. Force 128-slot exhaustion, 4,096-word pool exhaustion, write-credit
   exhaustion, same-tick response/scan-ready events, out-of-order responses,
   and port retry. No duplicate claim, issue, response owner, transfer, free,
   or lost wake is permitted.
5. Randomized closure: at all steps pool occupancy is at most 4,096; at terminal
   pending, reservations, response slots, combiner, writes, and scoreboards are
   empty; line-shadow bytes remain zero.

### Matched GZZ screen and promotion gates

Run only after the overlapping IndirectAccess production owner releases or
coordinates the path. Build a fresh isolated current/fix pair with identical
guest SHA, input, checkpoint, Ramulator hashes, commands, configs, and all
non-treatment source. Require terminal return zero, `m5_exit`, nonempty final
stats, and exact fingerprint/reference correctness before timing.

Mechanism gate:

- descriptors 16,384; A issues/responses 1,025/1,025;
- shared transfers/rollbacks 16,384/0;
- capacity 4,096, HWM <= 4,096, line shadow zero;
- 1,024 full-line writes, zero partial writes, semantic/transport backing bytes
  both 65,536;
- exact B once, raw B retained zero, descriptor backing zero, replay zero,
  coherent ACK and order flags one, pages ready four;
- response-slot HWM at least 8 (target at least 16), average A issue rate at
  least 0.10 line/cycle, and overlap-resume events nonzero;
- no impossible bandwidth, phantom traffic, panic, fatal, retry livelock, or
  terminal residue.

Performance screen:

- B and consumer phases each within 1% of the fresh current control;
- A window no more than 50% of the current observed 19,086,114 ticks;
- backing window no more than 50% of the current observed 15,533,877 ticks;
- total strict ticks improve versus the fresh current control.

Promotion gate (three exact repetitions per arm, report all observations):

- median total strict ticks <= 1.10 x sealed r6 = 28,017,413;
- median A window <= 1.25 x sealed r6 = 2,799,003;
- median backing window <= 1.25 x sealed r6 = 2,667,151;
- every repetition passes every correctness, storage, legality, and mechanism
  gate. No speedup is claimed from the historical pair in this report.

## Terminal handoff

The shared-payload rollback owner completed and pushed checkpoint
`39bf4a80f147`; the implementation owner should integrate from that completed
baseline and preserve its exact final-use/rollback helper while first trying
the Request-side early Build resume with the existing pending latch. Do not add
a payload buffer, line shadow, second pool, or pre-scan issue path. Preserve the
exact fanout object and current unified-pool transfer semantics. Add focused
scheduler/retry counters and tests before any gem5 launch. Escalate to the fixed
32-entry ready FIFO only if controlled evidence shows credit-blocked scanner
idle time prevents the single latch from meeting the mechanism gate.
