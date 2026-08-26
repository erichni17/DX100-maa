# Selected virtualization integration (2026-08-26)

## Scope

Branch `codex/virtualization-selected-integration-20260826` starts at lead
commit `e6373c9f`, which remains frozen in its original worktree while the full
SSSP service runs. This separate worktree integrates the selected CG design,
its correctness methodology, the full IS certificate, and the fail-closed goal
auditor without mutating either live service's source tree.

## Selected CG lineage

The integrated production candidate is `direct4_product_page_fed_q16`:

- physical SPD remains eight tiles/core, 4,096 words/tile, or 524,288 bytes;
- q retains one logical 16K Row/Offset ordering scope;
- p intentionally uses four physical 4K gathers and does not preserve p-side
  16K reordering;
- virtual-p backing and coherent q-index backing are both eliminated;
- small and medium deterministic pairs are exact and report, respectively,
  `1.405585308x` and `1.388065786x` control/candidate ratios.

The candidate-only full runner is integrated but its lead-owned run at
`/data1/nier/dx100-runs/2026-08-26-cg-direct4-product-page-fed-q16-full-r2`
is still active. No full result or speedup is claimed before its numerical and
mechanism gate passes.

## Alternative retained separately

The accepted page-fed product-overlap implementation remains on branch
`codex/session-cg-page-fed-product-overlap-20260826-20260826-001126-188985bc`.
It is exact and improves small CG by `1.018416427x`, but it changes the
page-fed protocol to require publisher-driven product readiness. Direct4/q16
prepublishes products before opening q16 and therefore uses the original
page-fed protocol. Merging both implementations without a deliberate protocol
reconciliation would be incorrect; the selected integration does not do so.

## Sealed evidence

- Full page-fed CG numerical/mechanism certificate:
  `/data1/nier/dx100-runs/2026-08-26-cg-page-fed-full-reclassification-r1`.
- Full NAS IS correctness certificate:
  `/data1/nier/dx100-runs/2026-08-26-is-scalar-soa-full-certificate-r1`.
- Hardened HashJoin PRO/PRH roots retain exact hash ledgers and terminal gates.
- Full SSSP remains active at
  `/data1/nier/dx100-runs/2026-08-25-sssp-coherent-full-s22-r2`.

## Integration validation

The combined focused suite passes 94 tests covering schedule diagnosis,
deterministic reductions, direct4/q16 small/medium/full runners, CG and IS
certificates, page-fed application gates, and the completion auditor. All new
Python sources compile and `git diff --check` passes.

Both certificate validators pass from this integrated source. Fresh audit
`/data1/nier/dx100-runs/2026-08-26-hybrid-goal-audit-r2` correctly reports
`INCOMPLETE`, passes IS and both HashJoin kernels, and leaves `gate.complete`
absent because full direct4/q16 CG and full SSSP are nonterminal.

No native baseline was rerun and no remote push is part of this integration
checkpoint.
