# SoA/JIT bounded-capacity optimization (2026-08-14)

## Result

The existing logical-16K, physical-4K SoA/JIT RMW path was not replaced.
Two bounded latency-hiding structures were added to it:

- a fixed 16-line predicate feeder, with 1 or 16 active credits; and
- a fixed 32-line value-owner/coalescer pool, with 4, 8, 16, or 32 active
  lines.

With eight A-line contexts, eight ordered lookahead slots per context, and
eight direct-index lines, the combined 16-predicate/32-value treatment reduced
the focused exact test from 251,020,366 to 52,211,843 `simTicks`, a **4.8077x
speedup** (79.20% latency reduction).

| Predicate credits | Value owners | `simTicks` | Speedup vs. 1/4 |
|---:|---:|---:|---:|
| 1 | 4 | 251,020,366 | 1.0000x |
| 1 | 8 | 191,321,250 | 1.3120x |
| 1 | 16 | 162,553,733 | 1.5442x |
| 1 | 32 | 151,408,742 | 1.6579x |
| 16 | 4 | 153,713,048 | 1.6330x |
| 16 | 8 | 95,726,355 | 2.6223x |
| 16 | 16 | 64,963,776 | 3.8640x |
| 16 | 32 | 52,211,843 | 4.8077x |

This is not an end-to-end GZP or CG result. The historical exact native-16K
API controls are about 18.3M-18.4M ticks, so the best arm remains far above
the single-digit-overhead target. It establishes that bounded concurrency
removes a real implementation bottleneck, not that the design is complete.

## Correctness and provenance

All eight arms restored the same checkpoint, used the same frozen gem5 and
guest binaries, and changed only predicate credits and active value owners.
Every arm had wrapper status zero, one ROI terminator, one exact `m5_exit`, two
closed SoA/JIT generations, and no panic/fatal/error marker. Every arm
reported:

```text
output_hash=2761840269561229581
errors=0
selected=29689
rejected=3079
```

Value-read issue/response counts and all terminal ledgers closed exactly.
Raw commands, configurations, traces, statistics, hashes, and the validated
table are under:

```text
/data1/nier/dx100-runs/2026-08-14-soa-jit-capacity-combined-fbec9dbe-r1
```

The source commit was `fbec9dbe935c7b72d7a9a668b733f6cc156ca4d0`.
The frozen gem5 SHA-256 was `a50c167d...f8c8404`; the guest SHA-256 was
`c7fb4f8d...9c497`.

## Why it improved

The 1/4 control repeatedly stopped on two independent resources:

- one predicate cache line could be in flight, so the 16K row-fill phase
  exposed cache latency; and
- four non-evictable value lines quickly filled, so ordered aliases could not
  progress while their responses were outstanding.

At 16/32, predicate stalls fell from 4,092 to 117 and value stalls fell from
2,872,154 to 114,170. Value reads also fell from 27,826 to 22,280 because the
larger owner pool merged more aliases behind the same cache-line request.

The remaining best-arm stage totals across two operations are approximately
33.3M fill ticks and 17.4M request ticks. Predicate/index ingestion therefore
dominates after value-owner pressure is reduced; the request phase still has
114,170 value/lookahead stalls and 51,962 A-context stalls.

## Bounded hardware charge

Per indirect unit, the treatment physically provisions:

- 16 predicate lines: 1,440 modeled bytes (1,536 C++ host bytes);
- 32 value-line payloads: 2,048 bytes;
- the complete value-owner object: 3,888 C++ bytes including tags/state;
- eight A/lookahead contexts: 3,328 C++ bytes;
- one ordered apply arbiter: 24 C++ bytes; and
- eight direct-index lines: 4,096 bytes of active data/tag state.

The trace separately reports modeled payload and compiled state-object sizes;
runtime credit selection does not shrink the fixed physical provision.

## Next gate

1. Measure the optimized volume RMW in full fixed-input GZP.
2. Complete the response-acknowledged SPD-to-LLC publisher so the gradient RMW
   no longer relies on correctness-only CPU staging.
3. Profile fill-stage cache throughput and request-stage context pressure on
   the application before increasing either fixed structure again.
