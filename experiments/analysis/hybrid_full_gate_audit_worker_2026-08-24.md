# Hybrid full-result classifier audit (2026-08-24)

The audited roots are intentionally not treated as a uniform schema.

| Workload | Supplied root | Required terminal/correctness evidence | Current one-shot reading |
|---|---|---|---|
| CG | `2026-08-24-cg-page-product-full-baf142f7-r1` | `result.txt` (`terminal=true`, `correct=true`), `gate.complete`, one exact CG fingerprint and terminal, ROI, m5 exit, first complete stats window | incomplete: restore/gate result not yet present |
| IS | `2026-08-24-is-scalar-soa-full-a44aaa60-r5` | NAS verification 6, scalar-SoA terminal PASS, ROI, m5 exit, first complete stats window | incomplete: restore is still nonterminal |
| HashJoin PRO | `hashjoin-hybrid-full-fc5f3ea4-20260824-0425` | PRO-specific exact cardinality/result row, PRO terminal PASS, ROI, m5 exit, stats | incomplete: preserved result row is header-only; `PARTIAL.status` is explicitly not a whole-gate pass |
| HashJoin PRH | `hashjoin-hybrid-prh-full-d7d29bf5-20260824-061147` | PRH-specific exact cardinality/result row, PRH terminal PASS, ROI, m5 exit, stats | incomplete: recovery root has no terminal row or exit evidence |
| SSSP | `2026-08-24-sssp-old-result-full-e690867f-r1` | zero checkpoint/restore exits, passed wrapper/gate, exact fingerprint, old-result terminal with closed counts, ROI, m5 exit, exactly two stats windows | incomplete: candidate restore/gate evidence has not closed |

`experiments/scripts/classify_hybrid_full_results.py` is a one-shot reader: it
does not inspect PIDs, services, or process-exit observations. A root can be
reported `running` only if its owner explicitly creates `RUNNING.status` with
the content `running`; otherwise missing evidence is `incomplete`. It reports
`correctness-failed` for a fatal simulator condition or a present but wrong
workload result. It only publishes `first_roi_simTicks` for `terminal-valid`.

The optional `--baseline` accepts frozen JSON metadata and echoes it for an
external comparison layer; it does not run native arms and cannot alter any
classification decision.

Example one-shot audit:

```bash
python3 experiments/scripts/classify_hybrid_full_results.py \
  --cg /data1/nier/dx100-runs/2026-08-24-cg-page-product-full-baf142f7-r1 \
  --is /data1/nier/dx100-runs/2026-08-24-is-scalar-soa-full-a44aaa60-r5 \
  --hashjoin-pro /data1/nier/dx100-runs/hashjoin-hybrid-full-fc5f3ea4-20260824-0425 \
  --hashjoin-prh /data1/nier/dx100-runs/hashjoin-hybrid-prh-full-d7d29bf5-20260824-061147 \
  --sssp /data1/nier/dx100-runs/2026-08-24-sssp-old-result-full-e690867f-r1
```
