# Bounded contiguous-row reorder study (2026-08-03)

## Outcome

The standalone experiment supports a **workload-scoped** contiguous decoded-row
policy, not a general replacement for the native 16K reorder lifetime.

On the existing deterministic FP64 tile-consumer trace, the 16K-logical/4K-active
treatment reduces modeled A-line requests from 16,384 (`native4k`) to 9,670
(-40.979%) and decoded-row transitions from 515 to 152 (-70.485%).  It remains
close to the one-epoch trace control (9,523 lines and 128 transitions), places
all 16,384 results exactly once, and never exceeds 4,096 live descriptors.

That recovery is not free: four full B scans move 262,144 semantic bytes, of
which 196,608 bytes / 3,072 cache lines are rereads; a 16-word/cycle selector
has a 4,096-cycle serialization lower bound; and a slightly overweight row
interval causes one additional capacity drain (five active epochs total).

The static policy is deliberately fail-closed under skew.  `fanout` and
`same_line` place all 16K records in one row interval, drain it in four bounded
epochs, and issue the same four A requests as `native4k`.  They therefore fail
the strict recovery gate.  No overflow, hidden N-entry state, or oracle order is
used to turn those cases into wins.

This evidence justifies a later gem5 **screen**, after ownership transfer, only
for traces with wide decoded-row dispersion.  It does not justify promotion,
and no simulator source was modified here.

## Native source grounding

Source revision: `9393bf09f9318d31b1f8406d839cc2510690e47d`.

### Insertion and row matching

The native OffsetTable entry is exactly three C++ `int` fields: logical
iteration, word ID, and next pointer (`Tables.hh:52-55`).  Allocation creates
one entry and one validity flag per configured entry and reserves a free-index
vector of the same capacity (`Tables.cc:123-140`).  Insert pops a real free
slot, writes `(itr,wid)`, and links the previous line-chain tail to it; full
capacity is a fatal error rather than an implicit spill (`Tables.cc:143-166`).

For each B element, `fillRowTable` reads the index, computes/translates the A
cache line, decodes it with Ramulator's RoBaRaCoCh map, derives the RowTable
slice and `grow_addr`, and records the logical iteration and word offset
(`IndirectAccess.cc:920-956`).  The epoch-capacity check occurs before insert
and forces a drain at the configured limit (`:933-941`).  A RowTable slice then
matches in this order:

1. an unsent identical `(grow row, cache line)`;
2. an unsent identical grow row with a free line entry;
3. the first free row slot;
4. otherwise insertion fails and the indirect unit drains.

Those cases are directly implemented at `Tables.cc:489-535`.  Within a row,
an identical line appends another Offset entry; a new line stores its address
and the chain's first/last Offset IDs (`Tables.cc:278-306`).  B itself is not
copied into RowTable state.

### Issue selection

The current issue path is deterministic but is not a global sort oracle.
RowTable construction precomputes a bank/slice traversal; Build visits that
slice order and asks each slice for its next entry
(`IndirectAccess.cc:1452-1489`).  The native RowTable cursor selects the first
valid row slot, groups other slots with the same `grow_addr`, and walks valid
cache-line entries in slot order (`Tables.cc:573-609`, `:676-715`).  The
bounded virtual path similarly scans finite rows/entries and reserves a finite
response slot/word budget before creating the read (`IndirectAccess.cc:
1455-1605`).  This is insertion/slice order constrained by decoded rows; it is
not knowledge of future B values.

The standalone model keeps that non-oracle property: partition bounds are
equal contiguous row-key intervals derived solely from the registered A
aperture and DDR geometry.  Inside an active epoch it issues first-seen rows,
then first-seen lines.  Validation hashes record the resulting order but never
feed it back into selection.

### Response placement

A native response is decoded again to `(slice,grow)`, matched by returned
cache-line address, and its linked Offset chain is removed
(`IndirectAccess.cc:2039-2087`; `Tables.cc:412-418`; `Tables.cc:168-186`).
Every recovered `(itr,wid)` extracts that word from the returned line and
writes it to destination `SPD[dst][itr]` (`IndirectAccess.cc:2143-2162`).

The bounded virtual path reserves the Offset-chain head with the request,
places a returned line/packed words in a finite response slot, and retires the
chain through the bounded destination mechanism (`IndirectAccess.cc:
2056-2141`).  The model checks the same semantic contract: each logical `i` is
placed once at C[i], every policy produces the same output hash, and no missing
or duplicate placement is tolerated.  Its Python output list and exact-once
counts are validation oracles only; policy selection and issue never read them.

### Total native table storage

Zero-valued Offset capacity/epoch parameters resolve to the logical tile size,
so the default 16K configuration really allocates 16K Offset slots
(`MAA.cc:55-71`).  Each indirect unit receives that capacity independently
(`MAA.cc:392-400`).  RowTable allocation is separate: every supported
organization is constructed, not only the active organization
(`IndirectAccess.cc:205-280`), and every row allocates line entries plus valid
and claimed flags (`Tables.cc:455-487`, `:260-268`).

For the frozen one-channel DDR4 controls (organizations 2/4/8/16), the checked
source ledger reports:

| Native table view | Amount | Boundary |
|---|---:|---|
| Offset C++ entry + valid arrays | 212,992 B/unit | `16,384 * (12 + 1)`; excludes container/object overhead |
| Offset free-index backing | at least 65,536 B/unit | 16,384 reserved `int` IDs |
| Active bit-packed Row/Offset/invalidator lower bound | 148,736 B | active 16-slice, 64-row, 8-line geometry |
| All allocated RowTable organizations | 32,768 line entries, 1,920 rows | arrays for 2/4/8/16 slices coexist |
| All-organization bit-packed descriptor lower bound | 452,064 B | includes validity/claim and invalidator lower bounds |

The C++ RowTable raw-array formula is
`32768 * (sizeof(RowTableEntry::Entry) + 2*sizeof(bool)) +
1920 * 2*sizeof(bool)`, before the `RowTableEntry`/`RowTableSlice` objects,
vectors, alignment, and allocators.  These source/C++ counts are not a
synthesized SRAM area claim.

## Implemented policy

For a registered A aperture, linearize each decoded
`(row, bank-group, bank)` tuple into a monotonic row key.  Split the aperture's
row-key interval into four equal contiguous ranges.  For partition `p=0..3`:

1. sequentially read every B line from coherent memory;
2. decode A[B[i]] and discard the temporary descriptor unless its row key is
   in range p;
3. admit at most K=4,096 `(i,wid,line,row)` records;
4. group/issue first-seen rows and lines, place responses by `i`, and drain;
5. if a range contains more than K records, repeat step 3 within the same scan;
6. advance only after the bounded epoch is empty.

The bounds depend on the registered A range and DRAM geometry, not a histogram
of B.  Thus there is no discovery pass, N-entry selector array, descriptor
spill, or oracle issue list.  B values not admitted are not retained on chip;
later partitions reread them through the coherent hierarchy.  The worst case
is four DRAM fetches if the 64 KiB B stream is not LLC-resident.

## Hardware and traffic ledger

The default FP64 K=4K bit-packed lower bound is reproduced from the same field
formulas as `experiments/scripts/report_maa_storage.py`:

| State | Formula | Bytes |
|---|---:|---:|
| Offset records | `4096 * (15-bit i + 3-bit wid + valid)` | 9,728 |
| Row/line entries | `4096 * (64-bit line + 2*15-bit heads + valid)` | 48,640 |
| Row headers | `512 * (64-bit grow + valid + sent)` | 4,224 |
| Logical invalidator lower bound | fixed 16K aperture | 4,096 |
| **Reorder/invalidator subtotal** |  | **66,688** |
| pass/cursor/count/drain control | 31 bits rounded once | 4 |

The comparable full bounded-mechanism ledger charges every previously defined
finite buffer rather than counting only the tables:

| Component | Bytes |
|---|---:|
| 32-tile, 4K-physical SPD | 524,288 |
| bounded B feeder | 8,192 |
| finite A response pool | 3,840 |
| finite C combiner | 24,576 |
| readiness, tags, write tracking, and other bounded control | 25,554 |
| 4K Row/Offset/invalidator | 66,688 |
| new partition control | 4 |
| **Total lower bound** | **653,142** |

The corresponding 16K-active metadata is 254,464 B.  A frozen 4K-physical but
full-16K-metadata mechanism ledger is 842,482 B.  Moving to this treatment
removes 189,340 B from that lower-bound total, while replacing it with three B
rereads and finite selector work.  A genuinely full-physical native16 ledger
also contains 1,572,864 additional SPD bytes (32 tiles times 12,288 saved
elements times four bytes), yielding 2,415,346 B under the same comparison.
These are capacity lower bounds, not area, timing, or energy estimates.

Per 16K operation:

| Traffic/work | native16 | native4k | bounded rows |
|---|---:|---:|---:|
| B passes | 1 | 1 across four calls | 4 |
| B semantic bytes | 65,536 | 65,536 | 262,144 |
| B line requests | 1,024 | 1,024 | 4,096 |
| B reread bytes/lines | 0 / 0 | 0 / 0 | 196,608 / 3,072 |
| Selector words/cycle lower bound | 0 / 0 | 0 / 0 | 65,536 / 4,096 |
| Reorder backing records/traffic | 0 / 0 | 0 / 0 | 0 / 0 |

## Deterministic trace comparison

The trace formulas are taken directly from
`benchmarks/API/test_virtual_tile_consumer.cpp:79-109` and
`benchmarks/API/test_virtual_index_gather.cpp:31-54`.  Full order/output hashes
are in `results_summary.json`; the complete report is reproducible with
`python3 bounded_row_model.py`.

| Trace | Policy | A-line requests | Row transitions | Epochs / skew drains | Gate |
|---|---|---:|---:|---:|---|
| tile-consumer FP64 | native16 | 9,523 | 128 | 1 / 0 | control |
|  | native4k | 16,384 | 515 | 4 / 0 | control |
|  | bounded rows | 9,670 | 152 | 5 / 1 | pass |
| virtual-index random FP32 | native16 | 4,096 | 32 | 1 / 0 | control |
|  | native4k | 13,436 | 131 | 4 / 0 | control |
|  | bounded rows | 4,659 | 41 | 5 / 1 | pass |
| fanout FP32 | native16 / native4k / bounded | 1 / 4 / 4 | 0 / 0 / 0 | 1 / 4 / 4; bounded drains 3 | **reject** |
| same-line FP32 | native16 / native4k / bounded | 1 / 4 / 4 | 0 / 0 / 0 | 1 / 4 / 4; bounded drains 3 | **reject** |
| line-revisit FP32 | native16 / native4k / bounded | 7,379 / 16,129 / 7,626 | 256 / 782 / 272 | 1 / 4 / 5 | pass |

`native16` and `native4k` in this table are trace-model controls: one N-sized
first-seen row/line grouping versus four sequential K-sized groupings.  They
are not cycle-exact emulations of native slice arbitration or row-capacity
drain timing.  The executable model makes no simTicks claim.

## Frozen gem5 controls

No new gem5 job was launched while the hybrid worker owned live validation.
Instead, the frozen matched controls were re-audited in evidence-checklist
order.  They share the exact gem5 binary, benchmark binary, benchmark source,
IndirectAccess source, and Ramulator config hashes recorded in
`gem5_control_evidence.json`; both wrappers returned zero, reached `m5_exit`,
contain final stats, and match exact output:

| Control | Exact output | ROI simTicks | A-line descriptors / unique | Row descriptors / unique |
|---|---|---:|---:|---:|
| native direct 16K | `hash=7228541527853630339 errors=0` | 40,874,044 | 9,858 / 9,523 | 1,458 / 129 |
| four native direct 4K calls | same hash, `errors=0` | 60,408,687 | 16,384 / 16,384 | 2,108 / 516 |

The 4K control is 47.792% more ROI ticks in this one deterministic observation,
but that delta combines physical capacity, call boundaries, and reorder scope.
It is context, not a timing estimate for the unimplemented bounded-row
treatment.  There is one repetition and no candidate gem5 result, so no
speedup or promotion claim follows.

## 64K logical over 16K active mapping (not implemented)

The same ratio uses four aperture-derived row intervals and a 16,384-entry
active epoch.  With 4-byte B and FP64 A:

- four B scans = 1,048,576 B and 16,384 line requests;
- rereads = 786,432 B / 12,288 lines;
- selector work = 262,144 words, at least 16,384 cycles at 16 words/cycle;
- FP64 Row/Offset/headers/invalidator lower bound = 279,040 B;
- partition control = 35 bits, rounded once to 5 B.

An overweight interval still drains; it does not gain a hidden 64K mapping.
The physical SPD, response, combiner, readiness, and write-tracking ledger must
be rescaled and revalidated before implementation.  Full 64K is explicitly
out of scope this week.

## Handoff and gate

Keep this result as model evidence only.  A later gem5 implementation should be
requested from the owner of `IndirectAccess.cc`/`Tables.cc` after the live
hybrid work ends.  The screen must freeze the same binary/input/mapping and run:

1. native direct 16K;
2. four native direct 4K calls;
3. one-pass 16K logical with 4K Offset/Row epoch;
4. arm 3 with four contiguous decoded-row intervals and a finite selector.

Reject the candidate unless exact output and lifecycle checks pass, peak live
descriptors remain <=4,096, all 65,536 selector words are charged, and both A
source-line descriptors and inserted decoded rows strictly decrease versus arm
3.  Reject any N-sized selector/completion/issue-order store or uncharged
backing transfer.  Only then inspect simTicks.
