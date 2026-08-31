# Shared-payload cross-application applicability

## Decision

Do not rerun CG, IS, HashJoin, or SSSP solely for the GZZ shared-payload
changes. Their selected hybrid instructions use separate engines and do not
execute the generic virtual response/combiner path changed here.

| Workload | Selected candidate path | Shared-payload patch applies? |
|---|---|---|
| NAS CG | page-fed P16/Q16 SoA/JIT | no |
| NAS IS | scalar SoA/JIT RMW | no |
| HashJoin PRO/PRH | scalar SoA/JIT RMW | no |
| GAPBS SSSP | old-result SoA/JIT RMW | no |
| UME GZZ | virtual indexed load plus page materializer | yes; accepted |
| API indirect load | generic virtual indexed load | yes |
| XRAGE/FLAG gathers | generic complete-line/direct consumer | yes |

This is an instruction-path boundary, not a claim that the four excluded
applications are finished research targets. Their existing candidate results
remain governed by their own correctness and performance gates:

- CG's strict-bit ablation is flat; page-fed publication/consumer overhead is
  the unresolved bottleneck.
- IS full scalar-SoA evidence is terminal-valid but does not test generic
  gather result storage.
- Hardened HashJoin PRO/PRH are terminal-valid and performance-negative.
- SSSP remains on the old-result/coherent-fallback path and has a separate
  full-application aperture/tail problem.

The next generic shared-pool expansion should therefore use API, XRAGE, or
FLAG, where source responses and destination combining are actually active.
Existing complete-line XRAGE/FLAG evidence already covers fixed partitions;
the new GZZ result specifically adds dynamic shared capacity and bounded
partial-spill liveness under a fragmented real-application output pattern.

