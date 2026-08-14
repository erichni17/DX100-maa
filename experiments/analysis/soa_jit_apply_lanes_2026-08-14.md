# SoA/JIT selectable apply lanes — 2026-08-14

This bounded optimization extends the existing logical-16K/physical-4K
SoA/JIT RMW path with `--maa_soa_jit_apply_lanes={1,2,4}`.  The default is
one, preserving the prior timing treatment.  Four apply lanes are physically
provisioned in every treatment; selecting a treatment changes only how many
of those fixed lanes are active.

Each apply-lane grant owns an exact `(generation, A physical address,
context)` tuple for one modeled cycle.  A context and an A address may each
appear at most once among that cycle's owners.  The scheduler only grants a
lane when the context's ready slot matches `context.nextOffset`, consumes the
same Offset entry immediately after the arithmetic update, and advances a
context at most once per cycle.  Consequently, distinct A-line contexts may
apply concurrently while every A line retains exact Offset-chain order,
including bit-sensitive floating-point duplicate order.

The unchanged protocol boundaries remain fail-closed: Decode requires the
full logical Row/Offset window, Request begins only after the full fill,
generation and translated-address checks remain on all data responses, and a
context remains live through its response-bearing A-line WriteReq until the
matching WriteResp.  Terminal closure still requires empty contexts and
Offset state plus exact value, alias, A-read, A-write, predicate, and
WriteResp accounting.

The completion trace reports `apply_lanes` and per-operation `apply_hwm`.
The storage trace reports four fixed lanes, active lanes, active high water,
per-owner bytes, and total lane-pool bytes.  Statistics add configured active
lanes and apply-lane high water.  The overlap runner retains every lane-1 arm
and adds matched physical-4K, context-8, lookahead-8, value-owner-32 lane-2
and lane-4 treatments.  Those treatments fail if they do not exhibit
same-cycle independent application, exact terminal closure, and the common
output hash.

Validation performed without a gem5 simulation:

- `tests/maa/run_soa_jit_overlap_state_unit.sh` passed optimized and
  ASan/UBSan builds.
- 23 focused Python contract/safety/predicate test functions passed directly;
  the system Python does not provide pytest.
- `bash -n experiments/scripts/run_hybrid_rmw_soa_overlap_matrix.sh` passed.
- `scons build/X86/mem/MAA/IndirectAccess.o build/X86/mem/MAA/MAA.o -j8`
  passed, including regenerated `MAA.py` parameter bindings.

No full gem5 simulation was run.  The new lane-2/lane-4 runner arms are the
deferred performance/correctness treatment for a later simulation campaign.
