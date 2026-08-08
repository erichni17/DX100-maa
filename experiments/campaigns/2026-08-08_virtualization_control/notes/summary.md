# Virtualization Control

This is the frozen control for the August 8 optimization sprint. Both arms use
the same treatment-neutral checkpoint, source commit, gem5 binary, workload,
Ramulator library, logical 16K range, and 16K Offset capacity. Unlike deferred
treatments were run serially because the workload reads one shared treatment
file after checkpoint restore.

Both arms completed with exact output hash `7228541527853630339`:

| Arm | `simTicks` | Relative to native 16K |
|---|---:|---:|
| Direct-index native 16K | 41,189,861 | baseline |
| Transparent 16K-on-4K | 46,889,591 | 13.837701% slower |

The transparent path therefore has a real, expected virtualization cost. It
retains the 16K reorder metadata but adds 5,019 acknowledged backing writes,
four page-ready events, and extra DRAM activation/precharge activity.

This result is not the older 8.26% retirement-only calibration. That experiment
did not equalize the full producer/consumer chain. It also does not supersede
the prior 14.27% matched result, which used an older binary; it re-establishes
the control at source commit `0108d9b` for comparisons made in this sprint.

## Excluded Attempts

Two earlier roots are excluded. The concurrent arms shared one deferred
treatment selector, and the transparent process observed `native_direct`.
It then correctly panicked when element 5,537 exceeded the 4K physical SPD.
This is a harness race, not architecture evidence. Different treatments must
remain serial unless they use independent selector paths.
