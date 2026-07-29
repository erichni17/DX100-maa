# Storage model

This is a capacity count for the controlled one-indirect-unit configuration,
not a synthesized area or power estimate.

The configuration has 4 cores, 8 tile IDs per core, and 16,384 32-bit words per
tile ID. A 64-bit tile occupies two adjacent tile IDs; it is not one 16K buffer
split into 8K input and 8K output halves. For example, the native double gather
uses one 64 KiB tile ID for 16K 32-bit B indices and two tile IDs (128 KiB) for
16K 64-bit A results.

The current direct-index configuration retains a 16K logical instruction but
uses 4K active Row and Offset capacity and backs each tile ID with only 4,096
physical 32-bit words. Its selected B feeder depth is 128 cache lines.

| Component | Native 16K physical | Direct-index 4K physical / 16K logical |
| --- | ---: | ---: |
| SPD payload, 32 tile IDs | 2,097,152 B | 524,288 B |
| Per-element readiness, bit-packed | 65,536 B | 16,384 B |
| Direct-index B feeder, 128 lines | 0 B | 8,192 B |
| Source response pool, 480 64-bit words | 0 B | 3,840 B |
| Destination combiner, 384 cache lines | 0 B | 24,576 B |
| Bounded virtual tags/control and completion | 0 B | 10,738 B |
| Retained Row/Offset/invalidator lower bound | 254,464 B | 66,688 B |
| Comparable lower-bound total | 2,417,152 B | 653,138 B |

The current comparable lower-bound reduction versus the original native-16K,
full-descriptor point is 1,764,014 bytes (72.979%). The direct design keeps
4,096 Offset entries and 4,096 active Row-Table entry slots for one indirect
unit. It drains these structures between 4K epochs and therefore does not
preserve issue ordering across the entire 16K logical window.

The intermediate 4K-Row/16K-Offset treatment had a 682,322-byte comparable
lower bound. Reusing and bounding the Offset Table reduces this to 653,138
bytes, 22.475% below the 842,482-byte full-descriptor direct-index point. See
`descriptor_capacity.md` and `offset_capacity_epoch.md`.

The physical SPD plus active bounded payload/control is 571,634 bytes, a
72.742% reduction versus the native SPD payload alone. That narrower number is
not the apples-to-apples headline because it omits metadata retained by both
designs. The gem5 C++ model also retains an inactive 8 KiB legacy response-line
array; counting it only against the candidate yields 850,674 comparable bytes
and a 64.807% reduction.

Cache state, unbounded queues, ports, wiring, arbitration, SRAM periphery, and
C++ container/allocator overhead remain excluded. The current direct-index
opcode also virtualizes only a fused gather whose output retires to backing
memory. It does not virtualize arbitrary producer/consumer tile chains, so the
whole-SPD reduction is a target-design budget rather than a demonstrated chip
area saving.
