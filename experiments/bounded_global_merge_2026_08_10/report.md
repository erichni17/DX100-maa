# Four bounded row-local runs recover the global ordering

## Decision

Propose a narrow live gem5 vertical slice. The executable model passes the
predeclared structural gate: on every deterministic trace it is no worse than
the current four-pass schedule in A-line requests or DRAM-row activations, the
aggregate improves both, every output is exact, and the candidate never has
more than 4,096 active descriptors. This is not a promotion or speedup claim;
the sort, backing, and merge stages have not been timed in gem5.

On the authenticated current 16K physical trace, the current 16K-informed
four-pass order needs 9,577 A-line requests and 137 row activations. The global
merge needs 9,523 and 129: 54 fewer A requests (-0.564%) and eight fewer row
activations (-5.839%). Its issue digest, placement digest, A-line count, row
count, and exact output all equal the global16 oracle.

## Source-grounded semantics

The model is grounded at exact source commit
`ee08be4bb902ac72ced1f34ed02771cbe9588114`:

- `Tables.hh:52-56` defines an OffsetTable entry as logical `itr`, response
  word `wid`, and the next chain link. `Tables.cc:146-165` preserves those
  fields while appending a descriptor to an equal-line chain.
- `Tables.cc:340-368` coalesces equal physical line addresses inside a row and
  retains each `(itr,wid)` in OffsetTable. `Tables.cc:551-590` first matches
  `(grow,line)`, then adds a new line or row.
- `IndirectAccess.cc:277-294` constructs the bank-major RowTable slice order;
  for the frozen 1-channel/1-rank/4-bank-group/4-bank geometry it is
  `0,4,8,12,1,5,9,13,2,6,10,14,3,7,11,15`.
- `MAA.cc:562-576` decodes the frozen RoBaRaCoCh physical mapping.
  `IndirectAccess.cc:2383-2399` reconstructs and translates `A[B[i]]`, derives
  `wid`, slice, and grow, and `:2580-2583` admits exactly those identities.
- `IndirectAccess.cc:4606-4617` selects response words through retained `wid`;
  `:5233-5267` and `:5294-5327` restore them through retained logical `itr`.
- The current spool already defines the six-byte record used here:
  `BoundedDescriptorSpool.hh:14-39` specifies 14-bit `i`, 32-bit B value,
  six-byte records, and five-byte carry; `:179-195` gives the exact packing.

The model independently re-decodes physical A line, slice, row, and `wid` from
the six-byte record through the existing address translation. The authenticated
trace observes 256 A pages; that is page-table/TLB state shared with the normal
load path, not candidate reorder state or a hidden descriptor map.

## Executed mechanism and bound

The counted-grow planner produces four exact populations of 4,096. Pass 0 is
the deterministic resident choice. During the second B scan, pass 0 occupies
the reusable 4K sort workspace and the other 12,288 records enter the current
three external segments. The model then executes these steps:

1. Heap-sort the resident 4K records in place and write its sorted run.
2. Sequentially read each external population into the same workspace,
   heap-sort it, and overwrite that segment with its sorted run.
3. Discard the 4K workspace. Retain four six-byte heads, four 64-byte line
   buffers, at most five carry bytes per reader, finite cursors, and valid bits.
4. Four-way merge by `(slice rank, DRAM row, physical A line, logical i)`.
   Stream an equal-line cluster through one A response and place every word at
   its recorded `i`.

The full-trace high-water and storage ledger is:

| State | Bound / observed |
|---|---:|
| Active descriptor limit / high-water | 4,096 / 4,096 |
| Reused sort workspace payload | 24,576 B |
| Classification line-plus-carry staging | 207 B |
| Merge heads + four line buffers + carry/cursors/valid | 314 B |
| Final run populations | 4 × 4,096 |
| LLC backing footprint | 98,304 B / 1,536 lines |
| Hidden operation-sized descriptor map | none |

The model executes 339,116 heap comparisons and 178,731 swaps across the four
runs, plus 48,015 four-head merge comparisons. These are work counters, not
cycle estimates.

## Backing, pass, and traffic accounting

Both current bounded paged4 and the proposed merge retain the current one
summary plus one classification B scan: 131,072 semantic B bytes and 2,048 B
line reads on the frozen aligned stream.

| Descriptor traffic | Current four-pass spool | Global-merge candidate |
|---|---:|---:|
| Backing footprint | 73,728 B / 1,152 lines | 98,304 B / 1,536 lines |
| Classification append | 1,152 writes | 1,152 writes |
| External sort input | 0 | 1,152 reads / 3 run passes |
| Sorted-run materialization | 0 | 1,536 writes / 4 runs |
| Sequential replay / global merge | 1,152 reads | 1,536 reads / 1 pass |
| Logical LLC descriptor writes + reads | 2,304 lines | 5,376 lines |
| Candidate eventual dirty writeback | not in supplied baseline | 1,536 lines |
| Candidate total including dirty writeback | — | 6,912 lines / 442,368 B |

Every one of the candidate's 5,376 logical LLC line accesses is executed
against a fixed byte-addressed store and included in event digest
`44f8cfdf53fb8b2de5413bde3db57fd2204e8dab964ac69bb5271e4b50aa339c`.
The merge reads exactly 16,384 records, 98,304 valid bytes, and 1,536 lines.
The byte store includes record-crossing-line behavior; observed carry is four
bytes, below the five-byte bound.

## Deterministic trace comparison

`results.json` freezes three generated adversarial traces and the authenticated
current physical trace. “Row activations” is an explicit per-slice open-row
model: the first request to a row activates it, a different row in that slice
reactivates it, and equal-row requests hit. It is a structural row-order metric,
not a claim about the Ramulator scheduler's command timing.

| Trace | Native4 A / rows | Current four-pass A / rows | Global merge A / rows | Global16 A / rows |
|---|---:|---:|---:|---:|
| Skew + repeated lines | 228 / 8 | 236 / 11 | **228 / 8** | 228 / 8 |
| Skew + unique lines | 256 / 8 | 256 / 11 | **256 / 8** | 256 / 8 |
| Adversarial + repeated lines | 178 / 27 | 64 / 11 | **56 / 8** | 56 / 8 |
| Authenticated current 16K | 16,384 / 516 | 9,577 / 137 | **9,523 / 129** | 9,523 / 129 |

The authenticated trace contains 54 A lines and four bank rows spanning more
than one planned population. The current population barrier repeats those 54
line requests and causes eight row reactivations. The merge exposes all four
heads concurrently, so equal lines coalesce once and every bank row is visited
once. The global result digest is
`f9fc0b9fe26f825a0b9ebfa6bc71f0f94c0f159e7615f1bc2f06215f08e779c0`.

The aggregate current/global-merge counts are 10,133/10,063 A requests and
170/153 row activations. There is no per-trace regression, and both aggregate
improvements are strict.

## Exact current measured context

The supplied exact current comparators are retained without reinterpretation:

| Arm | simTicks | Descriptor Fill cycles | A Request cycles |
|---|---:|---:|---:|
| native4 | 59,267,176 | 5,169,508 | 52,038,128 |
| bounded paged4 | 60,913,869 | 22,029,879 | 30,625,172 |

Bounded paged4 therefore pays 16,860,371 more descriptor Fill cycles while
saving 21,412,956 A Request cycles relative to native4. Its 12,288 external
six-byte records, 72 KiB backing, 1,152 writes, and 1,152 reads are the exact
baseline preserved above. No candidate `simTicks`, Fill cycles, or Request
cycles are inferred from the model.

## Vertical-slice contract

The structural result is strong enough to propose, but not to approve, a live
gem5 slice. The slice should be rejected unless it reports all of the following
on a matched binary/input/checkpoint pair:

- four populations, each at most 4,096, with active descriptor high-water at
  most 4,096 and no fallback or hidden identity array;
- the exact six-byte record count, run populations, sort reads/writes, merge
  reads, line counts, run-head occupancy, comparisons, and terminal ACKs;
- candidate A-line requests at most 9,523 and a row-order signature consistent
  with 129 unique `(slice,row)` groups on the frozen trace;
- exactly 16,384 admissions and retirements, no duplicate or missing logical
  `i`, exact output hash, terminal completion, and no stale ownership;
- measured Fill/Request cycles and `simTicks`; promotion requires correctness,
  the predicted mechanism signature, and an actual matched timing improvement.

No core MAA source is changed by this work.
