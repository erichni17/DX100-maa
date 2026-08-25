# Full-CG host golden-vector oracle (2026-08-25)

This is the required successor gate for the rejected class-C physical
page-product run.  It does not change that decision: the run remains
`REJECT_CORRECTNESS_GATE`, its four coarse quantized-hash mismatches remain
recorded, and its simTicks remain rejected-run provenance only.

## Frozen construction identity

`experiments/scripts/build_cg_full_golden_oracle.py build` accepts only the
following class-C precomputed header and reference identity:

| Item | Frozen value |
| --- | --- |
| Header SHA-256 / bytes | `f2b18716e4a2356c597c95ee3583549def72700f2cb3294b0fcaacca46dbe131` / `992830458` |
| CG source commit / blob | `5d51743bfca566c486c6786cf3b18e6d378d805a` / `78f9d77983565fd6a0c32a3db627956f84b1cfdd` |
| Existing reference log SHA-256 | `0fe931685c37695bc51c74288c67f1494a0c91a723f8e831efa0ac2a7515441c` |
| Existing reference identity | the `CG_FINGERPRINT` recorded in `cg_full_page_product_rejection_2026-08-25.md` |

The tool copies the checked header, materializes the exact source blob from
Git, and compiles a standalone `FUNC` probe.  To keep construction bounded,
the probe parses the three BASE-required arrays (`a`, `colidx`, `rowstr`) from
the header at runtime instead of asking the compiler to parse a 993 MB C
initializer.  It then calls the frozen source's `conj_grad_base` loop with
exactly one OpenMP thread.  It writes `golden_oracle.json`, two little-endian
binary32 vectors (`x.f32le`, `z.f32le`), their hashes, scalar `rnorm` and
`zeta`, and the immutable `criteria.json`.  A failed or partial build has no
oracle JSON and cannot be consumed as a reference.

Example construction (a host-only, bounded command):

```bash
python3 experiments/scripts/build_cg_full_golden_oracle.py build \
  --repo "$PWD" \
  --precomputed-header /data1/nier/dx100-runs/2026-08-24-cg-page-product-full-precomputed-5d51743b-r2/input/cg_data_4C.h \
  --output /data1/nier/dx100-runs/2026-08-25-cg-host-golden-oracle \
  --compile-timeout 1800 --run-timeout 1800
```

No gem5 binary, checkpoint, restore, native gem5 baseline, or architecture
source is part of that command.

## Predeclared successor acceptance rule

The checked-in tool fixes these values before any successor full candidate:

| Quantity | Maximum absolute error | Maximum relative error | Count above absolute / relative limit |
| --- | ---: | ---: | ---: |
| every `x[i]` | `1e-5` | `1e-3` | `0 / 0` |
| every `z[i]` | `1e-5` | `1e-3` | `0 / 0` |
| `rnorm` residual | `2.5e-7` | `2e-3` | n/a |
| `zeta` | `1e-8` | `1e-10` | n/a |

All values must be finite.  A candidate submits a JSON manifest with the
exact schema `dx100.cg.full.candidate_vector.v1`, an *exactly equal*
provenance object, `x`/`z` binary32-le files of exactly 150,000 elements, and
the SHA-256 of each file.  `verify` rejects path escape, symlinks, wrong
shape, missing scalars, nonfinite values, hash drift, provenance drift, and
any limit violation before producing a PASS JSON:

```bash
python3 experiments/scripts/build_cg_full_golden_oracle.py verify \
  --golden-oracle /data1/nier/dx100-runs/2026-08-25-cg-host-golden-oracle/golden_oracle.json \
  --candidate-manifest candidate_vectors.json \
  --output candidate_oracle_result.json
```

The candidate manifest must be a result export, not a replacement reference;
the verifier never accepts caller-provided limits or a caller-provided header
identity.

## Is ordinary host `BASE` semantically equivalent?

Only at the source-algorithm level.  The probe uses the frozen sparse matrix,
the same `conj_grad_base` loops, class-C constants, binary32 arrays, and one
inverse-power iteration.  It is therefore an independently constructed
reference for the scalar BASE update order, not a gem5 baseline.

It is not execution-equivalent to the four-thread gem5 MAA candidate.  The
candidate executes virtual gathers, page product publication, and a 16K
SoA/JIT ADD; those alter scheduling and potentially floating-point grouping.
Host compiler/libm behavior, x86 floating-point details, and OpenMP reduction
order are also distinct.  One host thread removes host scheduling variability,
but it cannot prove candidate microarchitectural equivalence or reproduce
MAA's grouping.  That is why this gate compares exported elements and residual
criteria rather than claiming host timing or exact raw fingerprints.
