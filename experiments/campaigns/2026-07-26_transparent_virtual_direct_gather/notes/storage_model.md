# Storage model

This is a capacity count for the controlled one-indirect-unit configuration,
not a synthesized area or power estimate.

The configuration has 4 cores, 8 tile IDs per core, and 16,384 32-bit words per
tile ID. A 64-bit tile occupies two adjacent tile IDs; it is not one 16K buffer
split into 8K input and 8K output halves. For example, the native double gather
uses one 64 KiB tile ID for 16K 32-bit B indices and two tile IDs (128 KiB) for
16K 64-bit A results.

The direct-index configuration retains a 16K logical instruction and Row/Offset
window but backs each tile ID with only 4,096 physical 32-bit words. Its
selected B feeder depth is 128 cache lines, not the earlier eight-line point.

| Component | Native 16K physical | Direct-index 4K physical / 16K logical |
| --- | ---: | ---: |
| SPD payload, 32 tile IDs | 2,097,152 B | 524,288 B |
| Per-element readiness, bit-packed | 65,536 B | 16,384 B |
| Direct-index B feeder, 128 lines | 0 B | 8,192 B |
| Source response pool, 480 64-bit words | 0 B | 3,840 B |
| Destination combiner, 384 cache lines | 0 B | 24,576 B |
| Bounded virtual tags/control and completion | 0 B | 10,738 B |
| Retained Row/Offset/invalidator lower bound | 254,464 B | 254,464 B |
| Comparable lower-bound total | 2,417,152 B | 842,482 B |

The comparable lower-bound reduction is 1,574,670 bytes (65.146%). The direct
design keeps 16,384 Offset entries and 16,384 active Row-Table entry slots for
the one indirect unit. This metadata is what preserves selection and issue
ordering across the full logical window even though only 8 KiB of B payload is
live in the feeder. The native and direct columns include the same lower-bound
descriptor state because the baseline already has those structures.

A later treatment reduced active Row-Table capacity to 4,096 entries while
retaining the 16,384-entry logical Offset Table. Its comparable lower bound is
682,322 bytes: 19.010% below the full-descriptor direct-index design and 71.772%
below this table's original native-16K lower bound. This is not yet a fully 4K
descriptor design because the Offset Table remains logical-iteration indexed.
See `descriptor_capacity.md`.

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
