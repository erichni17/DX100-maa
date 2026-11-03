# gradzatp_invert indexed-reduction regression

The CPU-side reduction in `gradzatp_invert_MAA_CSR()` can have repeated
`tilei_ptr[j]` values. Iterations then update the same point volume/gradient and
carry a dependence. An `omp simd simdlen(4)` independence promise is unsafe here.
The production fix removes only that directive; the arithmetic, CPU reference,
normalization, caller initialization requirements and MAA API stay unchanged.
Ordinary optimization remains enabled.

This is the exact `ordered-reduction-v1` repair from Joshveer Grewal's
[ArchEvolve audit](https://github.com/petecao/ArchEvolve/tree/main/docs/audits/ume-gradzatp-invert-ordered-reduction-v1-20261030).
The repaired kernel SHA-256 is
`cd1dc53143262be56b4767395fe7183597c82dc479bd90503863ecc4a45bcb19`.
`upstream-provenance.json` records the original kernel and imported test-source
hashes. The unchanged harness/checker and selected fixture/oracle function
bodies are reused with attribution; the launcher is self-contained in DX100.

## Run

From the repository root, using a fresh result directory:

```sh
python3 -B benchmarks/UME/tests/gradzatp_invert_reduction/run_regression.py \
  --compiler /usr/bin/g++-9 --compiler /usr/bin/g++-12 \
  --native-stress --output /tmp/ume-invert-regression-new
```

The standard profiles use `-O2` and `-O3`, OpenMP, assertions, one runtime
worker/four compiled contexts, 16K tiles, no fast math and no FP contraction.
`--native-stress` adds a separately labeled `-O3 -march=native` profile.
Compilation and cases run serially. The runner limits each process to 2 GiB
virtual memory and each output file to 256 MiB; it has no simulation or model
launch path. Raw fixture outputs and compiler/vectorization logs stay in the
result directory, outside Git. Only a compact result summary is checked in.

Eight finite cases cover repeated points, mixed rows, zero points, empty
non-normalizing rows, internal/boundary points, 16K/16K+1 corner rows, and an
outer point-block tail. Fixed finite outputs must match CPU bytes and the
independent serial oracle exactly, in addition to the unchanged strict checker.

Two negative controls remain failures: empty normalizing points still produce
nonfinite values, and nonzero initial MAA outputs still violate the original
zero-initialization precondition. The checker retains its `1e-3` absolute
threshold and rejects nonfinite results. This repair selects no new empty-point
policy, reset behavior or numerical tolerance.

## Linux verification

The recorded Linux/x86-64 GCC 9.5 and GCC 12.3 check covers six compiler profiles:
48 fixed finite cases pass exactly; 12 expected caller failures remain failures;
all 48 strict-checker control checks pass. The unchanged original kernel also
passes these Linux finite cases: its indexed loop was not vectorized, as shown
by the compiler reports. The original failure is reproduced in the upstream
GCC 15.2/ARM audit; we do not claim to reproduce that failure on these Linux
compilers. The false independence annotation is removed for portability.

These results validate this native functional profile, not multiworker gem5,
hardware visibility/atomicity, or performance. Existing archived runs and
binaries retain their original source identities. Future runs using this repair
must identify the new source/binary and use compatible fresh checkpoints;
rebuilding does not update a checkpoint's saved executable image. This change
is for `gradzatp_invert`, not the separate regular `gradzatp` adapter.

The commit retains the byte-pinned upstream kernel/checker/harness and selected
oracle bodies. Formatting-only pre-commit hooks (EOF, isort, black, pyupgrade,
gem5 style) were excluded for this import; they would alter those provenance
bytes or require unrelated formatting changes. Syntax, JSON, whitespace, conflict,
large-file and other applicable checks still run. No runtime/scientific checker
is skipped or changed.
The commit-message tag checker is also unavailable in this tree because
`MAINTAINERS.yaml` is absent; that broken hook was excluded for this commit.
