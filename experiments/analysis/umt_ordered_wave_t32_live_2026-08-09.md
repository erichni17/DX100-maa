# UMT ordered-wave T32 live resource treatment

Date: 2026-08-09

Frozen control source: `fcbfedac47387844e90a383e647366cd8ffa54bd`

## Treatment

The treatment changes only the production `UmtOrderedWaveStreamStateModel`
compute-token count from 16 to 32.  It retains eight divider lanes, divider
initiation interval 32, one global FP issue slot, four single-ported banks, the
5 KiB paired source/result store, and the adaptive D32/D64 guest ABI.

The independently represented logical-state floor rises from 50,411 to 57,950
bits.  The 7,539-bit increment consists of 7,536 token bits, two control bits,
and one instrumentation bit.  It excludes padding, ECC, SRAM/register layout,
arbitration, muxing, clocking, physical area, timing, power, and energy.

## Evidence contract

The source is branched directly from the frozen promoted control rather than
from the issue-width exploration branch.  Consequently, a T16-versus-T32 live
A/B changes token capacity and its matching cost assertions only.

Before application timing is read, the treatment must pass the state-model unit
test, D32 and D64 poison-tail preflights, mixed adaptive-ABI evidence, exact
application result checks, and source/binary/input provenance checks.  SPP1 and
SPP2 use the same native executable, workload, mode, simulator configuration,
and host resource policy in paired repetitions.  `simTicks` is the application
metric.  Host time is operational evidence only.

Promotion requires:

1. exact correctness in every arm;
2. the same selector/traffic signature as the T16 control;
3. no SPP2 regression beyond repeat spread;
4. a reproducible SPP1 improvement over T16; and
5. mechanism counters consistent with reduced token backpressure rather than
   changed work.

No application-performance or physical-design claim is made until those gates
pass.

## Prepared issue-two successor

A separate follow-on treatment retains T32/L8/II32 and permits at most two
global FP issues per cycle.  Writebacks reserve bank ports first; both issue
slots share the round-robin cursor; existing per-unit next-issue state forbids
two adds or two multiplies in one cycle; divider-lane state constrains divides;
and two bank-reading operations may issue only from distinct single-ported
banks.

The treatment adds no arithmetic unit or persistent functional scheduler
state.  It exports two 64-bit mechanism counters, increasing the measured
state floor from 57,950 to 58,078 bits.  A direct priority-encoder
implementation has 64 selector-candidate inputs and 128 operand-route bits:
32 candidate inputs and 64 route bits beyond issue width one.  These are
combinational logical lower bounds, not gates, wires, physical area, timing,
power, or energy.

The compiled state-model test requires nonzero dual-issue cycles while
retaining bit-exact results.  This remains a prepared prototype and must not
be built or interpreted as application evidence until the T32-only live A/B
is classified.
