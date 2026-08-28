# Strict CG feeder-depth independent review (2026-08-28)

## Handoff

Review target: commit `8a3484fd1f7e20fed1f678defcb17f4f3472c2b1`
(`analysis: accept strict feeder performance point`). No gem5 run was launched and
no evidence root or mechanism source was changed.

**ACCEPT the bounded simulation result and its two-factor attribution.** The
sealed `simTicks` values and all reported one-line-relative ratios recompute.
At NA1024, feeder depth is the larger treatment and masked-line retirement
still gives a distinct gain at depth 64. The selected 64-line/masked arm has
exact CG output, 65 complete P/Q/whole windows, complete write ACK closure,
and direct trace evidence that all 16K descriptors precede A issue.

**ACCEPT 64 lines only as the fastest measured NA1024 point, not as a
cost/performance optimum or architecture default.** Eight lines is the first
visible knee. Moving from 8 to 64 uses 8x the feeder payload (2 KiB to 16 KiB
over four configured indirect units) for 2.0239% lower NA256 latency and
3.0848% lower NA1024 latency. There is no NA1024 128-line observation, no
synthesis, and no full-CG or native4 comparison.

**ACCEPT the 16 KiB calculation only as reserved semantic payload.** It is not
complete feeder accounting. Tags, per-line pending/ready/ownership state,
request credits/MSHRs, queue/arbitration logic, SRAM ports/periphery, muxing,
Fmax, power, and physical design remain unaccounted.

**REJECT two stronger readings of the report.** The data do not establish that
64 is preferable to the 8-line knee under any hardware-cost objective, and the
NA256 backing phase is not literally unchanged: the sealed aggregate counter
ranges from 281,794 to 281,819 cycles. It is materially invariant (a 25-cycle,
0.0089% range), so the report should say "effectively unchanged."

**PENDING:** seal the intermediate arms' commands, resolved configs, restore
logs, and strict traces; run the matched full-CG gate; obtain a
provenance-matched native4 result; and perform a real control/data storage and
timing/power evaluation before promotion. If 64 is to be called a speed
optimum rather than the fastest point tested at NA1024, measure 128 at NA1024.

## Evidence integrity and comparability

The committed 80-entry sweep ledger has the stated SHA-256
`374bf24911c37cbeeb06c713f243b8011a3c1a0d6d137b8785e32e356f8cf5cd`.
Every entry passes `sha256sum --check`. The two predecessor matched roots also
pass their complete raw ledgers:

- NA256: 51/51 entries; ledger SHA-256
  `dba7690db2dbb2f6d87f4f3e791893a03f66602d8015c55bef259094bddc56b7`.
- NA1024: 52/52 entries; ledger SHA-256
  `fdb76b36420c7e2b2645465d4652542cf59ebf6b7b3dd7093ec647a411aaf5cc`.

All sweep results name gem5 SHA-256
`4c07d55ffb8528483f1b7cfe629301b23ac23c4c4679a15bfc7b1972c54f2ccd`.
The referenced file still has that hash. Guest hashes are constant within a
problem size (`f2d0169a...` at NA256 and `20335fcdb...` at NA1024), and both
guests are included in the verified predecessor ledgers. The NA1024 factorial
commands normalize to the same SHA-256 after removing only the output path,
`virtual_index_buffer_lines`, and `virtual_masked_writes`. The four source
commit labels differ because analysis/runner commits advanced between runs;
there is no diff in `src/mem/MAA` or the MAA option plumbing between the
oldest sweep source commit and `8a3484fd`, and the executable hash is identical.

For each of the four NA1024 factorial endpoints, sealed logs directly show
wrapper exit 0, exactly one `m5_exit`, one passing fingerprint, one passing
logical16 terminal, nonempty final stats, and no fatal text. Their sealed
fingerprint/terminal/11-reduction streams have the same digest.

The sweep ledger is weaker for intermediate capacity arms. It seals
`gate.complete`, `restore.log.exit`, `result.json`, and `stats.txt`, but not
`command.json`, `config.ini`, `restore.log`, or `strict_trace.log`. The live
unsealed files currently resolve to the named line depth with strict mode and
masked writes enabled, and their traces reproduce the phase totals below, but
that treatment and raw-trace provenance cannot be re-established solely from
the committed ledger. The runner also records the CLI depth in `result.json`
without checking the resolved `virtual_index_buffer_lines` value in
`config.ini` (`run_cg_strict_line_combined.py:188-199,279`). This does not
invalidate the sealed timing table, but it prevents the intermediate-arm raw
causality narrative from receiving the same evidence grade as the four
factorial endpoints.

## Recomputed capacity ratios

“Lower” is independently recomputed as
`100 * (1 - candidate_simTicks / one_line_simTicks)`. The source values are the
sealed `result.json`/`stats.txt` files named by the committed ledger.

| Feeder lines | NA256 `simTicks` | NA256 lower vs. 1 | NA1024 `simTicks` | NA1024 lower vs. 1 |
|---:|---:|---:|---:|---:|
| 1 | 395,548,742 | 0.0000% | 2,213,855,573 | 0.0000% |
| 2 | 308,636,780 | 21.9725% | not run | - |
| 4 | 267,179,304 | 32.4535% | 1,387,035,399 | 37.3475% |
| 8 | 251,554,970 | 36.4035% | 1,289,047,306 | 41.7736% |
| 16 | 251,025,687 | 36.5374% | 1,282,365,382 | 42.0755% |
| 32 | 250,163,059 | 36.7554% | 1,267,190,829 | 42.7609% |
| 64 | 246,463,712 | 37.6907% | 1,249,282,534 | 43.5698% |
| 128 | 246,214,877 | 37.7536% | not run | - |

The additional checks also match the report: 64 is 3.08481867% lower than 8
at NA1024, 128 is 0.10096212% lower than 64 at NA256, and 64 is 2.02391469%
lower than 8 at NA256.

## NA1024 2x2 attribution

| Feeder | P retirement | `simTicks` | Conditional result |
|---:|---|---:|---:|
| 1 | 4-byte word | 2,386,167,394 | control |
| 1 | masked 64-byte line | 2,213,855,573 | 7.2213% lower than 1-line word |
| 64 | 4-byte word | 1,417,918,170 | 40.5776% lower than 1-line word |
| 64 | masked 64-byte line | 1,249,282,534 | 11.8932% lower than 64-line word |

The combined arm is a `2.386167394 / 1.249282534 = 1.91003022x` latency
speedup over the one-line word control. Holding masked retirement fixed, the
64-line feeder is 43.5698% lower latency than one line. Thus the report's
ranking is correct: feeder depth is the larger effect, and line combining has
a measured benefit after the feeder change.

The effects should not be described as statistically or algebraically
independent: the masked-line percentage is 7.2213% at one line and 11.8932%
at 64 lines. This is a valid factorial separation with a treatment interaction,
not an additive-effect proof. There is one deterministic observation per arm.

Across all four arms, sealed stats conserve 130 strict operations, 133,120 B
line fetches, 2,129,920 descriptor insertions, 4,587 A issues, 520 ready pages,
1,064,960 Q selections/applies/admitted words, 65 Q operations, and 66,560
publisher issues/responses. All arms close 65 P, 65 Q, and 65 whole windows
with identical output/reduction evidence. The intended P-write factor changes
only the retirement representation: 1,064,960 four-byte writes in word mode
versus 358,114 64-byte masked-line writes. The selected trace contains 358,114
matching write completions, no non-64-byte P issue, and 260 product-page
responses.

## What `virtual_index_buffer_lines` actually adds

The source supports the narrow mechanism description:

1. `MAA.py:156-159` defines the knob as lines “buffered or in flight.”
   `MAA.cc:733-752` passes the same configured value into every indirect unit.
2. `IndirectAccess.cc:941-1050` fills a sequential B/index window while
   `pending_lines + ready_lines < line_capacity`. Each entry creates one
   ordinary 64-byte `MemCmd::ReadReq`; `IndirectAccess.cc:9038-9070` sends it
   through the existing timed MAA cache/memory routing. Downstream port
   acceptance and memory response timing remain modeled.
3. On response, `IndirectAccess.cc:3155-3212` removes the pending line, marks
   the line ready, and expands its sixteen 32-bit words into private
   `DirectIndexWord` records. A ready line retains ownership until all of its
   words are consumed and erased (`IndirectAccess.cc:3067-3154`). The knob
   therefore adds concurrent read ownership/credits plus response retention;
   it does not widen the B word or descriptor semantics.
4. The selected sealed traces reach `b_queue_high_water=64` in every one of
   the 65 P windows; both one-line controls reach exactly one. The treatment is
   active, not merely configured.

The simulator representation is intentionally not an RTL storage model.
`IndirectAccess.hh:342-349,491-497` uses dynamic maps and one record per live
word. On this 64-bit build `DirectIndexWord` is 32 bytes, so a densely ready
64-line/four-unit host representation alone can reach
`64 * 16 * 32 * 4 = 128 KiB`, before map/vector node overhead. This host
footprint should not be charged as target hardware, but it also does not prove
that a target feeder consists only of data bits.

The report's target payload arithmetic is correct as a lower bound:

- one line: `1 * 64 B * 4 = 256 B`;
- eight lines: `8 * 64 B * 4 = 2 KiB`;
- 64 lines: `64 * 64 B * 4 = 16 KiB`;
- one-to-64 delta: 16,128 B; eight-to-64 delta: 14 KiB.

The visible SPD payload saving is also arithmetically correct:
`(16384 - 4096) * 4 B * 32 tile IDs = 1,572,864 B`, and
`16,128 / 1,572,864 = 1.025390625%`. Comparing payload bits is useful, but it
is not an iso-area comparison because the feeder's control, ports, and timing
costs differ from SPD data-array bits.

## Full-16K reorder and conserved work

The full-window ordering claim is source grounded. `MAA.cc:383-414` rejects
strict mode unless logical/physical geometry is 16K/4K, RowTable reordering is
enabled, all 16K Offset entries can be retained, and replay/drain alternatives
are disabled. `IndirectAccess.cc:6467-6505` additionally requires one complete
stride-1 16K span and enough RowTable slots. At fill closure,
`IndirectAccess.cc:6928-6960` requires cursor and Offset occupancy to equal
16K and all feeder maps to be empty. Build cannot open until that admission is
closed (`IndirectAccess.cc:7025-7034`). Finally,
`StrictTwoPhaseReference.hh:172-203` rejects admission closure before all
16K words/descriptors arrive and rejects any A issue before closure and the
last Row/Offset insertion.

The selected 64-line/masked sealed trace supplies matching runtime evidence:
65/65 P timing rows have `logical=16384`, `b_words=16384`,
`descriptors=16384`, `exact_b_once=1`, `raw_b_retained_bytes=0`,
`descriptor_backing_bytes=0`, `replay_passes=0`, `coherent_ack=1`,
`order_ok=1`, and `terminal=1`. All 65 whole-window rows have
`p16_reorder=1`, `q16_reorder=1`, `direct4=0`, `drains=0`, `fallbacks=0`,
`order_ok=1`, and `terminal=1`. This supports a full-16K reorder-window claim,
not a completed full-application or native speedup claim.

The NA256 P-only conserved-work numbers are also consistent with sealed stats
and the strict source invariants: ten windows imply 163,840 B words and
descriptors and 655,360 semantic bytes; subtracting the sealed Q counters from
the aggregate strict counters gives 168 matched P A issues/responses and
26,672 matched P backing issues/ACKs in every arm. The currently present raw
traces reproduce all of those values and the report's exact B-fetch totals.
However, those NA256 traces are not in the committed sweep ledger, so the
exact per-phase tick decomposition remains pending stronger sealing.

## Why eight is the defensible knee

Eight lines captures most of the observed benefit: 36.4035% at NA256 and
41.7736% at NA1024 relative to one line. Beyond eight, the NA1024 marginal
reductions are 0.5184% (8 to 16), 1.1833% (16 to 32), and 1.4132% (32 to 64).
The last step is not saturated, while the unmeasured 64-to-128 NA1024 step
could behave differently from NA256. Therefore 64 is simply the fastest
NA1024 depth measured.

A later decision may still prefer 64 if full-CG benefit survives and the 14
KiB incremental payload plus control is cheap under a stated area/power/Fmax
budget. On the current evidence, eight is the defensible cost/performance knee
and 64 is a provisional CG speed point. Rejecting 128 as a cost-driven sweep
stop is reasonable; rejecting it as an NA1024 performance candidate is not yet
evidence based.

## Validation performed

- `sha256sum --check` on all 80 committed sweep-ledger entries: PASS.
- `sha256sum --check` on both predecessor raw-root ledgers (51 and 52
  entries): PASS.
- Seven strict source/runner contract checks executed directly: PASS.
- `experiments/scripts/strict_two_phase/run_reference_unit.sh` optimized and
  ASan/UBSan modes: PASS.
- Six cross-application strict/line-combined `unittest` cases: PASS.
- Review-only `git diff --check`: PASS.

`pytest` was unavailable in the environment, so the pytest-style strict
contract functions were imported and executed directly; the standard-library
test suite was run with `python3 -m unittest`. No validation command launched
gem5.
