# True-4K online row-window candidate

## Policy choice before implementation

The authenticated 16,384-record physical trace at
`/data1/nier/dx100-runs/2026-08-03-virtualization-integration/bounded-range-4bf5ef5/physical_admission_records.jsonl`
was replayed through an exact model of 4,096 live descriptors, 4,096 RowTable
line slots, 512 RowTable directories, eight lines per directory, and the live
16-slice mapping. A victim is a translated grow across all slices; retiring a
whole grow frees enough descriptors to make forward progress without a hidden
queue. Later appearances count as reopens.

| Finite online policy | A-line issues | Victim episodes | Reopens | Row-directory insertions | Peak descriptors / lines / rows |
|---|---:|---:|---:|---:|---:|
| Oldest live grow | 15,426 | 29 | 20 | 2,085 | 3,877 / 3,877 / 505 |
| Fullest live grow | 15,609 | 27 | 18 | 2,170 | 3,877 / 3,877 / 499 |
| Farthest grow-order boundary | 14,382 | 51 | 42 | 2,141 | 4,096 / 3,877 / 512 |

The grow-boundary rule saves another 1,044 modeled A-line requests relative to
oldest, but requires 76% more issue episodes and 110% more reopens. Fullest
minimizes episodes by two but increases both A-line work and directory
insertions. Oldest is the most defensible balance and is the only policy
implemented.

## Mechanism contract

The candidate consumes B in logical iteration order once. The existing
Word/Offset tables are the only precise descriptor store and remain capped at
4,096. The existing RowTable remains capped at 512 rows and 4,096 line slots.
On Row or Offset pressure, a fixed 512-entry grow ledger selects the oldest
live translated grow, charges all 512 selection visits through RowTable timing,
and issues only that grow. No replay cursor, spill, fallback vector, host oracle,
or uncharged queue exists. The fixed grow ledger is 12,416 charged bytes and
fails closed if more than 512 distinct grows appear. Generic exact
unique-address instrumentation is disabled in this mode; cache routing is
selected directly, so those inherited host sets cannot become hidden 16K
operation state.

Every descriptor retains its logical iteration through the Offset entry and
destination combiner. Admissions must be exactly sequential; retirement count,
sum, and XOR must close over the same iteration set; exact destination/backing
bytes remain the production correctness oracle. Capacity, history, stale
victim, non-sequential admission, invalid retirement, or closure failure is
fatal.

## Validation and matched evidence

Pending production build and the four-arm matched gem5 matrix. The matrix uses
one treatment-neutral, byte-identical checkpoint lineage and serial selector
updates for `native16`, `native4`, the authenticated frozen gem5 binary built
at commit `9ddf1ad3`, and the online-oldest candidate. The runner records each
arm's binary hash and source commit; each arm snapshots `SPD.cc` and `SPD.hh`,
and the matrix separately snapshots their exact `9ddf1ad3` versions.
