# Bounded 4K Reorder Experiment

## Question

Can a 16K logical gather retain useful global row reordering when both the
active Row and Offset metadata are bounded to 4K?

The range ideas tested here came from collaborative discussion. They are
candidate hypotheses, not decisions or final designs.

## Mechanism

Every bounded arm scans the same cached 16K `B[i]` stream four times. Each pass
admits only one subset into 4K Row+Offset metadata, issues the corresponding
`A[B[i]]` requests in row-aware order, and retires that subset before the next
pass.

```text
16K B values in LLC
        |
        +-- pass 0 filter --> 4K Row+Offset --> reordered A requests
        +-- pass 1 filter --> 4K Row+Offset --> reordered A requests
        +-- pass 2 filter --> 4K Row+Offset --> reordered A requests
        +-- pass 3 filter --> 4K Row+Offset --> reordered A requests
```

This preserves reorder visibility across the full 16K B stream for each chosen
row subset. It does not preserve one unrestricted 16K reorder window: each A
row belongs to exactly one finite pass.

## Result

All arms restored the same checkpoint, produced exact output hash
`7228541527853630339`, and closed 16,384 admissions and retirements exactly
once.

| Arm | simTicks | Overhead vs. full metadata |
|---|---:|---:|
| Full metadata hybrid | 51,504,776 | 0.00% |
| 4K modulo buckets | 62,456,646 | 21.26% |
| 4K fixed global ranges | 77,111,619 | 49.72% |
| 4K equal-width source ranges | 63,918,356 | 24.10% |
| 4K balanced range oracle | 62,045,990 | 20.47% |

The fixed global ranges fail as a policy because all 16,384 values land in the
first pass. Equal-width source ranges are better, but their first pass still
contains 5,743 values and exceeds the nominal 4K epoch.

The offline balanced oracle is only 0.658% faster than modulo in three
same-checkpoint replicas. It lowers DRAM activates from 3,799 to 3,395 and
precharges from 2,605 to 2,170, but requires more row-table build work. This is
an upper-bound diagnostic: it does not pay for discovering or sorting the
boundaries online.

## Interpretation

- Re-reading cached sequential B values is functional and bounded.
- Contiguous row ranges can improve DRAM locality.
- For this case, ideal balancing adds too little over simple modulo to justify
  a new sampling/sorting controller yet; its overhead would likely consume the
  0.658% gain.
- This does not reject bounded reordering generally. It rejects this specific
  balanced-range implementation direction as the next high-ROI optimization.

The next experiment should return to the larger measured costs: the hybrid
payload-transfer tail or a workload where modulo partitioning loses substantial
row locality.

