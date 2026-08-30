# XRAGE bounded page-ready drain result (2026-08-30)

## Decision

Accept the existing bounded page-ready queue for selected XRAGE drain
selection. It publishes all 8,192 complete lines with exact output at
37,291,759 `simTicks`, 0.016% below the full-slot-scan control and 11.865%
below native16.

The result removes an unrealistic 1,536-slot ready-line scan. The tiny timing
change is not an optimization claim.

## Fixed comparison

Both arms use source `fa0a63f523abf17e03348495bf3f19bf02b7fe21`, gem5
SHA-256
`7afc44c7ff8bd8ca972ae2d7acd6ae15df3311220d8b1fd13fc27426f3c1e023`,
the same XRAGE input/checkpoint contract, logical16K/physical4K, 1,536 tags,
8-way XOR7, drain width 1, and lookup latency 3.

| Drain selector | `simTicks` | Full selections | Page deferrals | Output hash |
|---|---:|---:|---:|---|
| full-slot scan | 37,297,706 | 0 | 0 | `5576400619275092867` |
| bounded page-ready queue | 37,291,759 | 8,192 | 119 | `5576400619275092867` |

Every producer/consumer counter and exact output closes in both arms.

## Mechanism and cost

When a combiner line transitions to full, its slot enters one of at most 16
logical-page queues. Drain selects from a fixed 16-head encoder and unlinks the
chosen slot after a successful coherent write. It no longer walks all 1,536
tags to discover full masks.

A packed lower bound for 1,536 slots is about 5.1 KiB: two 11-bit slot links,
one queued bit, and a 4-bit page id per slot, plus 16 head/tail pairs. This is
additional metadata and needs enqueue/dequeue ports; it is preferable to an
untimed global slot scan but is not free.

## Evidence

Evidence root:
`/data1/nier/dx100-runs/2026-08-30-xrage-page-ready-drain-r1`.

Artifact ledger:
`xrage_page_ready_drain_artifacts_2026-08-30.sha256`.

## Remaining gates

The selected design now has finite drain width, 8-way lookup, pipelined lookup
latency, and bounded ready selection. Remaining high-value work is physical
payload/reference RAM ports and exclusive destination/coherence ownership,
followed by reset/epoch and synthesis-based area/Fmax evidence.
