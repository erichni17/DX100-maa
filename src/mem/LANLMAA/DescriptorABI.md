# LANLMAA descriptor ABI

The optional CPU-visible mode accepts a 64-byte little-endian version-1
descriptor from a fixed physical slot table. Opcodes 7 and 8 are version-2
formats and consume the submitted slot plus its immediately following slot.
A 64-bit write to doorbell offset `8 * slot` submits that slot. One descriptor
executes at a time. A doorbell while any descriptor traffic or execution is
active is acknowledged but counted as a busy rejection. After a descriptor
reaches `Completed` or drained `Error`, a later doorbell explicitly rearms the
existing operation, line, and continuation structures and submits its slot.
There is no hidden descriptor queue: software must observe the completion
record or terminal status before submitting the next descriptor.

## Common descriptor fields

| Offset | Width | Field |
| ---: | ---: | --- |
| 0 | 4 | Magic `LMA1` (`0x31414d4c`) |
| 4 | 2 | Version `1` |
| 6 | 1 | Opcode |
| 7 | 1 | Opcode flags; zero except for opcodes 4 and 6 |
| 8 | 4 | Item count, nonzero and no larger than `max_descriptor_items` |
| 12 | 4 | Reserved, must be zero |
| 16 | 8 | Address or start-index vector |
| 24 | 8 | 64-bit result vector |
| 32 | 8 | 32-byte completion record |
| 40 | 24 | Opcode-specific fields |

The three common ranges must be 64-bit aligned, non-overlapping, mapped
memory, outside the control aperture, and outside the descriptor table. All
items must fit the configured operation window.

Opcode `1`, `DirectGather`, interprets the vector at offset 16 as 64-bit
physical addresses and writes one gathered 64-bit value per item. Bytes 40
through 63 must be zero.

Opcode `2`, `IndexedCellWalk`, uses these opcode-specific fields:

| Offset | Width | Field |
| ---: | ---: | --- |
| 40 | 8 | 16-byte-aligned record-array base |
| 48 | 4 | Record count, nonzero |
| 52 | 4 | Maximum records consumed per item, nonzero |
| 56 | 8 | Terminal index, which must be outside the record array |

The vector at offset 16 contains 64-bit start indices. Each record is the fixed
little-endian pair `{uint64_t next_index, uint64_t payload}`. The engine sums
payloads until `next_index` equals the terminal index and writes the sum. An
initial or continuation index outside `record_count`, any unsafe range, or a
walk that reaches the step bound before its terminal fails the entire
descriptor. No result or completion is published on failure. Already accepted
memory requests are drained before the error state is exposed.

This record pair is a staging ABI, not the native layout of Branson `Cell` or
SPARTA `Grid::ChildCell`. Software integration must construct an explicit
indexed record view; the prototype does not claim transparent application ABI
compatibility.

Opcode `3`, `PackedDirectionalCellWalk`, is a narrower SPARTA-derived contract
that avoids replicating records by direction and remaining-visit count. It
uses these opcode-specific fields:

| Offset | Width | Field |
| ---: | ---: | --- |
| 40 | 8 | 8-byte-aligned packed-cell array base |
| 48 | 4 | Cell count, nonzero and at most `2^24` |
| 52 | 4 | Maximum visits per item, nonzero |
| 56 | 8 | Reserved, must be zero |

Each 64-bit start-state word contains a 24-bit start-cell index in bits 0--23,
a positive-direction bit in bit 24, a 32-bit nonzero visit count in bits
25--56, and reserved zeros in bits 57--63. The visit count must not exceed the
descriptor maximum. Each 8-byte packed cell stores a 24-bit positive neighbor
in bits 0--23, a 24-bit negative neighbor in bits 24--47, and reserved zeros
in bits 48--63. Every selected neighbor is range-checked before its access.

The engine adds `current_cell_index + 1` for each visit and retires the item
when its retained visit count reaches zero. This derived checksum validates
direction and continuation state without adding a payload field. Compared
with the opcode-2 state-expanded SPARTA staging baseline at eight visits,
opcode 3 stores one 8-byte record rather than sixteen 16-byte records per
cell. The packed record is still a microbenchmark ABI, not native SPARTA
`Grid::ChildCell`; child/parent/surface predicates and six-field FP64 tallies
remain outside this opcode.

Opcode `4`, `FaceMinMax`, is a narrow EAP/FLAG-derived face loop. It reuses
the common fields as follows:

| Offset | Width | Field |
| ---: | ---: | --- |
| 16 | 8 | Base of one packed 64-bit face word per item |
| 24 | 8 | 32-byte-aligned base of 32-byte cell records |
| 32 | 8 | 32-byte completion record |
| 40 | 8 | 8-byte-aligned base of four FP64 output arrays |
| 48 | 4 | Cell count, nonzero and at most `2^31` |
| 52 | 4 | Face-value element count |
| 56 | 8 | 8-byte-aligned face-value vector base |

Flags bits 0--1 select the internal-face mode: zero is normal interpolation,
one is density guarded, two is pressure weighted, and three is reserved.
Flag bit 2 selects the external face-value vector for boundary faces. Bits
3--7 are reserved. When bit 2 is clear, offsets 52--63 must be zero. When it is
set, the face-value count must be nonzero and at most `2^31`, and the complete
range must be mapped, non-overlapping, outside MMIO, and outside the descriptor
table.

A face word stores two 31-bit payloads in bits 0--30 and 31--61, and a two-bit
kind in bits 62--63:

| Kind | Meaning | Payload 0 | Payload 1 |
| ---: | --- | --- | --- |
| 0 | Inactive | Ignored/poison-safe | Ignored/poison-safe |
| 1 | Internal (`face_id > 2`) | Low-cell index | High-cell index |
| 2 | Low boundary (`face_id == 2`) | Low-cell index | Face-value ordinal, or canonical zero |
| 3 | High boundary (`face_id == 1`) | High-cell index | Face-value ordinal, or canonical zero |

This is backward compatible with the original encoding: kind zero is the
false predicate and kind one is the old bit-62 active face. An inactive face
retires without checking either poison payload and without cell reads or
updates. Every required cell index and face-value ordinal is checked before
its derived request. Opcode 4 also requires `item_count` not to exceed the
configured continuation-context count because the gather-before-update
barrier retains one context for every potentially active face.

Normal mode uses a 32-byte, 32-byte-aligned little-endian FP64 cell record
`{half_low, half_high, value_low, value_high}`. Density-guarded and pressure
modes use a 40-byte, 40-byte-aligned record that appends `rho`. For an internal
`(low, high)` face, normal mode computes

`(half_low[high] * value_high[low] + half_high[low] * value_low[high]) /
 (half_low[high] + half_high[low])`.

Density-guarded mode first gathers `rho[low]` and `rho[high]`. If both are
nonpositive, the face value is zero after two gathers; otherwise it performs
the four normal gathers. Pressure mode uses the same guard, then gathers
`value_low[low]` and `value_high[high]`. When their product is nonpositive it
weights the two interpolation coefficients by `rho[high]` and `rho[low]`,
respectively; otherwise it uses the normal coefficients. A live pressure face
therefore performs eight gathers. The controller reduces the sign test and
then the two coefficients while streaming, so it retains at most three FP64
scalars: the already-modeled operation value plus the two continuation scalar
registers. It does not add accelerator array payload.

A low boundary gathers either `value_high[low]` or its face-value ordinal and
updates only `low_min/low_max`. A high boundary gathers either
`value_low[high]` or its face-value ordinal and updates only
`high_min/high_max`. Internal faces update all four arrays. The four contiguous
`cell_count`-element output arrays are `high_min`, `high_max`, `low_min`, and
`low_max`; the engine issues coherent FP64 MIN/MAX atomics to the corresponding
high or low cell.

Every gathered field, required denominator, coefficient result, and final
face value must be finite, and every required denominator must be nonzero.
Every active face completes and validates all of its gathers before any
output atomic is permitted. A failure drains accepted reads and publishes no
output atomic or completion; every successful context remains allocated until
all of its exact atomic acknowledgements return. This is the directly verified
arithmetic and indexing shape of EAP Patterns `inside_com3b`, not a native EAP
mesh ABI, application-correctness result, physical FP datapath cost, or
application-speedup claim.

### UMT ordered eight-corner wave contract (opcode 11)

Opcode 11 is a version-2, 192-byte contract occupying three adjacent
64-byte descriptor slots. Byte 7 must equal one and promises an exactly
eight-corner positive UCB zone whose records and results are arranged in the
CPU-provided topological solve order. Opcode 10 remains unchanged as the
per-corner baseline.

| Offset | Size | Meaning |
| ---: | ---: | --- |
| 8 | 4 | Group count, 1-32 |
| 12 | 4 | Record stride, exactly 192 bytes |
| 16 | 8 | Record base |
| 24 | 8 | Result base |
| 32 | 8 | 32-byte completion record |
| 40 | 8 | Reserved, zero |
| 48 | 8 | ABI fingerprint `0x215544d46139a30b` |
| 56 | 4 | Corner count, exactly 8 |
| 60 | 4 | Stored nonzero edge count, 0-12 |
| 64 | 4 | 28-bit strict-upper edge mask in source-major order |
| 68 | 4 | Reserved, zero |
| 72 | 0-96 | Packed nonzero FP64 coefficients in ascending mask-bit order |
| 72+8N | through 191 | Reserved, zero |

Each group record is three ordinal-major FP64 vectors:
`source[8]`, `sum_area[8]`, and `sigma_times_volume[8]`. The 28-bit mask
reconstructs the shared strict-upper coefficient triangle; at most twelve
nonzero coefficients are stored and absent edges reconstruct as zero. Set bits
with zero or nonfinite coefficients, a population/count mismatch, and nonzero
padding are noncanonical and rejected. For each ordinal, the
engine adds the two denominator terms, divides the current retained source,
and applies every nonzero forward coefficient as one multiply followed by one
add to a later retained source. It returns `flux[8]` per group and reports
`group_count * 8` acknowledged result writes. The decoder rejects nonfinite
coefficients, bad sizes, reserved bits, range overflow, pairwise overlap, and
a descriptor that extends beyond the configured table.

The native UMT integration performs incident-face and EZ correction assembly
on the CPU, submits the whole ordered zone once per group batch, installs all
eight returned angular fluxes, and skips the CPU's downstream recurrence.
Thus opcode 11 tests the synchronization and retained-dependency boundary; it
does not claim to offload the earlier source-assembly physics.

The timing contract constructs the actual sparse dependency DAG and schedules
it on one FP64 add/sub unit, one FP64 multiplier, eight iterative dividers
(64-cycle latency and initiation interval), and global issue width one. Per
group it charges eight denominator adds, eight divides, and one multiply/add
pair per nonzero edge. Architecturally retained group state is eight mutable
sources plus eight denominators; at most twelve coefficients are
descriptor-global. The compact form removes five descriptor fetches per
submission relative to the padded version-1 prototype.
The C++ vectors used by the functional simulator are not an SRAM sizing
claim. Synthesis, wiring, power, and physical timing remain unproven.

## Completion and control records

Successful descriptors write a 32-byte completion record:

| Offset | Width | Field |
| ---: | ---: | --- |
| 0 | 4 | Magic `LMAC` (`0x43414d4c`) |
| 4 | 2 | Version `1` |
| 6 | 1 | Completed opcode |
| 7 | 1 | Status zero |
| 8 | 4 | Completed slot |
| 12 | 4 | Reserved zero |
| 16 | 8 | Item count |
| 24 | 8 | Acknowledged result writes; logical updates for opcodes 4, 6, and 8; replayed events for opcode 5; direct tally writes for opcode 7 |

The control aperture exposes device/version at `0x100`, slot and item limits
at `0x108`, state at `0x110`, completed slot at `0x118`, error code at `0x120`,
and an opcode bitmap at `0x128`. Bitmap bit `n` advertises opcode `n`; bits
1--8 are currently set.

### Branson event-replay contract (opcode 5)

`BransonEventReplay` has an independently tested decoder in
`BransonEventDescriptor.hh` and a live two-pass engine path. The first pass
validates every root and event chain without issuing tally updates. Only after
that traffic is quiescent does the second pass replay the same immutable event
records through the timed event-control units and acknowledged FP64 tally
atomics. The 64-byte descriptor uses the common magic and version and requires
zero flags and reserved fields:

| Offset | Size | Meaning |
| ---: | ---: | --- |
| 8 | 4 | Root count |
| 16 | 8 | 32-byte-aligned root-record base |
| 24 | 8 | Base of two FP64 tally arrays |
| 32 | 8 | 32-byte completion record |
| 40 | 8 | 32-byte-aligned event-record base |
| 48 | 4 | Event-record count |
| 52 | 4 | Maximum events per root |
| 56 | 4 | Cell/tally count |

Each 32-byte root is `{first_event, event_count, initial_cell, final_cell,
terminal_kind, reserved[3]}` as eight little-endian 32-bit words. The explicit
initial cell makes a corrupted first-event source detectable. Each 32-byte
event is `{source_cell,
destination_cell, next_event, kind, absorbed_delta_bits, track_delta_bits}`;
the one-byte kind is followed by three reserved zero bytes, and each delta is
finite FP64. `next_event == 0xffffffff` is terminal. The two tally arrays are
absorbed then track, each with `cell_count` FP64 elements. The decoder rejects
empty or excessive root counts, zero event/cell/step bounds, misalignment,
range overflow, reserved bits, and every pairwise overlap among roots, events,
tallies, and completion. Runtime validation checks every source/destination,
chain link, terminal record, and FP64 delta. A descriptor-owned validation
failure drains accepted reads and publishes neither tally updates nor a
completion record. Timed event-control work already queued or issued by other
contexts has no external effect and is explicitly counted as cancelled, so
queued and issued work remain auditable across that drain.

Software owns a submitted descriptor's roots and events exclusively until the
terminal status is visible: those buffers must not be mutated concurrently.
The staged timing parameters model event decode/control latency, initiation
interval, replicated units, and continuation-context quantum. They do not
model Branson RNG, logarithm/exponential evaluation, geometry, or native
application integration, so this opcode alone is not an application-speedup
claim.

`branson_active_context_limit` optionally limits opcode-5 admission below the
physical continuation-table capacity; zero selects the physical capacity. The
limit reuses the existing active-context count and forces the same update-drain
path as a physically full table, so pending roots cannot deadlock behind
undrained atomics. Other opcodes continue to use all physical continuation
entries. `bransonContextThrottleCycles` counts only cycles blocked by this
logical Branson limit below physical capacity. The limit is a workload-specific
control/comparator contract, not a claim that the physical continuation SRAM
has been reduced or that its area, timing, or energy has been synthesized.

The physical-state mapping overlays each retained root's
`{first_event,event_count}` and `{initial_cell,final_cell}` pairs onto the
existing operation-entry value and index words, with terminal kind in existing
flags. An active event uses the existing continuation address and index plus
three scalar words: packed `{destination_cell,next_event}` and the two FP64
deltas. At 64 operation entries, default latency four, one unit, and quantum
four, the additional nominal timing/scheduler state is four valid plus six-bit
context tags and an eight-bit preferred-context/quantum cursor (36 bits total),
apart from global phase and statistics counters. Thus the mapping adds no large
event buffer to the existing rounded array-payload budget. This is a
transparent bit mapping, not synthesis, port, timing, energy, or area evidence.
The 32-byte root record does add 16 external bytes per root relative to the
earlier 16-byte reference-replay staging record, and two-pass validation reads
every event twice on a successful uncached replay.

### SPARTA six-tally contract (opcode 6)

`SpartaSixTally` accelerates the exact six-channel cell accumulation shape
verified in pinned SPARTA `compute_thermal_grid_kokkos.cpp`: count, mass,
three momentum components, and kinetic-energy contribution. The CPU or
application framework forms the contributions; this contract covers their
indexed scatter-add, not particle physics or native application integration.
The descriptor reserves byte 7 for two mutually exclusive, opt-in policies:

- bit 0 permits one bounded younger accumulating generation behind a draining
  same-address update;
- bit 1 requires nondecreasing cell indices, partitions staging into fixed
  four-item blocks, and holds each same-cell/channel subgroup until every
  member of that block has joined.

Software may assert either bit only after materializing the selected indices
and contributions in cell-major order. Bits 0 and 1 together, any other flag
bit, or a decreasing cell index under bit 1 fail closed. Flag zero retains the
baseline policy. The remaining reserved fields are zero:

| Offset | Size | Meaning |
| ---: | ---: | --- |
| 8 | 4 | Item count |
| 16 | 8 | Base of one little-endian `uint32_t` cell index per item |
| 24 | 8 | Base of cell-major `double[cell_count][6]` tallies |
| 32 | 8 | 32-byte completion record |
| 40 | 8 | Base of item-major `double[item_count][6]` contributions |
| 48 | 4 | Cell count |
| 52 | 4 | Channel count, exactly 6 |
| 56 | 8 | Reserved, zero |

All four ranges must be aligned, mapped, pairwise disjoint, outside MMIO, and
outside the descriptor table. Every cell index is loaded and range-checked
before execution. Execution then uses two passes over immutable contribution
storage. The first pass checks that every FP64 contribution is finite and
issues no updates. Only after all first-pass traffic and operations quiesce
does the second pass reread contributions and issue relaxed FP64 adds to
`tally[cell][channel]`. Completion is published only after exactly
`item_count * 6` logical update acknowledgements. A validation failure drains
accepted reads and publishes neither a tally update nor completion. Software
must not mutate the cell-index or contribution arrays until terminal status;
a nonfinite value observed after validation is treated as an ownership
violation.

The retained item index, cell index, and three-bit channel ordinal overlay
existing opcode-specific operation-entry fields. Contributions stream through
the existing scalar value word and FP64 update combiner, so this mapping adds
no second accumulator or update table. Cell-group mode reuses one descriptor
wire bit and derives the group from the retained item ordinal, but adds a group
identity to each update entry so out-of-order contribution returns cannot mix
adjacent staging blocks. For 64 operation and update entries, the minimum tag
is four bits per update entry, or 256 logical bits total; this state must be
charged even if a physical array has enough rounding slack. That is a
structural mapping only: it does not price ports or arbitration, establish
synthesis timing/area/energy, provide a native SPARTA ABI, or demonstrate
application speedup.

### Banked line-merge table contract

The selected 32-entry line table is four banks with eight entries per bank,
not an unpriced 32-way multiported search. Consecutive 64-byte lines select
consecutive banks. Each bank resolves at most one distinct line address in a
cycle. Two logical slots targeting the same line share that lookup and both
join the retained waiter list; two different lines mapping to the same bank
leave the younger operation ready and increment `lineBankConflictCycles`.
Allocation and matching search only the selected bank, so capacity cannot be
borrowed from another bank. `line_banks` must be a power of two, divide
`line_entries`, and not exceed it. This establishes functional arbitration and
bounded contention accounting; metadata SRAM/CAM synthesis and whole-path
timing, area, power, or speedup remain unclaimed.

`Completed` and `Error` remain visible until the next doorbell. A terminal
rearm clears the previous error and per-descriptor cursors only after all
retained packets, operation contexts, line entries, and update entries are
quiescent. The completion record remains the durable per-submission result.

### Native SPARTA fused-cell contract (opcode 7)

Opcode 7 is a version-2, 128-byte contract that consumes two adjacent
version-1 descriptor slots. A submission from the final physical slot fails
closed because it cannot own a successor slot. The opcode bitmap advertises
bit 7, and the live decoder fetches and validates both slots before issuing
source traffic. `SpartaFusedCellModel.hh` remains the independent decoder and
functional oracle for the live path.

Byte 7 must equal one. The bit promises that the six target tally channels are
zero and CPU-exclusive until completion. The two-slot fields are:

| Offset | Size | Meaning |
| ---: | ---: | --- |
| 8 | 4 | Cell count, 1-64 |
| 12 | 4 | Particle count, 1-64 |
| 16 | 8 | Native CPU `ChildInfo` base |
| 24 | 8 | Native signed 32-bit `next` base |
| 32 | 8 | Native CPU `OnePart` base |
| 40 | 8 | Native CPU `Species` base |
| 48 | 8 | Native signed 32-bit `species2group` base |
| 56 | 8 | Target tally base |
| 64 | 8 | 32-byte completion record |
| 72 | 8 | CPU-layout fingerprint `0xa34d454519758371` |
| 80 | 4 | Nonzero cell group bit |
| 84 | 4 | Nonnegative target mixture group |
| 88 | 4 | Positive species count |
| 92 | 4 | Tally cell stride, aligned and at least 48 bytes |
| 96 | 32 | Reserved, zero |

The pinned layout is 104-byte `OnePart` with `ispecies`, `icell`, and `v` at
offsets 4, 8, and 40; 192-byte `Species` with `mass` at 24; and 64-byte
`ChildInfo` with `count`, `first`, and `mask` at 0, 4, and 8. The reference
decoder rejects a changed fingerprint, misalignment, 48-bit range overflow,
and any pairwise range overlap.

The live scheduler admits at most eight cells, issues their field reads in
round-robin order, follows every declared list in order, and uses a 64-bit
particle-visit bitmap to reject duplicates, cycles, early/late terminals,
wrong-cell records, and incomplete coverage. Eligible records produce six
finite FP64 contributions and update a retained per-cell summary. After source
validation, a separate pass reads all six target words for every cell and
requires each to compare equal to zero. No external tally write occurs until
both passes quiesce. Success directly writes six sums for every cell with an
eligible particle and requires every coherent write acknowledgement before
completion. A pre-write failure discards all summaries, leaving the
promised-zero tally safe for scalar fallback.

The simulator's 64 operation objects are not a physical-structure claim. The
six sums plus eligible/valid state live in an explicit 64-entry paired-summary
store that reuses one 256-bit operation entry and one 384-bit continuation
entry per cell. Its four shared pair banks accept at most one summary access
per bank per cycle; a conflicting traversal remains ready and records a stall
cycle. Particle, remaining-count, mask, species, next, mass,
partial-velocity-square, and stage fields are live only while one of eight
explicit active-context slots is owned. Those slots map to the separately
charged eight 448-bit contexts. Inactive C++ object fields do not imply 64
physical context entries.

The reference model and one real-X86 native-record smoke prove bounded
state-machine, coherent traffic, fail-close/rearm, and arithmetic semantics.
They do not prove native SPARTA process submission, application timing or
speedup, or RTL FP/control cost. The shared-state research ledger charges only
the eight active contexts and descriptor control as a 464-byte overlay. The
base plus overlay provisions to 16 KiB under the declared ECC/control-margin
model before physical synthesis.

### UME gradzatp contract (opcode 8)

Opcode 8 is a version-2, 128-byte UME/FLAG-proxy contract. Flags and reserved
bytes must be zero. It accelerates the active-corner portion of `gradzatp`:
predicate classification, two corner-index reads, two FP32 corner fields, one
indexed FP32 zone-field gather, one FP32 multiply, and relaxed FP32 ADDs into
point volume and point gradient. Point normalization and boundary projection
remain on the CPU.

| Offset | Size | Meaning |
| ---: | ---: | --- |
| 8 | 4 | Corner count, 1-64 |
| 12 | 4 | Positive point count |
| 16 | 4 | Positive zone count |
| 24 | 8 | Signed 32-bit corner predicate base |
| 32 | 8 | Signed 32-bit corner-to-zone base |
| 40 | 8 | Signed 32-bit corner-to-point base |
| 48 | 8 | FP32 corner-volume base |
| 56 | 8 | FP32 corner-surface base |
| 64 | 8 | FP32 zone-field base |
| 72 | 8 | Promised-zero FP32 point-volume base |
| 80 | 8 | Promised-zero FP32 point-gradient base |
| 88 | 8 | 32-byte completion record |
| 96 | 8 | ABI fingerprint `0x2ea3d5c8f3d18aec` |
| 104 | 24 | Reserved, zero |

Inactive corners retire after the predicate read; their remaining indices and
FP32 fields are poison-safe. Active indices must be in range, every consumed
FP32 value must be finite, and both targeted outputs must compare equal to
zero. All corners complete this validation pass before the first update. The
engine also bounds every retained contribution by `FLT_MAX / active_corners`
before entering the update phase. A validation failure drains reads and emits
neither updates nor completion.

The update phase uses the existing banked combiner and acknowledged atomic
path with a four-byte FP32 ADD request. Same-address ordering is intentionally
relaxed, matching the source OpenMP atomic reduction's nondeterministic order.
The retained point index, zone index, two inputs, product, predicate, stage,
and update ordinal overlay mutually exclusive existing operation fields. This
prototype therefore adds no operation-array payload and no update-kind bit,
but the FP32 multiplier, atomic implementation, decoder/control, ports,
arbitration, timing closure, energy, and area remain uncosted. The live smoke
is synthetic functional evidence, not native UME/FLAG integration or an
application-speedup claim.
