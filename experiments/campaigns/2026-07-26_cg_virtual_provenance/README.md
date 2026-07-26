# CG virtual-gather provenance

This note separates two superficially conflicting NAS CG observations. They
used the same application binaries and exact `x_q5` fingerprint, but different
simulator implementations. They are not replicas of one treatment.

| Design | Simulator | Native ticks | Virtual ticks | Virtual result |
| --- | --- | ---: | ---: | ---: |
| Pre-correction | `15813d45877c...` (`fdc0b3f`) | 57,701,699,301 | 55,632,272,883 | 3.72% faster |
| Corrected retirement | `54f3fbb8712f...` (`107afd6`) | 57,701,699,301 | 63,820,099,979 | 10.60% slower |

The pre-correction result has three deterministic replicas. The later
simulator includes `cd140bb` (coherent, ordered virtual retirement),
`93cc64f` (cacheable SPD tile-readiness gating), and their prerequisites. Its
virtual request stage increased from 127,305,518 to 153,461,753 cycles. The
older speedup therefore must not be presented as performance of the corrected
design.

Both virtual runs use the fused virtual-gather software path. Neither run sets
an independently smaller physical SPD capacity, so neither establishes the
performance of a 16K-logical/4K-physical virtual tile. The corrected pair is a
mechanism-overhead anchor only.

Correctness evidence:

- Both cases exited normally through `m5_exit` and had no fatal diagnostics.
- Native and virtual produced `x_q5=88c0975669c7062d` with no non-finite values.
- Corrected retirement writes balanced exactly: 52,742,884 issued and
  52,742,884 completed.
- The corrected comparison is one observation per case and uses
  binary-specific checkpoints; it is not promoted as a replicated result.

Raw evidence:

- Pre-correction replicas:
  `/data1/nier/worktrees/DX100-cg-virtual-handoff-20260717/experiments/campaigns/2026-07-17_cg_virtual_handoff_replicas`
- Corrected virtual anchor:
  `/data1/nier/dx100-runs/2026-07-26-transparent-virtual/cg-virtual-anchor-107afd6`
- Corrected native anchor:
  `/data1/nier/dx100-runs/2026-07-26-transparent-virtual/cg-native-anchor-107afd6`
