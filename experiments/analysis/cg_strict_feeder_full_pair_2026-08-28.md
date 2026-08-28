# Strict full-CG feeder pair (2026-08-28)

## Purpose

Run one trace-free, same-binary, same-checkpoint full-CG pair with feeder depth
1 versus 64. The accepted 250-GB one-line certificate remains the per-window
mechanism authority; this pair isolates full-workload feeder performance and
does not rerun native or direct4.

## Gate

- Reuse the immutable accepted full guest and treatment-neutral checkpoint.
- Preserve strict two-phase ordering, masked line retirement, value cache,
  four apply lanes, 16K logical Row/Offset, and 4K physical SPD.
- Require exact full terminal counts, frozen numerical tolerances, and all
  conserved work statistics from the accepted full certificate.
- Require resolved feeder depth in each `config.ini`.
- Forbid debug traces so the pair does not duplicate 500 GB of mechanism
  evidence already established by the accepted one-line certificate.
- Compute only feeder1/feeder64 performance; make no native claim.

Terminal results are pending durable execution.
