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
| 7 | 1 | Flags, must be zero |
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
| 24 | 8 | Acknowledged result-write count |

The control aperture exposes device/version at `0x100`, slot and item limits
at `0x108`, state at `0x110`, completed slot at `0x118`, error code at `0x120`,
and an opcode bitmap at `0x128`. Bitmap bit `n` advertises opcode `n`.
`Completed` and `Error` remain visible until the next doorbell. A terminal
rearm clears the previous error and per-descriptor cursors only after all
retained packets, operation contexts, line entries, and update entries are
quiescent. The completion record remains the durable per-submission result.
