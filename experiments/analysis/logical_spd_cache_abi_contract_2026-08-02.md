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
otherwise unknown wire value are rejected. The scalar-register byte is checked
against the simulator's configured register count, including the second word
required by a 64-bit scalar.

The `Instruction` carries separate logical IDs plus future generation,
transaction, and slot fields. This avoids overloading physical IDs before
hidden SPD slots exist. The decoder validates the complete four-word shape
before any controller state mutation.

There are no public logical wait helpers in this patch. The existing
`wait_virtual_page` helper remains exclusively indexed by a legacy physical
completion-token tile. A future generation-aware logical ready range must be
implemented before software receives a logical page or tile wait API.

## Current behavior and residual gaps

Today a logical word sequence is decoded and held until word 3. At that
`CpuSidePort` boundary, its full shape and configured scalar-register span are
validated. Its destination must be non-null, naturally aligned to the FP32 or
FP64 element width, inside a registered address range, and leave room through
the exclusive range end for all 16,384 result elements (64 KiB for FP32 or
128 KiB for FP64). A fully valid sequence then fails closed with a clear
diagnostic before backing fields, IF admission, controller state, or SPD state
can change. This is intentional: the patch is not connected to the cache
controller, so accepting it would wrongly hand an all-`-1` physical ALU to the
existing execution unit. Malformed, mixed, conditional, aliased, unsupported,
misaligned, unregistered, or truncated forms fail at the same admission
boundary.

This patch does not change StreamAccess or Port response semantics, does not
convert stores to `WriteReq`, does not add hidden SPD storage, does not modify
virtual-indirect production, and does not retire opcode 16. The existing
transparent opcode/path and its completion-token ABI remain intact.

The focused host test compiles and runs under the repository's C++11 guest
language mode. It exhaustively classifies all high-byte patterns and register
bytes, checks exact and truncated 16K FP32/FP64 destination spans, and verifies
that the guest helper writes the same wire image. Python checks bind the
`CpuSidePort` validation-before-fail-closed ordering and the absence of logical
wait aliases.
No gem5 simulation was run. These checks are ABI tests, not integration, timing,
area, correctness-through-memory, or performance evidence; this patch makes
no performance claim.
