# FLAG 8-way XOR complete-line result (2026-08-29)

## Decision

Accept 8-way/XOR-shift-7 as the selected cross-workload complete-line index.
Across all 14 recovered FLAG gathers it closes exact output and full-line-plus-
tail work with geometric-mean latency 0.004% below the same-binary 16-way
control. The timing is effectively identical while lookup associativity and
selected-way mux fan-in are halved.

## Fixed comparison

Both matrices use:

- source `3d28c6493ef26cdca1d0fa868e56d6cf97236bd9`;
- gem5 SHA-256
  `ed75a5543a693242580130e47665d2f4fcab8ee9cf2f7f20174ae648603c4e4e`;
- logical16K/physical4K, 2,048 line tags, 3,072 combiner words, 1,024
  response words, and drain width 1;
- the same verifier/input per case, with byte-identical checkpoint physical
  memory for each of the 14 pairs; and
- no timeout.

Only associativity and set indexing differ:

| Arm | Ways | Set map | Sets |
|---|---:|---|---:|
| Control | 16 | low-bit/modulo | 128 |
| Selected | 8 | `line ^ (line >> 7)`, then modulo | 256 |

## Result

The selected arm's per-case latency change ranges from 0.063% lower to 0.004%
higher. Ten cases are tick-identical; equal-weight geometric-mean latency is
0.004% lower. These differences are scheduling-order effects, not a speedup.

The final same-binary application comparison is:

| Comparator | Selected XOR8 latency change |
|---|---:|
| fused16 | **-7.463%** |
| compact16 | **-0.026%** |
| 16-way complete-line | **-0.004%** |

XOR8 is faster than fused16 in every case. Against compact16, per-case wins
and losses remain mixed; the geometric-mean result is a tie.

Every arm/case closes:

- the exact output hash;
- `floor(length / 8)` complete FP64 lines plus one exact final tail;
- write issue/WriteResp equality;
- the configured one-line-per-cycle drain peak bound; and
- terminal checkpoint, restore, final stats, and `m5_exit`.

The older shift-7 campaign found the full-line liveness bug: one configuration
reached final drain with a complete `0xff` line while all 64 write credits were
occupied. Source incorrectly sent that full line through the illegal-partial
check. Commit `3d28c649` keeps a credit-blocked full line resident and retries
after an ACK. The final 14+14 comparison uses only the corrected binary.

## Hardware interpretation

Tag count and useful result payload are unchanged. The selected organization
halves comparisons per insertion lookup from 16 to 8 and halves selected-way
mux fan-in, at the cost of a fixed XOR fold and twice as many sets. The 2,048-
tag FLAG geometry has 256 power-of-two sets at 8 ways, so the modulo reduces to
low set bits after folding.

This is still not a timed lookup or synthesis result. Comparator delay, four
parallel lookup/update lanes, same-set hazards, payload/reference RAM ports,
ready-line selection, and reset remain explicit gates.

A same-binary successor adds a three-cycle, four-start/four-completion lookup
pipeline and increases all-14 geometric-mean latency by only 0.155%, with exact
token and output closure. See `flag_lookup_latency_results_2026-08-30.md`.

## Provenance

- selected XOR8 root:
  `/data1/nier/dx100-runs/2026-08-29-flag-xor8-complete-line-r2`;
- matched 16-way root:
  `/data1/nier/dx100-runs/2026-08-29-flag-current16-drain1-r1`;
- matched fused16/compact16 root:
  `/data1/nier/dx100-runs/2026-08-29-flag-current-fused-compact-r1`;
- paired summary: `.../flag-xor8-complete-line-r2/summary/flag_xor8.md`;
- final current-control summary:
  `.../flag-xor8-complete-line-r2/current_comparison/flag_complete_line.md`;
- artifact ledger: `flag_xor8_artifacts_2026-08-29.sha256`.

The XOR8 campaign wrapper exited 2 after xargs despite all 14 terminal rows;
the result was recovered fail-closed only after checking every checkpoint,
restore, verifier pass, resolved ways/shift/drain config, line/tail count, and
row. `campaign.recovered.pass` preserves that distinction. The dispatcher was
subsequently hardened at `5fa23eae`.

## Next gate

Model an 8-way lookup pipeline and finite payload/reference ports. Then test a
small competing-coherence case or explicitly require exclusive destination
ownership until final WriteResp.
