# UME GZZ matched-consumer matrix

## Result

Fresh native16, native4, and strict logical16/physical4 runs used one frozen
simulator binary and equivalent MAA DIV/MUL page arithmetic. All three produced
the exact frozen output hash `7602200327591349891` with zero reference errors.

| Arm | `simTicks` | Relative conclusion |
|---|---:|---|
| native16 | 20,546,885 | performance ceiling |
| strict 16K/4K hybrid | 25,470,375 | 23.96% slower than native16 |
| native4 | 29,755,345 | hybrid is 1.168x faster |

The hybrid reduces latency by 14.40% versus native4 and recovers 46.53% of the
native4-to-native16 latency gap. This is the first GZZ result in this campaign
with matched consumer instructions; unlike the earlier reused-control ratios,
it supports performance attribution to tile geometry and virtualization.

Sealed evidence:
`/data1/nier/dx100-runs/2026-08-31-ume-gzz-matched-consumer-r6`

- Simulator SHA-256:
  `d3885ab0f0b84be5bce64c0fa81af97c3d1b84638e0e23bdcff95e25fcf493cc`
- Source commit recorded by the campaign: `f331383f`
- Same simulator binary and Ramulator configuration for all arms
- Fresh selector-specific checkpoints; all checkpoint identities remained
  immutable

## Strict mechanism

- 16,384 B indices admitted before the first A issue
- 16,384 retained Row/Offset descriptors
- 4,096-word shared source/combiner pool, high water 4,096
- Four coherent backing pages became ready
- 65,536 semantic backing bytes, exactly one result word per logical lane
- 66,368 transport bytes, 832 bytes (1.27%) above semantic minimum
- 1,011 full-line writes plus 26 masked writes
- The 26 masked writes represent 13 fragmented lines: one pressure spill and
  one completion fragment per line
- 1,037 write issues and 1,037 ACKs; no partial result was exposed before ACK

The partial fallback is required for general liveness. With arbitrary reordered
returns, a 4K combiner can hold almost 4K words spread across incomplete cache
lines, leaving too little room for another source response. Waiting for a full
line can deadlock. The implementation spills a populated partial line to
coherent backing only under source-credit starvation, then tracks that line
until its remaining fragment is acknowledged.

## Hardware caveat

The shared data pool remains bounded to 4,096 words. Tracking pressure-spilled
lines requires persistent identity metadata. A direct bitmap for this 16K
logical window is 1,024 bits (128 bytes) per indirect unit; this may be folded
into existing per-line retirement/page-readiness metadata, but that accounting
still needs an explicit hardware report. The C++ response arrays are host-side
simulation storage; the modeled hardware assumes compact useful-word storage
and fanout counts, not one physical cache line per software response object.

The successor replaces host-dynamic spilled-line identity with that fixed
bitmap and charges it in `report_maa_storage.py`. Candidate-only replay at
commit `8ac798e4` with simulator SHA-256
`cd36ea5acd0ee660ae66ba384cdef0acad265d48acc73e62bd2b13a2f161b8d0`
exactly reproduces 25,470,375 ticks, the output hash, 1,037/1,037 write closure,
and all strict counters. The sealed r6 matrix remains the same-binary
performance authority.
