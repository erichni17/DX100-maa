# Descriptor-Capacity Experiment

The selected direct-index mechanism originally bounded physical SPD and live
payload but retained a 16K Row Table and 16K Offset Table. This experiment
reduced only active Row-Table capacity from 16K to 4K entries by changing
`num_row_table_rows_per_slice` from 64 to 16. All other resolved MAA
configuration values matched after normalizing two newly recorded false-valued
defaults.

## Broad FLAG result

All 14 FLAG gathers passed exact guest output, terminal `m5_exit`, input and
guest-binary hashes, checkpoint provenance, two stats blocks, and resolved
configuration comparison. Against each workload's full-Row-Table direct4 arm,
the 4K Row-Table arm had:

- 1.127% equal-weight geometric-mean latency overhead.
- -0.626% to +3.165% per-workload latency change.
- 0.361% geometric-mean increase in issued A-source requests.
- 72 total Row-Table-full events.

No FLAG gather has enough one-pass degradation to justify repeated B scans.
Three configurations are slightly faster, which is legal because the smaller
table changes source issue order; this is not a claim that capacity reduction
intrinsically improves performance.

## What is and is not bounded

This historical treatment lets the Row Table retain 4,096 active source
descriptors and dynamically drain when it fills. It does not preserve one
monolithic 16K reorder window. The treatment still allocates a 16K Offset Table,
so it is a 4K-Row/16K-Offset design rather than the current fully bounded point.

The storage model reports:

| Component | Full Row/Offset | 4K Row / 16K Offset |
|---|---:|---:|
| Active Row-Table entries | 16,384 | 4,096 |
| Offset-Table entries | 16,384 | 16,384 |
| Shared descriptor lower bound | 254,464 B | 95,872 B |
| Direct-index comparable lower bound | 842,482 B | 682,322 B |

The 4K-Row design is 19.010% smaller than the full-descriptor direct-index
design in this model and 71.772% smaller than the original 2,417,152-byte
native-16K comparable lower bound. These are capacity lower bounds, not
synthesized area estimates.

## Fully bounded follow-up

The Offset Table was subsequently made reusable between drain epochs and
reduced to 4K entries. Across all 14 FLAG gathers, reducing Offset capacity from
16K to 4K at a fixed 4K epoch changed no simulated behavior: ROI ticks, writes,
DRAM commands, and MAA issue traces matched exactly. Changing the epoch from
16K to 4K at fixed 16K storage changed geometric-mean latency by -1.051%.

The current mechanism therefore uses bounded 4K reorder epochs; it does not
retain or reconstruct the full 16K reorder opportunity. See
`offset_capacity_epoch.md` for the three-arm attribution and updated storage
ledger.

## Professor's subset proposal

The repeated-B partition policy was tested on FLAG00. Routing repeated B scans
through LLC and retaining partial C combiner lines across partition barriers
removed much of the naive overhead, but one-pass dynamic drain remained faster:

| Policy | FLAG00 latency vs. full descriptors |
|---|---:|
| 4K Row Table, one pass | +3.013% |
| 4K Row Table, two cached partitions with retained combiner | +4.639% |
| 4K Row Table, three cached partitions with retained combiner | +4.582% |
| 4K Row Table, four cached partitions with retained combiner | +7.260% |

The proposal remains useful only for a workload where one-pass eviction loses
substantial A-row locality. It should not be enabled by default on current FLAG.
Any promoted multi-pass result must also charge finite partition-filter
throughput; the exploratory runs used unlimited filter throughput.

## Evidence

- Broad baseline:
  `/data1/nier/dx100-runs/2026-07-29-flag-gather-generalization-recovery3-670a072`
- Broad 4K-Row campaign:
  `/data1/nier/dx100-runs/2026-07-29-flag-quarter-descriptors-05f390c`
- Fail-closed comparison:
  `/data1/nier/dx100-runs/2026-07-29-flag-quarter-descriptors-05f390c/comparison`
- Frozen candidate simulator SHA-256:
  `5181b5159fa2ed6903692496e2bb2fa00394b223e972ba3b97925a413e4a02c4`
- Partition-count experiment:
  `/data1/nier/dx100-runs/2026-07-29-flag00-partition-count-05f390c`
