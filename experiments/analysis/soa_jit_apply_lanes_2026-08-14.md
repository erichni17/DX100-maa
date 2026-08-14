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

## Exact gem5 result

The deferred sweep completed at:

`/data1/nier/dx100-runs/2026-08-14-soa-jit-apply-lanes-p16v32-08845927-r1`

All arms restored the same immutable checkpoint, used guest SHA-256
`c7fb4f8dd038cb129115f11a11390aa672bd4e9fba4f05573e4aa257e089c497`,
produced exact output hash `2761840269561229581` with zero errors, and closed
the value, A-read, A-write, alias, and terminal ledgers.

| active lanes | `simTicks` | delta from lane 1 | value reads | value stalls |
|---:|---:|---:|---:|---:|
| 1 | 52,211,843 | baseline | 22,280 | 114,170 |
| 2 | 54,581,566 | +4.538% | 22,965 | 130,905 |
| 4 | 54,522,722 | +4.426% | 22,624 | 117,354 |

The additional lanes are a measured rejection.  They change request timing,
reduce value-cache merging, increase cache-line reads, and make the exact
microbenchmark slower.  The default one-lane treatment remains the selected
configuration; the two- and four-lane modes must not be presented as an
optimization or included in the promoted hardware point.
