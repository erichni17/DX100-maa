# LANLMAA descriptor ABI v1

The optional CPU-visible mode accepts one 64-byte little-endian descriptor
from a fixed physical slot table. A 64-bit write to doorbell offset `8 * slot`
submits that slot. One descriptor executes at a time. A doorbell while any
descriptor traffic or execution is active is acknowledged but counted as a
busy rejection. After a descriptor reaches `Completed` or drained `Error`, a
later doorbell explicitly rearms the existing operation, line, and
continuation structures and submits its slot. There is no hidden descriptor
queue: software must observe the completion record or terminal status before
submitting the next descriptor.

## Common descriptor fields

| Offset | Width | Field |
| ---: | ---: | --- |
| 0 | 4 | Magic `LMA1` (`0x31414d4c`) |
| 4 | 2 | Version `1` |
| 6 | 1 | Opcode |
| 7 | 1 | Opcode flags; zero except for opcode 4 |
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
| 24 | 8 | Acknowledged result-write count, or logical update count for opcode 4 |

The control aperture exposes device/version at `0x100`, slot and item limits
at `0x108`, state at `0x110`, completed slot at `0x118`, error code at `0x120`,
and an opcode bitmap at `0x128`. Bitmap bit `n` advertises opcode `n`; bits
1--4 are currently set.
`Completed` and `Error` remain visible until the next doorbell. A terminal
rearm clears the previous error and per-descriptor cursors only after all
retained packets, operation contexts, line entries, and update entries are
quiescent. The completion record remains the durable per-submission result.
