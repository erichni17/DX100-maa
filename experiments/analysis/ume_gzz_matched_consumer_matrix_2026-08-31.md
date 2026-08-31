# UME GZZ matched-consumer matrix

## Scope

This is a deterministic reduced-input application gate: `n=16,384` with
196,384 padded output elements. It executes the real GZZ kernel and exact
reference, but it is not the separate 1M-element full-scale GZZ campaign.

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

The fixed-active-RowTable storage ledger reports a 1,953,744-byte comparable
lower bound for the hybrid versus 3,176,448 bytes for native16: 38.49% lower.
It is 40.39% larger than native4's 1,391,616-byte lower bound. These are packed
capacity estimates from the simulator configuration, not synthesized area.

Sealed evidence:
`/data1/nier/dx100-runs/2026-08-31-ume-gzz-matched-consumer-r6`

- Simulator SHA-256:
  `d3885ab0f0b84be5bce64c0fa81af97c3d1b84638e0e23bdcff95e25fcf493cc`
- Source commit recorded by the campaign: `f331383f`
- Same simulator binary and Ramulator configuration for all arms
- Fresh selector-specific checkpoints; all checkpoint identities remained
  immutable

The native controls reproduced bit-for-bit timing in six independent campaign
attempts: native16 was always 20,546,885 ticks and native4 was always
29,755,345. The strict r6 arm and the fixed-bitmap successor replay both
produced 25,470,375 ticks with identical mechanism counters and output. The
failed strict attempts are retained as engineering diagnostics, not included
as performance samples.

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

## Remaining bottleneck

The end-to-end gap to native16 is 4,923,490 ticks, or 15,730 MAA cycles at the
configured clock. The MAA total-cycle counter is also exactly 15,730 cycles
higher: 81,375 for the hybrid versus 65,645 for native16.

| Counter | native16 | strict hybrid | Delta |
|---|---:|---:|---:|
| MAA total cycles | 65,645 | 81,375 | +15,730 |
| MAA busy cycles | 58,551 | 73,331 | +14,780 |
| stream SPD-write access cycles | 7,174 | 21,865 | +14,691 |
| indirect-read cycles | 19,094 | 22,905 | +3,811 |
| indirect-RMW cycles | 28,339 | 33,049 | +4,710 |

These counters overlap, so the stream delta is not an additive causal proof.
It is nevertheless 93% of the total gap's magnitude and is far larger than the
1.27% backing-transport overhead. The next optimization should overlap page
materialization into an alternate physical tile with computation/RMW on the
current page. The configuration already provisions eight tiles per core and
the matched consumer currently uses seven, so a one-tile ping-pong experiment
does not increase configured tile capacity.

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
