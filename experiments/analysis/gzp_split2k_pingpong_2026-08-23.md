# GZP split-2K ping-pong result - 2026-08-23

## Decision

Reject the strict two-by-2K split as a performance optimization for the current
one-window GZP gate. It is exact and demonstrates safe overlap, but it is
0.155% slower than the equal-capacity 4K control in both deterministic
replicas.

## Evidence

Raw result root:
`/data1/nier/dx100-runs/2026-08-23-gzp-split2k-one-window-192eba51-r4`

Implementation commits on the isolated experiment branch are `1526b554`,
`b7bf0688`, and `192eba51`. The latter two repair register-capacity and ALU
subrange correctness defects found by the fail-closed gate.

| arm | replica 1 `simTicks` | replica 2 `simTicks` | result |
|---|---:|---:|---:|
| one 4K publication window | 256,344,496 | 256,344,496 | baseline |
| two 2K ping-pong windows | 256,741,067 | 256,741,067 | 0.155% slower |

Both arms produce exact output hash `12472729817211538253` and index hash
`673024389483126372`. A-value issue/response counts, published bytes, and
terminal ledgers close exactly.

## Interpretation

The treatment issues the second half before the first half's final write
response, so the intended overlap opportunity is real. Safe owner release is
also verified after write response completion. However, splitting the same 4K
capacity increases stream instructions from 12 to 16 and publication terminals
from four to eight. That control overhead exceeds the saved waiting time by
396,571 ticks.

This rejects a fixed 2K/2K split, not overlap in general. Reconsider ping-pong
only for a measured full-application path with exposed inbound/outbound copy
latency large enough to amortize the extra terminal work.
