# FLAG combiner lookup-latency result (2026-08-30)

## Decision

Accept a three-MAA-cycle pipelined lookup as low overhead for the selected
FLAG design. Across all 14 recovered gathers, lookup latency 3 adds 0.155%
equal-weight geometric-mean latency versus same-binary latency 0. Every case
remains exact, with per-case overhead between 0.035% and 0.262%.

## Fixed comparison

Both matrices use source `fa0a63f523abf17e03348495bf3f19bf02b7fe21`, gem5
SHA-256
`7afc44c7ff8bd8ca972ae2d7acd6ae15df3311220d8b1fd13fc27426f3c1e023`,
logical16K/physical4K, 2,048 tags, 8-way XOR7, 3,072 combiner words,
1,024 response words, and drain width 1. Only lookup latency changes from 0
to 3.

Every latency-3 case closes:

- exact output hash;
- one lookup issue/completion per logical FLAG element;
- `floor(length / 8)` complete FP64 lines plus one exact tail;
- write issue/WriteResp equality;
- zero ready-token wait cycles;
- summed peak pending metadata of 16, 24, or 48 across the case's logical
  operations, below the 1,024-word response-pool bound; and
- terminal checkpoint, restore, final stats, and `m5_exit`.

## Interpretation

The latency pipeline overlaps lookup across response slots and with memory:
four starts and four completions are independently allowed each MAA cycle.
Payload is not duplicated; exact lookup tokens reference data retained in the
already-counted response pool. The result therefore removes the zero-cycle
tag-lookup assumption without serializing one lookup at a time.

Lookup-token metadata is not included in the current packed storage ledger.
Although source bounds it by the 1,024-word response pool, measured peak at
latency 3 is only 12 per logical FLAG operation. A practical fixed queue should
be sized/backpressured near the measured pipeline requirement and charged
explicitly.

This remains a sensitivity rather than a synthesis result. XOR/set decode,
same-set hazards, physical tag/payload RAM ports, ready selection, and reset
still need a concrete implementation.

## Evidence

- latency-0 root:
  `/data1/nier/dx100-runs/2026-08-29-flag-xor8-lookup0-current-r1`;
- latency-3 root:
  `/data1/nier/dx100-runs/2026-08-29-flag-xor8-lookup3-r1`;
- paired summary:
  `.../flag-xor8-lookup3-r1/current_comparison/flag_xor8.md`;
- artifact ledger: `flag_lookup_latency_artifacts_2026-08-30.sha256`.

## Next gate

Enable the existing bounded page-ready queue to eliminate full-slot drain
scans, then address payload/reference ports and exclusive destination
ownership.

The all-14 queue successor is exact and timing-neutral (-0.0002% geometric
mean). See `flag_page_ready_drain_results_2026-08-30.md`.
