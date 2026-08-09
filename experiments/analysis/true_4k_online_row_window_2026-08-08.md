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

Implementation commit `2f20dc55` is a direct child of the requested base
`6e84c2c4`; the production-discovered scheduling fix is commit `0ad5d2de`.
The final matched evidence is at
`/data1/nier/dx100-runs/2026-08-09-true-4k-online-row-window/0ad5d2de`.
Every arm has a zero checkpoint/restore exit, a pass marker, a non-empty
terminal log and stats file, and the same output hash
`7228541527853630339`.

| Arm | Binary source | simTicks | Online delta vs arm | B/index words | A-line requests | DRAM reads / activates |
|---|---|---:|---:|---:|---:|---:|
| native16 | `0ad5d2de` | 39,933,479 | +41.9876% | 16,384 | 9,858 | 25,093 / 4,687 |
| native4 | `0ad5d2de` | 58,974,208 | -3.8553% | 16,384 | 16,384 | 31,616 / 5,056 |
| replay9dd | exact `9ddf1ad3` binary | 66,689,971 | -14.9789% | 81,920 | 9,603 | 24,042 / 3,851 |
| online-oldest | `0ad5d2de` | 56,700,576 | -- | 16,384 | 15,360 | 13,656 / 1,080 |

Thus online-oldest is a 1.040099x speedup over native4 and a 1.176178x
speedup over the replay candidate, but it is 41.9876% slower than native16.
This is a viable bounded alternative, not a native16 replacement on this
workload.

The online arm admitted and retired exactly 16,384 descriptors, produced
16,384 authenticated physical records, and used one B pass (16,384 words,
1,025 coherent cache-line reads, zero uncached index responses). Its measured
high-water marks were 3,944 precise descriptors, 3,883 RowTable line slots,
512 RowTable directories, 96 response slots, and 149 response words. Twenty
oldest-grow victims caused 20 counted reopens; `20 * 512 = 10,240` selection
visits were timing-charged. Policy storage was 12,416 bytes. The completion
record says `fallback=none`, `overflow=none`, and `placement=iteration`; there
were zero replay/summary passes and zero Offset drains.

The four arms reference one shared byte-identical checkpoint manifest hash
`61c75c0ad14ed3719674e14c3f49fefbea029e365c0ec6f6ac484231a66b8009`.
Current and extracted `9ddf1ad3` snapshots are byte-identical for both
`SPD.cc` (`d53aec6d...e5fe0`) and `SPD.hh`
(`d4000962...8846`). Binary provenance is explicit: current gem5 is
`f08378b1...d165`, frozen replay gem5 is the expected
`64980714...f1e`, and Ramulator is `76ea3a9c...5753`.

The first matrix attempt at
`/data1/nier/dx100-runs/2026-08-09-true-4k-online-row-window/2f20dc55`
is intentionally preserved as negative evidence. At iteration 3,883 and tick
3,255,914,892, Request repeatedly attempted Fill while an online victim owned
the window and 96 source responses were outstanding. This made no simulated
time progress and grew the trace, so the run was terminated. Commit
`0ad5d2de` blocks refill until that victim drains; a deterministic source
contract covers the failure and the replacement full simulation completes.

Validation passed for the production `build/X86/gem5.opt`, the online policy
unit, the bounded range/quantile/metadata-ledger units, 30 Python contracts,
shell syntax, `util/style.py -m`, and `git diff --check`.
