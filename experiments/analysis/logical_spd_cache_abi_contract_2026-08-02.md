# Logical SPD-cache ABI contract — patch 2

This patch adds only the software/MMIO representation and conservative
decoder checks for a future controller-owned logical `ALU_SCALAR`. It is not
connected to the cache controller, SPD slots, indirect producer, scheduler,
or response path.

## High-byte audit and encoding

Instruction word 0 already uses bits 39:0. The audit covered every in-tree
writer of `INSTR_opcode_datatype_optype_tdst1_tdst2`: each writes only those
low five bytes, leaving bits 63:40 as zero. `CpuSidePort` also previously
ignored the high bytes. The high bytes are therefore free, but the integration
plan's proposed requirement that an ordinary physical instruction write all
three bytes as `0xff` is not compatible with the established all-zero physical
wire image.

The compatible encoding is deliberately narrow:

| Bits | Logical scalar value | Legacy physical value |
| --- | --- | --- |
| 63:56 | `logicalSrc1ID` in `[0, 1]` | `0x00` |
| 55:48 | `0xff`: reserved logical source 2 / form discriminant | `0x00` |
| 47:40 | `logicalDst1ID` in `[0, 1]` | `0x00` |
| 39:0 | existing `ALU_SCALAR = 8` header, with physical destinations `0xff` | unchanged existing header |

Only an all-zero high-byte group is a physical instruction. Only the tagged
two-descriptor form above is logical. Every other high-byte pattern is
rejected, including logical source 2, descriptor IDs outside the two-entry
first slice, and the plan's all-`0xff` physical convention. This makes a new
logical form distinguishable without rewriting or changing the behavior of
the existing physical API.

## Exact decoded form

`maa_alu_scalar_logical<T>(srcLogical, dstLogical, destinationBacking,
scalarReg, op)` writes ordinary opcode 8. It puts no physical SPD source or
destination in either instruction word, keeps only `scalarReg` in word 1,
uses the word-2 no-address sentinel, and uses word 3 for
`destinationBacking`. `srcLogical != dstLogical`; a logical source 2,
condition tile, extra scalar register, or any physical SPD operand is
rejected. Datatypes are limited to the six ordinary MAA types and operations
to the sixteen existing `ADD` through `EQ` scalar operations; `NE_OP` and any
otherwise unknown wire value are rejected.

The `Instruction` carries separate logical IDs plus future generation,
transaction, and slot fields. This avoids overloading physical IDs before
hidden SPD slots exist. The decoder validates the complete four-word shape
before any controller state mutation.

The public `maa_wait_logical_page` and `maa_wait_logical_tile` spell the
future two-descriptor/four-page ready-index shape. They are ABI placeholders:
the currently physical-token-indexed ready producer is intentionally
unchanged, so these waits are not yet a supported synchronization mechanism.

## Current behavior and residual gaps

Today a valid logical word sequence is decoded and shape-validated, then
fails closed with a clear diagnostic before IF/controller admission. This is
intentional: the patch is not connected to the cache controller, so accepting
it would wrongly hand an all-`-1` physical ALU to the existing execution unit.
Malformed, mixed, conditional, aliased, or unsupported forms fail at the same
boundary.

This patch does not change StreamAccess or Port response semantics, does not
convert stores to `WriteReq`, does not add hidden SPD storage, does not modify
virtual-indirect production, and does not retire opcode 16. The existing
transparent opcode/path and its completion-token ABI remain intact.

The focused host test exhaustively classifies all high-byte patterns and each
rejected scalar-shape category, and checks that the guest helper writes the
same wire image. Python checks bind the source and scope boundaries.
No gem5 simulation was run. These checks are ABI tests, not integration, timing,
area, correctness-through-memory, or performance evidence; this patch makes
no performance claim.
