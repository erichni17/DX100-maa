# Hybrid full-result classifier audit (2026-08-24)

The audited roots are intentionally not treated as a uniform schema.

| Workload | Supplied root | Required terminal/correctness evidence | Current one-shot reading |
|---|---|---|---|
| CG | `2026-08-24-cg-page-product-full-baf142f7-r1` | result/gate, exact CG fingerprint and terminal, bounded config, ROI/m5 exit, first stats window, SHA ledger | incomplete: checkpoint construction |
| IS | `2026-08-24-is-scalar-soa-full-a44aaa60-r5` | zero restore exit, terminal PASS, NAS verification 6, scalar-SoA markers, result row, manifest hashes, first stats window | incomplete: O3 ROI |
| HashJoin PRO | `hashjoin-hybrid-full-fc5f3ea4-20260824-0425` | exact cardinality, real SoA/JIT route marker, kernel-specific pass rules, bounded config, closed first-window ledgers, m5 exit | terminal-valid partial arm: `28,586,786,731 simTicks`; no whole two-kernel claim |
| HashJoin PRH | `hashjoin-hybrid-prh-full-d7d29bf5-20260824-061147` | same raw checks with a required routed shifted pass | incomplete: recovery O3 ROI |
| SSSP | `2026-08-24-sssp-old-result-full-e690867f-r1` | zero exits, passed wrapper/gate, exact fingerprint, closed terminal, two stats windows, manifest/artifact/checkpoint hashes | incomplete: O3 ROI |

`experiments/scripts/classify_hybrid_full_results.py` is a one-shot reader: it
does not inspect PIDs, services, or process-exit observations. A root can be
reported `running` only if its owner explicitly creates `RUNNING.status` with
the content `running`; otherwise missing evidence is `incomplete`. It reports
`correctness-failed` for a fatal simulator condition or a present but wrong
workload result. It only publishes `first_roi_simTicks` for `terminal-valid`.

The lead build path used to launch IS was replaced by a later unified build.
The live process still held the original executable inode, whose SHA-256 was
verified as `2d02fa...6152`, identical to the existing read-only archive. The
raw root records PID start identity, command hash, cgroup, live executable
hash, and archive hash in `runtime_gem5_recovery.manifest`. The classifier
allows only this exact recovery schema and still rejects any archive mismatch.

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
