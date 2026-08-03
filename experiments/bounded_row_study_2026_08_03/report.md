# Finite bounded-row model repair (2026-08-03)

## Binding outcome

Commit `62b181af75260193e36f55095d4165cd4cba0858` remains rejected as
implementation-authorizing evidence. This successor is **model evidence only**.
It closes the finite-state, validation, accounting, provenance, and handoff
defects, but it intentionally removes the prior workload A-line/row comparison.

The frozen 2026-08-02 runs do not record each B physical address and translated
A physical line, Ramulator fields, RowTable slice, and `grow_addr`. Their
`MAAVirtualTrace` files contain lifecycle/build counters only. Therefore the old
`source_line_offset=17` placement and all numbers derived from it are gone.
`extract_grounded_trace.py` fails closed on both frozen logs and states that a
new trace run is required. No paddr, slice, row, or alignment is inferred.

No production simulator source was edited or claimed. In particular, this
session did not request `IndirectAccess` or `Tables` ownership.

## What is now executable

`bounded_row_model.py` implements the prospective 4K mechanism with fixed
arrays constructed at initialization:

| State | Exact bound |
|---|---:|
| Offset entries | 4,096 |
| RowTable slices | 16 |
| Rows per slice | 32 |
| Row slots | 512 |
| Lines per row slot | 8 |
| Line slots | 4,096 |
| Response descriptors | 96 |
| Response words | 480 |

Policy admission and issue selection use no `dict`, `set`, `OrderedDict`, or
append-only container. Offset, row, line, cursor, response-count, and drain
state have fixed charged sizes. Lists/sets used to collect test results,
preflight an evidence envelope, or hash the emitted issue stream are explicitly
validation oracles; policy never reads them to admit or order a request.

### Admission and drains

All fields of a physical record are checked before the model constructs its
policy tables. A B index must be an actual integer (a Python `bool` is rejected)
and must be in `[0, source_elements)`. B paddr alignment, A line alignment,
channel/rank/bank-group/bank/row/column/wid ranges, slice aperture, duplicate
iteration IDs, and missing iterations are also rejected before mutation.

Insertion mirrors the native finite search:

1. append to an identical unsent `(slice, grow, A line)`;
2. use a free line slot in an existing row slot with that grow;
3. use the first free row slot in that slice;
4. otherwise drain and retry the same validated B word.

The ninth distinct line for one grow uses a second finite row slot, matching
native `RowTableSlice::insert`; no line container grows past eight. When all 32
row slots in one slice are occupied, the model drains. Offset 4,097 drains at
exactly 4,096. A line chain drains at 480 offsets so that the frozen 480-word
response descriptor can represent it. All drain causes are separately counted.

### Real slice and issue order

For the frozen one-channel, one-rank, four-bank-group, four-bank organization,
the slice is exactly `bankgroup * 4 + bank`, matching
`IndirectAccess.cc:getRowTableIdx`. At 16 slices, `grow_addr` is the decoded DDR
row, matching `getGrowAddr`; bank group and bank are retained in the slice.

Each slice has independently frozen lower and exclusive-upper physical grow
bounds. Four contiguous intervals are computed inside that slice. They are not
derived from the observed B histogram. A record outside its registered slice
aperture is rejected.

Issue creation reproduces the native constructor/traversal order: bank outer,
bank group inner, giving
`[0,4,8,12,1,5,9,13,2,6,10,14,3,7,11,15]`. Within a slice it uses the first
valid row slot, other row slots with the same grow, and valid line slots in slot
order. One line per live slice is selected per traversal round. Per-slice grow
changes, rather than a misleading global cross-bank row sequence, are counted
as row transitions.

The exact source grounding is repository parent
`9393bf09f9318d31b1f8406d839cc2510690e47d` plus frozen snapshots. The audit
verifies `Tables.cc` SHA-256
`48befc4a8185cd9fea0e9032e805301eb58e08eb54d8b2fbd6b8f469eeac8659`,
`Tables.hh` SHA-256
`90b8cc8429f9fc68a46b55dae06d896cebcb51460d6be45beb962eb0d2de4a46`,
and the per-control `IndirectAccess`/MAA snapshots listed in
`gem5_control_evidence.json`.

### Source-equivalent versus prospective behavior

The following are source-equivalent: slice derivation, 16-slice traversal,
first-free row/line insertion order, linked Offset semantics, eight line slots,
96 response slots, 480 response words, and response reservation before request
creation.

The following remain prospective model behavior: 32 rather than the frozen
control's 64 row slots per slice; four per-slice contiguous aperture intervals;
draining before a line chain exceeds 480 words (the current bounded source path
would reject an oversized single response); fill-then-drain epochs; and
immediate build-round credit return. These approximations are adequate for
finite geometry/adversarial checks, not gem5 timing or workload traffic claims.

## Required adversarial results

The executable unit suite covers the binding cases:

| Case | Result |
|---|---|
| 4,096 distinct rows, round-robin across 16 slices | peak 512 rows; 7 row-capacity drains; 8 epochs |
| 9 lines in one grow | 2 row slots, 9 line slots; no overflow |
| 257 lines in one grow/slice | peak 32 rows and 256 lines; 1 row-capacity drain |
| 4,096 offsets on one line | 480-word peak; 8 descriptor drains; 9 A requests/epochs |
| all 4,096 records in one partition | exact 65,536 selector words; no hidden selector state |
| exact 4,096 Offset boundary | one epoch, no capacity drain |
| 4,097 Offset boundary | one Offset drain, two epochs |
| malformed/bool/out-of-range B index | rejected before policy table construction |

Every successful case has exactly one placement per logical iteration and
never exceeds its fixed arrays. These are synthetic geometry and semantic
checks. Their output hash is deliberately labeled
`synthetic_semantic_check_only`; it is not the workload oracle.

## Physical trace and output evidence

The new extractor requires a strict metadata envelope with source commit, gem5
binary hash, benchmark hash, checkpoint hash, exact oracle, all 16 registered
slice bounds, and one record per iteration containing B paddr/index and A
paddr/channel/rank/bank-group/bank/row/column/slice/grow/wid. Missing,
duplicate, malformed, inconsistent, or out-of-range fields fail closed.

The real frozen workload oracle remains
`hash=7228541527853630339 errors=0`. Only `audit_gem5_controls.py` claims that
oracle, after hashing the containing logs and all other artifacts it consumes.
The model does not attempt to reproduce that benchmark's multiply/store hash.

## Frozen controls and B accounting

The schema-2 control manifest records exact paths and SHA-256 hashes for both
gem5 runs' `restore.exit`, `restore.log`, `result.tsv`, final `stats.txt`, run
config, checkpoint config, run manifest, lifecycle trace, source snapshots, and
Ramulator config. It also verifies the exact gem5 and benchmark binary paths and
hashes. Claimed TSV values are accepted only after `result.tsv` itself hashes.

The controls have identical consumed MAA/IndirectAccess/benchmark/Ramulator
snapshot hashes, but their recorded Git commits are honestly different:
`a9d3821d...` for native16 and `d17fe737...` for native4K.

| Control | Exact output | ROI simTicks | observed B lines | A lines / unique | rows / unique |
|---|---|---:|---:|---:|---:|
| native direct 16K | frozen hash, errors=0 | 40,874,044 | 1,025 | 9,858 / 9,523 | 1,458 / 129 |
| four native direct 4K calls | same hash, errors=0 | 60,408,687 | 1,028 | 16,384 / 16,384 | 2,108 / 516 |

The 64 KiB B stream is not line-aligned: one whole-stream call touches 1,025
lines, while four 16 KiB calls touch `4 * 257 = 1,028`. The exact byte offset is
not present and is not invented. A prospective four-pass scan of that same
whole physical stream therefore projects `4 * 1,025 = 4,100` B-line touches,
including 3,075 rereads, and 262,144 semantic bytes. This is alignment-correct
projected work, not an observed candidate counter. The old 4,096/3,072-line
claim is removed.

The two controls have one repetition, treatment-specific checkpoints and
physical/call geometry, and no bounded-row candidate. Their tick difference is
context only; no speedup or promotion claim is made.

## Complete byte-addressable ledger

`storage_ledger()` computes every subtotal from field widths. Each field array
element rounds independently to a byte width; fields are not optimistically
packed across entries. Offset `next`, line head/tail/count/claim bits, response
descriptor identity, response payload ownership, invalidator state, all 128
partition boundary fields, per-slice cursors, selector/drain/state counters,
response occupancies, unit/instruction/generation ownership, A/B bases, logical
and source bounds, word-size/destination fields, placement count, and pending
writes are charged.

| Component | 16K logical / 4K active | 64K logical / 16K active arithmetic |
|---|---:|---:|
| Offset arrays | 24,576 | 98,304 |
| Row arrays | 2,048 | 8,192 |
| Line arrays | 65,536 | 262,144 |
| 96 descriptors + 480 response words/owners | 6,720 | 6,720 |
| Logical invalidator | 32,768 | 131,072 |
| Per-slice partition bounds/valid | 448 | 448 |
| Per-slice traversal cursors | 96 | 96 |
| Operation controls | 44 | 47 |
| **Charged total** | **132,236** | **507,023** |

The 64K/16K column is arithmetic only: it uses 2,048 rows (128/slice) and
16,384 line/Offset slots. It is not an implemented configuration. The old
66,688/67,200 optimistic metadata values, hardcoded four/five-byte partition
control, 279,040-byte 64K subtotal, and 653,142/842,482 full-mechanism totals
are removed. A full SPD/combiner/readiness/writeback total is withheld until
all of those components are separately rescaled and field-complete.

## Finite future gem5 ownership/completion contract

`future_gem5_screen_contract.json` is non-authorizing but concrete. A future
owner must bind one operation by `(indirect_unit_id, instruction_id,
generation)` from validated B admission through terminal completion. Slot
reservation is atomic. Every source request owns a generation-tagged tuple of
slice, row slot, line slot, paddr, Offset head, and count. A response must match
that full identity. Response slots/words and Offset chains release only after
consumption.

Partition advance requires zero live table entries, zero response reservations,
and zero pending writes. Success additionally requires partition 3 complete,
the exact logical placement count, the exact workload oracle, and zero ownership
counts. An error rejects new admission but retains ownership until all accepted
requests drain, then enters `TERMINAL_ERROR`.

After explicit ownership transfer, the four required screen arms are native
16K, four native 4K calls, one-pass logical16K/finite4K, and four-pass
physical-bounded rows. Gate order is artifact/completion audit, exact output,
finite ownership/capacity, grounded physical A-line and per-slice transition
comparison, then—and only then—`simTicks`.

Until the strict physical trace extractor succeeds and all four arms meet this
contract, the only coherent conclusion is: **retain finite model evidence; new
trace required; do not authorize implementation or performance claims**.
