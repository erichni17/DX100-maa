# LANL-MAA FP64 common-corner physical screen

This harness compares Berkeley HardFloat Release 1 binary64 add/subtract,
multiply, fused multiply-add, and replicated iterative-divider blocks under one
pinned OpenROAD-flow-scripts Nangate45 typical corner.

The successor screening top `LanlFp64Portfolio1A1M8D` places the selected
non-fusing one-add/subtract, one-multiply, eight-divider organization together
with a round-robin divide dispatcher, ready/valid backpressure, and six-bit
completion tags. It accepts at most one operation per cycle and exposes
per-unit completions; result-table writeback arbitration is outside this
screening boundary.

`LanlFp64Portfolio2S1A1M8D` is the bounded successor for the issue-width
question. It accepts two generic request slots per cycle, permits both when
they target distinct add/multiply units or two free divider lanes, and gives
slot 0 deterministic priority on a same-unit conflict. It retains the same
eight iterative dividers and per-unit completion boundary so its routed area
isolates the cost of wider operands, dispatch, and lost recoder sharing.

`activity/portfolio_activity_contract.json` and
`scripts/generate_portfolio_saif.py` generate three top-input SAIF sensitivity
profiles. UMT uses the conceptual 32-context source-order operation incidence
(38 add/subtract, 78 multiply, and 4 divide per context), not the smaller
observed-safe-reuse scheduling DAG. SPARTA uses the exact 64-particle
source-order operation count, and AMG uses a normalized balanced stream derived
from exact sparse-phase nonzero visits. Operand bits
come from an exact SPARTA native value pool for all three profiles, and only
top-level input nets are annotated. These reports are therefore useful for
control/datapath activity sensitivity but are deliberately ineligible for
native workload power or energy claims.

After the joint final route exists, `scripts/run_portfolio_power.sh` stages the
tracked activity inputs and builds only the three SAIF/OpenSTA power targets.
It intentionally omits the physical-flow output-group override used by
`run_common_corner.sh`, so Bazel materializes the power rule's JSON outputs.
The design-local `portfolio_power.bzl` and `portfolio_power_base.tcl` preserve
the generic flow while applying the correct steady-state case analysis
(`nReset=1`); the upstream helper assumes an unrelated active-high `reset`.

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
