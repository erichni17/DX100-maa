# Virtualization selected-integration checkpoint (2026-08-26)

## Selected full CG point

The best correctness-gated full CG point is direct4/q16 with retained value
lines and four active apply lanes:

- certificate:
  `/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-lane4-full-certificate-r1`;
- `111,116,739,967 simTicks`;
- 10.3673% lower latency than cache-on lane 1 (`123,968,991,971`);
- 57.2375% lower than the older fully bounded4 result
  (`259,846,205,097`);
- still 44.1664% slower than historical native4 (`77,075,327,902`).

It uses eight 4K-word tiles/core over four cores (524,288 B physical SPD),
262,144 B coherent product backing, no virtual-p or q-index backing, q-side
16K Row/Offset reorder, and four physical p gathers (`p16=false`, `q16=true`).
Value retention and lane activation add no payload/ports because the fixed
128-line value-owner and four-lane pools already exist.

## Full-reorder comparison

The accepted page-fed cache-on p16/q16 full point is
`162,849,334,269 simTicks` at
`/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-value-cache-full-r1`.
It preserves both p16 and q16 but retains 524,288 B external backing split
between virtual-p and products. Direct4 is 23.8750% lower latency, but that
delta combines p-backing removal with loss of p16 reorder and must not be
attributed to either alone.

The p16/q16 cache-on lane-4 full successor is accepted at
`158,381,418,273 simTicks`, 2.7436% below lane 1. Its certificate root is
`/data1/nier/dx100-runs/2026-08-26-cg-page-fed-p16q16-value-cache-lane4-full-certificate-r3`.
The modest full gain confirms that virtual-p materialization, not q apply-lane
occupancy alone, dominates this design point.

## Accepted optimizations

- Value retention removes repeated coherent product reads. At NA=1024 it
  reduces value reads 1,064,960 -> 66,862 and direct4 latency
  3,768,724,702 -> 837,625,247 ticks (4.4993x). At full scale it improves
  cache-off direct4 by 5.5264x.
- Four active apply lanes improve cache-on direct4 by 11.6550% at NA=256,
  10.2906% at NA=1024, and 10.3673% at full scale.
- On p16/q16 page-fed, lane 4 improves 5.3580% at NA=256 and 3.4158% at
  NA=1024. Context stalls are performance evidence, not conserved work.

## Fused p16 successor

The repaired guarded fused-p16 producer preserves p16 while removing the
262,144-B virtual-p backing. It reuses existing Row/Offset ownership,
coefficient-line owners, one timed ordinary FP32 MUL lane, bounded product
combining, and exact WriteResp completion.

Fresh NA=256 evidence is exact and 5.5422% faster than page-fed p16/q16:
`396,154,397` versus `419,398,090 simTicks`. The candidate adds 140 semantic
bytes / 392 conservative bytes of system control state, zero payload queues,
zero external ports, and no new multiplier. Hardened micro/CG ledgers close
every formerly missing zero-stat and immutable-root requirement.

The guarded NA=1024 confirmation at
`/data1/nier/dx100-runs/2026-08-26-cg-fused-p16-q16-na1024-r1` was stopped on
August 27 at 18:27 EDT after the service consumed 24 h 22 min of CPU time.
The candidate entered ROI but produced no terminal stats, `m5_exit`, output
fingerprint, or progress marker; its log had not advanced since August 26 at
18:23 EDT. It is **rejected as nonterminal diagnostic evidence**, not treated
as a correctness or performance result. The accepted NA=256 point therefore
remains the only CG evidence for this fused successor, and no full fused run
is authorized.

## Rejected optimization

The eight-tile producer ping-pong schedule is rejected. Its repaired candidate
failed to terminate after advancing at least 382x the serial simulated span;
it supplied no overlap or performance evidence. The implementation remains on
its isolated worker branch, while only the rejection report is integrated.

## Cross-application status

- CG: selected lane-4 full certificate passes.
- NAS IS: full official correctness passes; performance is not promoted.
- HashJoin PRO/PRH: hardened full exact correctness passes; archived native
  timing is not provenance-comparable.
- SSSP S22: durable full coherent-fallback run remains active at
  `/data1/nier/dx100-runs/2026-08-25-sssp-coherent-full-s22-r2`; no gate or
  performance claim yet.

Read-only completion audit r6 passes CG, IS, PRO, and PRH and remains
`INCOMPLETE` solely because SSSP has no terminal gate. No native baseline was
rerun during this checkpoint.
