# FLAG complete-line hybrid results (2026-08-29)

## Decision

Accept the fixed logical16K/physical4K complete-line hybrid as exact
cross-application simulator evidence on all 14 recovered LANL FLAG gathers.
Against current same-binary controls, its equal-weight geometric-mean latency
is 7.476% below fused16, effectively identical to compact16 (-0.009%), and
33.478% below the small bounded direct4 control.

Do not promote these observations as hardware-timed performance. The accepted
binary uses unlimited complete-line drain width, and the 16-way line-tag lookup,
payload ports, ready-line selection, reset, scoreboard search, and competing
coherence remain untimed.

## Fixed design

- software/logical gather and Row/Offset reorder window: 16K elements;
- physical result capacity: 4,096 FP64 words;
- original complete-line coverage point: 2,048 tags, 16 ways, 3,072 useful
  words;
- retained response pool: 128 slots, 1,024 useful words;
- coherent retirement: one write per complete 64-B line plus only the exact
  final logical tail; and
- fail-closed behavior: any non-tail partial victim or final partial drain
  panics.

The combiner and response payloads total exactly 4,096 words. This is a useful
payload bound, not an area-equivalence claim; tags, masks, references,
allocators, queues, and ports are additional hardware.

## Matched result

Every point uses gem5 SHA-256
`0861059339e7b9efb1073a11915a73cb450c7fa4ab6a59c3d6167dff2dd64a85`,
source `30a67a7a7f6cf1c8c6e1f498cc5a52425a3700c2`, the same guest binary and
input per configuration, two-channel Ramulator, and exact output verification.
For each of the 14 cases, the guarded candidate and `direct4_max` control have
byte-identical checkpoint physical-memory images.

| Comparator | Candidate latency change | Interpretation |
|---|---:|---|
| fused16 | **-7.476%** | complete-line 4K is faster on every FLAG case |
| compact16 | **-0.009%** | geometric-mean tie; per-case results vary |
| direct4 small | **-33.478%** | private complete-line retention removes fragmentation |
| direct4 max, guard disabled | **+0.000%** | exact tick/work identity in all 14 cases |

The per-case candidate advantage versus fused16 ranges from 1.871% to 15.493%.
Versus compact16 it ranges from 12.292% faster to 3.450% slower, so the
geometric-mean tie must not be described as uniform dominance.

## Where the gain comes from

The small direct4 control has only 16 line tags and 128 combiner words. It must
publish incomplete lines under pressure, producing between 7,061 and 22,536
partial transactions in these cases. The complete-line design retains scattered
returned words privately until all words of a destination cache line are ready.
It emits exactly `floor(length / 8)` full lines and one exact tail for every
non-multiple-of-eight FLAG length.

The guard does not create the gain. Disabling `virtual_complete_line_only` at
the same 2,048-tag/3,072-word geometry reproduces every candidate `simTicks`,
hash, write count, and full/tail count exactly. The gain is therefore
attributed to sufficient bounded private capacity for full-line publication,
not to the fail-closed checking flag or a fused direct sink.

## Correctness and provenance

The campaign audit requires, for every configuration:

- terminal checkpoint and restore exit codes plus `m5_exit`;
- the exact output hash;
- one result row and matching first/final `simTicks` in final stats;
- issue/WriteResp equality;
- full-line-plus-tail write closure;
- fixed source, binary, input, geometry, and no timeout; and
- no tracked source diff. Four late candidate launches recorded only the two
  then-untracked summarizer files; their source diff was empty and binary hash
  identical.

Evidence roots:

- candidate: `/data1/nier/dx100-runs/2026-08-29-flag-complete-tail-max-all14-r1`;
- current controls: `/data1/nier/dx100-runs/2026-08-29-flag-current-controls-r3`;
- failed 1,536-tag breadth attempt:
  `/data1/nier/dx100-runs/2026-08-29-flag-complete-tail-all14-r1`.

Artifact ledgers:

- `flag_complete_line_artifacts_2026-08-29.sha256`;
- `flag_current_controls_artifacts_2026-08-29.sha256`.

## Remaining gates

1. Rebuild and repeat XRAGE with complete-line drain widths 0/1/2/4/8.
2. Test 8-way and 4-way mappings or reject them on exact insertion replay.
3. Charge pipelined tag lookup and finite payload/reference ports.
4. Replace full-slot ready scans with a bounded ready queue/bitmap.
5. Test dirty backing, delayed/reordered acknowledgments, and overlapping
   producers, or explicitly require exclusive destination ownership.

The independent hardware review is
`../reviews/2026-08-29_complete_line_hardware_cost_review.md`.

## 8-way successor

A same-binary all-14 comparison now replaces the selected 16-way lookup with
8-way XOR-folded indexing at shift 7. Geometric-mean latency changes by
-0.004%, exact output/work closes everywhere, and checkpoint physical-memory
images match pairwise. Tag count and payload are unchanged, but lookup
associativity is halved. See `flag_xor8_results_2026-08-29.md`.
