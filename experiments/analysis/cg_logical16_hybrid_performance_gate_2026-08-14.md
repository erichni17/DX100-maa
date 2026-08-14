# CG logical-16 residual SoA/JIT exact performance gate

`run_cg_logical16_hybrid_performance.sh GEM5 OUTDIR TREATMENT_FLAG...` is the
narrow performance gate for the existing NA1024 residual SoA/JIT path. It
builds one guest at the checked-out source commit and creates exactly one
AtomicSimpleCPU checkpoint before CG reads its immutable
`token_stream_ld residual_soa_jit` selector. Every O3 restore therefore uses
the same guest binary, selector, checkpoint, input geometry, 16K Row/Offset
metadata, and 4K physical SPD. The control receives no extra flags; only the
treatment receives the explicit supplied `--maa_soa_jit_*` flags.

The recommended first treatment is:

```bash
experiments/scripts/run_cg_logical16_hybrid_performance.sh \
  build/X86/gem5.opt /path/to/evidence/cg-pre-a \
  --maa_soa_jit_pre_a_value_lookahead
```

Two deterministic replicas are required by default. Their four restores run
in parallel after the shared checkpoint is frozen. There is no wall-clock
timeout unless `CG_HYBRID_TIMEOUT_SECONDS` is explicitly set to a positive
integer. The gate
fails closed unless both arms have the exact CG fingerprint and terminal
ledger, terminal gem5 markers, matching frozen artifact/checkpoint/selector
provenance, and config hashes after removing only the resolved pre-A treatment
line. It records `simTicks`, value-read issue/response/fill counters, A-read
and A-write request/response counters, selected elements, terminal completions,
and pre-A counters. It requires balanced request/response ledgers and the same
guest-work counters per replica; `simTicks` speedup is reported only after
those checks pass.

This gate measures a simulator-only flag difference. It does not alter the
CG guest or claim an MAA mechanism or area improvement. A `VALID_MEASURED_PAIR`
means the pair is comparable and complete, not that the treatment is promoted;
the measured per-replica speedups in `decision.txt` remain the evidence.
