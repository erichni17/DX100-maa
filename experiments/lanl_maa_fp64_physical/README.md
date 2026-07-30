# LANL-MAA FP64 common-corner physical screen

This harness compares Berkeley HardFloat Release 1 binary64 add/subtract,
multiply, fused multiply-add, and replicated iterative-divider blocks under one
pinned OpenROAD-flow-scripts Nangate45 typical corner.

The tracked RTL exposes IEEE-754 binary64 at the block boundary. Each operation
therefore includes its required `fNToRecFN` input converters and `recFNToFN`
output converter. Add, multiply, and FMA retain one registered output stage and
accept one request per cycle. The divider retains HardFloat's native iterative
ready/valid state. Rounding is fixed to round-to-nearest-even, matching the
bounded native workload records; add/subtract selection remains dynamic.

The screen is deliberately narrow:

- HardFloat archive: official Release 1, SHA-256
  `6b3757c9fbfa2230c6a2b84605e39372cb589dd7500e979c4f0b8ecc8a03b14b`.
- bazel-orfs revision: `6b55b049a5e753a234151578a3b3424388660db7`,
  source archive SHA-256
  `5ac89aea9c35fbdbbe118b6cb415510dd97c7e59adebcf46593239e734b6b809`.
- Bazel: 8.6.0 in batch mode.
- Physical platform: the exact Nangate45 typical platform bundled by pinned
  ORFS; its Liberty/LEF hashes and PVT metadata are frozen with results.
- Constraint: one 10 ns clock, 0.25 ns input/output delays, 40% target core
  utilization, and 60% placement density.
- Replication points: one, four, and eight independent iterative dividers.
- Endpoint: ORFS `final`, after detailed route, parasitic extraction, and final
  timing/area reporting; GDS generation and signoff remain out of scope.

The harness does not assume FMA contraction is source-safe, does not model a
pipelined or interleaved divider that HardFloat does not provide, and does not
turn default OpenROAD switching activity into workload-derived energy. A
topology or unit count may be selected only if correctness, physical reports,
and workload incidence jointly support it.

`scripts/prepare_external_workspace.sh` verifies both archives and stages only
the required sources into the extracted bazel-orfs workspace. The external
tool/source cache remains outside Git. `scripts/run_common_corner.sh` refuses
to run unless the caller explicitly sets `LANL_MAA_ALLOW_PHYSICAL=1`; callers
must also impose the approved cgroup CPU, memory, and no-swap limits. The
runner gives Bazel a minimal allowlisted environment because Bazel otherwise
copies inherited values, including unrelated credentials, into its visible
server command line. It also requests only headless final logs, JSON metrics,
reports, DRC output, and SPEF; the default stage output group carries optional
Qt GUI runfiles that are not part of this experiment.
