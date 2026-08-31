# Selected complete-line hybrid (2026-08-30)

## Mechanism

1. Read all B indices for a logical 16K window into the existing 16K
   Row/Offset reorder engine.
2. Issue reordered scattered A requests. Returned words first occupy a bounded
   1,024-word response pool.
3. A four-start/four-completion, three-cycle lookup maps each logical result
   word into a 2,048-tag, 8-way XOR7 line combiner. Four banks allow one
   lookup/update per bank per MAA cycle.
4. The combiner retains fragments privately in 3,072 useful-word storage until
   a destination cache line is complete.
5. A bounded 16-page ready queue selects complete lines. A 32-byte/cycle,
   32-bank payload RAM assembles each 64-byte line with at most one word read
   per bank per cycle. At most one completed line per MAA cycle is issued
   coherently to LLC/backing; the exact final tail is the only permitted
   partial write.
6. WriteResp closes ownership/readiness. A later CPU/direct consumer reads the
   coherent backing result.

The response and combiner payloads are bounded by a 4,096-word physical result
budget. XRAGE uses 3,584 FP64 words and leaves 512 words unused; FLAG uses the
full 4,096. The full 16K Row/Offset engine remains physical, so this
virtualizes result storage while preserving the 16K reorder window. It is not
yet a 4K Row/Offset design and cannot transparently provide 64K reordering.

## Current evidence

- XRAGE gather0: 37,401,309 ticks with four combiner banks, finite lookup,
  bounded ready selection, and a 32-byte/cycle/32-bank payload port;
  timing-equivalent to conflict-free payload copy and exact output.
- 14 FLAG gathers: selected XOR8 is 7.463% below fused16 and tied with
  compact16 (-0.026%) on the same pre-lookup binary.
- FLAG lookup latency 3: +0.155% geometric-mean overhead versus same-binary
  latency 0.
- FLAG bounded ready queue: -0.0002% versus scan mode; exact all-14 closure.
- XRAGE four-bank insertion ports: +0.310% versus unbanked and 11.592% below
  native16; one bank is rejected.
- Across 14 FLAG gathers, the 32-byte/cycle payload port adds 0.003% geometric
  mean and at most 0.079% versus ideal copy, with zero payload backpressure.

The speed comes from avoiding fragmented partial-line backing writes while
retaining the original 16K reorder scope. It does not come from the safety
guard, which is timing-identical when disabled at the same capacity.

This selected payload-port result applies to the direct-gather complete-line
path. CG mostly emits partial-mask lines, IS/HashJoin do not execute this
virtual-result edge, and SSSP has a separate old-result publisher. It is not a
suite-wide retirement-port result.

A default-off masked-line extension now charges CG's partial lines through the
same 32-byte port. It is exact but adds 4.658% at `CG_NA=256` and 9.538% at
`CG_NA=1024`. Four active identities improve the larger case by only 0.063%,
so the selected point retains one identity. This extension is evidence, not
the selected direct-gather path.

Physical payload banking is now modeled. Thirty-two banks add 0.203% over
conflict-free payload reads at `CG_NA=1024`, for 9.760% total overhead versus
ideal copy. Sixty-four banks save only another 0.234% while doubling bank
count; 32 is selected. See `payload_bank_study_2026-08-30.md`.

The selected one-line control allocation is physically specialized in the
simulator rather than backed by inactive entries. Final evidence also closes
payload work at word granularity: scheduled words equal read words and the
selected one-line shared-port cycles equal summed per-line demand.

## Hardware boundary

Bounded now: result payload, tags/ways, response pool, lookup starts and
completions, lookup latency, combiner banks, aggregate payload-read bandwidth,
payload RAM bank count and one-read-per-bank conflicts, write credits, drain
width, ready selection, exact ACK identity, and complete-line/tail legality.

Still open: synthesized bank decoder/periphery/mux area and timing, same-set
hazards, reset/epoch implementation, CPU/virtual-alias coherence, and
area/energy/Fmax. Overlapping live MAA producers are now rejected
until prior page ACK closure; software still owes exclusive destination
ownership against CPU/alias writes.

The lookup pipeline duplicates no data, but its bounded identity-token metadata
is not yet in the storage ledger. Measured peak is 12 tokens per logical
operation at latency 3; source's 1,024-token ceiling is a fail-safe bound, not
the selected hardware size.
