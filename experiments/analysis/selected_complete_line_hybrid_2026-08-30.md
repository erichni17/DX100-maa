# Selected complete-line hybrid (2026-08-30)

## Mechanism

1. Read all B indices for a logical 16K window into the existing 16K
   Row/Offset reorder engine.
2. Issue reordered scattered A requests. Returned words first occupy a bounded
   1,024-word response pool.
3. A four-start/four-completion, three-cycle lookup maps each logical result
   word into a 2,048-tag, 8-way XOR7 line combiner.
4. The combiner retains fragments privately in 3,072 useful-word storage until
   a destination cache line is complete.
5. A bounded 16-page ready queue selects complete lines. At most one line per
   MAA cycle is written coherently to LLC/backing; the exact final tail is the
   only permitted partial write.
6. WriteResp closes ownership/readiness. A later CPU/direct consumer reads the
   coherent backing result.

The response and combiner payloads total exactly 4,096 FP64 words: a 4K
physical result budget. The full 16K Row/Offset engine remains physical, so
this virtualizes result storage while preserving the 16K reorder window. It is
not yet a 4K Row/Offset design and cannot transparently provide 64K reordering.

## Current evidence

- XRAGE gather0: 37,291,759 ticks with finite drain, lookup latency 3, and
  bounded ready selection, 11.865% below native16; exact output.
- 14 FLAG gathers: selected XOR8 is 7.463% below fused16 and tied with
  compact16 (-0.026%) on the same pre-lookup binary.
- FLAG lookup latency 3: +0.155% geometric-mean overhead versus same-binary
  latency 0.
- FLAG bounded ready queue: -0.0002% versus scan mode; exact all-14 closure.

The speed comes from avoiding fragmented partial-line backing writes while
retaining the original 16K reorder scope. It does not come from the safety
guard, which is timing-identical when disabled at the same capacity.

## Hardware boundary

Bounded now: result payload, tags/ways, response pool, lookup starts and
completions, lookup latency, write credits, drain width, ready selection,
exact ACK identity, and complete-line/tail legality.

Still open: physical tag/payload/reference RAM banking and ports, same-set
hazards, reset/epoch implementation, CPU/second-producer coherence or an
exclusive destination contract, and synthesized area/energy/Fmax.
