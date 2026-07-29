# Storage model

This is a capacity count, not a synthesized area or power estimate.

The default configuration has 4 cores, 8 tiles per core, and 16,384 32-bit
elements per tile. A 4K physical design keeps the 16K logical instruction
length but allocates only 4,096 payload elements per tile.

| Component | Native 16K physical | Direct-index 4K physical / 16K logical |
| --- | ---: | ---: |
| SPD payload, 32 tiles | 2,097,152 B | 524,288 B |
| Per-element readiness, bit-packed | 65,536 B | 16,384 B |
| Direct-index B feeder, 8 lines | 0 B | 512 B |
| Source response pool, 480 64-bit words | 0 B | 3,840 B |
| Destination combiner, 384 cache lines | 0 B | 24,576 B |
| Bounded virtual tags/control and completion | 0 B | 11,347 B |
| Retained Row/Offset/invalidator lower bound | 449,024 B | 449,024 B |
| Comparable lower-bound total | 2,611,712 B | 1,029,971 B |

The comparable lower-bound reduction is 1,581,741 bytes (60.56%). The direct
design retains the logical 16K Offset Table and configured Row Table because
that descriptor state is what allows it to select A requests across the full
logical window. The native and direct columns therefore include the same
bit-count lower bound for those shared structures. Cache state, unbounded
queues, ports, wiring, arbitration, and memory periphery remain excluded, so
this is not a synthesized area or power estimate.

The narrower payload-only comparison is 2,097,152 B versus 564,563 B when it
includes physical SPD, direct-index data buffers, and bounded virtual control.
That is a 73.08% reduction, but it is not the apples-to-apples headline because
it omits metadata retained by both designs.

Most importantly, the current direct-index opcode virtualizes one fused gather
whose output retires to backing memory. It does not virtualize arbitrary
producer/consumer tile chains. The whole-SPD reduction above is therefore a
target-design budget, not a hardware saving already demonstrated by the
implemented slice.
