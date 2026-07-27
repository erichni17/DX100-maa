# Storage model

This is a capacity count, not a synthesized area or power estimate.

The default configuration has 4 cores, 8 tiles per core, and 16,384 32-bit
elements per tile. A 4K physical design keeps the 16K logical instruction
length but allocates only 4,096 payload elements per tile.

| Component | Native 16K physical | 4K physical virtual slice |
| --- | ---: | ---: |
| SPD payload, 32 tiles | 2,097,152 B | 524,288 B |
| Per-element completion state, bit-packed | 65,536 B | 16,384 B |
| Corrected virtual-retirement structures and payload | 0 B | 36,864 B |
| Direct-index line buffer, selected four-line window | 0 B | 256 B |
| Counted total | 2,162,688 B | 577,792 B |

The counted reduction is 1,584,896 bytes (73.28%). Row tables, offset tables,
tile-level metadata, cache tags, control logic, ports, and physical-design
effects are excluded because they do not yet have a comparable implementation
cost model. The virtual-retirement count is conservative software-model
accounting for the 384/96/480/64 geometry, not an SRAM synthesis result.

Most importantly, the current direct-index opcode virtualizes one fused gather
whose output retires to backing memory. It does not virtualize arbitrary
producer/consumer tile chains. The whole-SPD reduction above is therefore a
target-design budget, not a hardware saving already demonstrated by the
implemented slice.
