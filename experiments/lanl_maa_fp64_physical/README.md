# LANL-MAA FP64 common-corner physical screen

This harness compares Berkeley HardFloat Release 1 binary64 add/subtract,
multiply, fused multiply-add, and replicated iterative-divider blocks under one
pinned OpenROAD-flow-scripts Nangate45 typical corner.

The independent `LanlMaaLineTable32x4LinkedWaiters` top costs the first
memory/control slice at that same corner. It models 32 entries in four
eight-way banks, 42-bit line tags, 16-bit per-entry generations, two logical
issue slots, one retained request channel, stale-response rejection, one
acknowledged completion channel, and nine 32-bit accounting counters. Each of
the 64 operation slots owns one waiting bit and one six-bit next pointer; each
line entry owns a head, tail, and count. This replaces the baseline's
2,048-bit waiter matrix and 64-way completion encoder while preserving
arrival-ordered completion. Each bank resolves one distinct line per cycle; a
same-line pair shares its lookup. The top excludes the 512-bit response-data
steering path, operation payload storage, coherence cache, MSHRs, and the FP64
back end, so it is a metadata/control cost screen rather than a
whole-accelerator result. The physical target is
`//lanl_fp64:line_table_32x4_linked_waiters_final` and requests 30 ps of
hold-repair margin because the bitmap baseline missed extracted hold by
6.15 ps.

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
`LanlFp64Portfolio2SSharedRecode1A1M8D` moves the four IEEE-to-recoded
converters ahead of dispatch and routes 65-bit recoded operands to the units.
It is a separate physical top so the screen can quantify whether eliminating
per-divider post-mux recoders recovers area and synthesis complexity.

`LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2W` closes the selected raw
back end's non-backpressurable completion boundary. Three-entry add and
multiply FIFOs, one retained result per divider lane, and two output holding
registers absorb its ten possible completion sources. A round-robin arbiter
retires at most two results per cycle, matching the 64-operation live window's
two-wide completion port. Conservative issue credits prevent buffer overflow;
held outputs retain identity under backpressure. The physical target is
`//lanl_fp64:fp64_completion_2w_final` and requests 30 ps of hold-repair
margin. It still excludes the line table, response-data steering, and operand
storage, so its delta from the raw shared-recode target measures only the
completion interface and its interaction with the arithmetic back end.

`LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2WSplit` is the bounded cost
challenger. Output zero serves add plus even-numbered dividers; output one
serves multiply plus odd-numbered dividers. Each output arbitrates only five
stable retained sources instead of selecting any two of ten, while preserving
the same FIFOs, per-divider retention, issue-credit overflow protection, and
ready/valid holding registers. The fixed split is naturally balanced for a
simultaneous add/multiply pair and for consecutive round-robin divider issue.
It remains lossless under an imbalance, but a pathological stream confined to
one domain retires at one result per cycle. The physical target is
`//lanl_fp64:fp64_completion_2w_split_final`.

`LanlMaaOperationRetirement64x4x2` adds the bounded 64-tag operation and
retirement boundary. Four tag-interleaved banks accept at most one allocation
and one completion per bank each cycle. Completion results remain resident
until a common two-wide retirement handshake; ordered mode exposes only the
head and its consecutive successor, while unordered mode selects at most one
ready result from each of two distinct banks. Backpressure freezes selection,
so tag/value/flag identity is stable. Allocated and issued bits reject stale,
duplicate, or unallocated completions rather than silently consuming them.
The integrated
`LanlFp64Portfolio2SSharedRecode1A1M8DCompletion2WSplitRetirement64x4x2`
top connects that boundary directly to the split FP64 completion channels and
gates arithmetic issue on a prior operation allocation. The external operands
remain a test interface; returned-line steering and the selected line table
are still separate. Its prepared physical target is
`//lanl_fp64:fp64_split_retirement_64x4x2_final`.

`LanlMaaOperationRetirementOverlay64x4x2` is the lower-cost successor. Once
the FP64 back end accepts an operation, its source operands are dead, so the
result and five exception flags reuse 69 bits of that operation's existing
256-bit payload entry. The control exposes four 16-entry payload banks rather
than instantiating a second result table. Each bank has one read/write port;
a retirement read wins over a same-bank completion write, and a dedicated
counter exposes those stalls. Allocation is a metadata commit only after the
upstream payload initializer acknowledges its write. The paired payload model
has no reset and exists only for directed RTL simulation. It is excluded from
the control-only physical target
`//lanl_fp64:retirement_overlay_control_64x4x2_final`; that target prices the
state, selection, arbitration, and 69-bit bank steering to combine with the
already-budgeted four-bank operation-window macro, not a complete accelerator.
It uses 25% core utilization solely to provide legal die perimeter for 1,273
exposed macro-boundary pins. Cell area is the useful cost metric; die area and
boundary-dominated wire length are not a joint placement result.

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
