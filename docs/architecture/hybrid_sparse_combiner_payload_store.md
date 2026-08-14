# Hybrid sparse combiner payload store

The virtual destination combiner now separates line identity from useful-word
payload. `VirtualCombineSlot` retains a valid bit, line address, 16-bit
valid-word mask, and a fixed 16-entry reference array. It does not own a
64-byte line. `VirtualCombinePayloadStore` owns one bounded pool of useful
words for the indirect unit, with at most 8 bytes per word so the same layout
supports FP32 and FP64 instructions.

No new configuration knob is required. A nonzero `virtual_combine_words=W`
creates exactly `W` useful-word entries. Increasing `virtual_combine_slots`
therefore increases tags and references but does not increase payload. The
existing zero setting preserves its admission behavior: it derives the pool
limit as `line tags * words per cache line`, so every tag can still become a
full line for the active FP32 or FP64 instruction.

## Ownership and retirement

Insertion allocates one pool entry before setting the line's valid-word bit.
An existing valid bit remains a fatal duplicate-output error. Exhaustion
returns a capacity stall without consuming the source response or changing the
line, so the current victim/drain machinery can make progress and retry.

Full and masked writes assemble a temporary 64-byte line from the referenced
words. `Packet::setData` copies that line during successful write creation;
only then are all selected references released. Word writes pass the referenced
word to the same write-creation path and release that one reference only after
success. Address conflicts, write-credit stalls, and rejected victim writes
retain every reference. Multiword release validates the entire reference set
before freeing any entry, preventing a duplicate reference from causing a
partial or double free.

These rules leave the existing behavior unchanged:

- a full line enters and leaves `VirtualCombinerPageOrder` at the same mask
  transitions;
- round-robin, fewest-word, and most-word victim selection still use the same
  line masks;
- full-line, masked-line, and word-at-a-time retirement use the same addresses,
  masks, write credits, and counters; and
- page readiness and write-completion accounting still attach to the packet
  created before payload ownership is released.

The helper encodes a generation in its 32-bit simulator reference. This makes
stale references, duplicate ownership, and double frees fail closed in unit
and sanitizer testing. It is simulator hardening, not a prescribed hardware
reference format. The source-checked ledger therefore reports the semantic
12-bit estimate and the explicit C++ storage separately. Each simulator line
owns a 16-entry `uint32_t` reference array (512 bits), while each pool entry
has an 8-bit allocation element, a 32-bit generation element, and a 32-bit
free-list element. At 4,096 entries those pool-bookkeeping element widths total
294,912 bits (36,864 bytes), excluding vector objects and unused capacity.

## Payload and metadata bit estimates

At the hybrid point `virtual_combine_words=4096`, the maximum-width payload is
constant:

```text
4096 words * 8 bytes/word = 32,768 bytes = 262,144 bits per indirect unit
```

The following metadata estimate assumes a 64-bit stored line address, one
valid bit, a 16-bit FP32-position mask, 16 pool references, and the minimum
12-bit index needed for 4,096 pool entries. That is
`1 + 64 + 16 + 16*12 = 273 bits` per line tag.

| Line tags | Payload bits | Payload bytes | Line metadata bits | Line metadata bytes |
|---:|---:|---:|---:|---:|
| 512 | 262,144 | 32,768 | 139,776 | 17,472 |
| 1,024 | 262,144 | 32,768 | 279,552 | 34,944 |
| 2,048 | 262,144 | 32,768 | 559,104 | 69,888 |

The corresponding C++ reference arrays are 262,144, 524,288, and 1,048,576
bits for 512, 1,024, and 2,048 tags. Those simulator-hardening widths are not
the semantic 12-bit hardware-reference estimate in the preceding table.

One explicit bounded allocator implementation—a 1-bit allocation vector, a
4,096-entry stack of 12-bit free indices, and a 13-bit stack pointer—adds
53,261 metadata bits per indirect unit. Other bounded allocators may encode
this state differently. Replacement state and the optional page-ready
intrusive queues are separate, configuration-dependent control metadata and
are not hidden in the payload number.

These are payload capacities and transparent bit estimates, not synthesized
area claims. They exclude C++ object padding, vector control objects, allocator
headers, SRAM periphery, ports, arbitration, and wiring. The accounting script
reports these terms separately for the same reason.

## Validation boundary

The reusable helper test compiles and runs optimized and with ASan/UBSan. It
covers FP32 and FP64 allocation/update, full-line assembly, masked release,
word-at-a-time release, pool reuse, exhaustion, invalid widths, duplicate
references, stale references, busy reset, and double-free rejection. Production
object compilation checks integration. No gem5 simulation evidence is claimed
by this storage refactor.
