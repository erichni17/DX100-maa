# Transparent SPD Matched Matrix

Date: August 2, 2026

## Qualification

The fail-closed matrix summarizer accepted all six runs. Each run has zero
checkpoint and restore exit codes, exactly one benchmark result row, exactly
one `m5_exit` marker, and exact output hash `7228541527853630339`. All runs use
the same gem5 binary and workload binary:

- gem5 SHA-256:
  `978e746a074cd9f661bf355f4a043d3a86b43034c6a4a6cf3bdff369b488c0f2`
- workload SHA-256:
  `6c2e7ab2690e9ffda0f582b4f6c6ac42a65c91ffb66be5803160e88932046533`

Performance comparisons use first-ROI `simTicks`, never host time.

## Results

| Mechanism | simTicks | A/source line requests | Row descriptors |
|---|---:|---:|---:|
| Original native 16K | 43,558,019 | 12,583 | 1,695 |
| Direct-index native 16K | 40,874,044 | 9,858 | 1,458 |
| Original native 4K | 55,407,886 | not extracted | not extracted |
| Direct-index native 4K | 60,408,687 | 16,384 | 2,108 |
| Explicit overlapped paging on 4K | 46,897,416 | not extracted | not extracted |
| Transparent 16K-on-4K | 46,708,677 | 9,634 | 1,399 |

The fair virtualization controls use the same direct-index producer:

- transparent versus direct-index native 16K: **+14.274665% latency**;
- transparent versus direct-index native 4K: **-22.678874% latency**, or
  **1.293308x speedup**; and
- transparent versus explicit overlapped paging: **-0.402451% latency**, or
  **1.004041x speedup**.

The transparent point therefore lies between native 16K and native 4K, as a
virtualized design should. The earlier +7.233% result against original native
16K is not the fair virtualization overhead: direct-index ingestion itself is
6.162% lower-latency than the original index-tile path.

## Mechanism Signature

The transparent producer retains one 16K Row/Offset reorder epoch while
placing returned payload values in coherent backing. Its 9,634 source-line
requests are close to direct-index native 16K (9,858) and far below four
independent 4K epochs (16,384). The physical 4K SPD page is therefore not what
preserves the 16K reorder opportunity; the retained Row/Offset metadata is.

The transparent path pays for coherent backing writes, four backing-to-SPD
fills, and four ALU/store page chains. Its current controller is only 0.4%
faster than explicit software paging, so the demonstrated benefit is
transparent ownership and preserved reordering, not a large scheduler gain.

## Limits

This matrix is one deterministic microbenchmark. It does not establish a
general SPD cache, benchmark-suite speedup, synthesized area, energy, or true
memory-write acknowledgement for final stream stores. The live controller has
one logical descriptor, one generated micro-operation at a time, fixed page
order, and one special ALU-plus-store consumer.

## Raw Evidence

Raw runs and generated summaries are under:

`/data1/nier/dx100-runs/2026-08-02-transparent-spd-premeeting`

The generated summary hashes are:

- JSON: `c331a36b6802936135d4e8431d87fb69025fe6d172584d7255213308fead2556`
- Markdown: `ad7630887b471b22974733506a0047f220205e5bc4d0eb20f58531af9dc11419`

Regenerate the fail-closed summary with
`experiments/analysis/summarize_virtual_tile_consumer_matrix.py`, naming each
run directory explicitly and selecting `direct_native16`, `direct_native4`,
and `explicit_paged4` as references.
