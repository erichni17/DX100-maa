# Inactive masked-fragment retention contract

## Scope

This default-off mechanism extends the existing 16K-reorder/4K-SPD hybrid; it
does not define a new hybrid. Every producer fragment still reaches coherent
backing storage. A retained line only avoids the later backing read when all
authoritative words have been reconstructed exactly before its logical page is
active and `setVirtualPageReady` has sealed that page. Every miss, poison, or
incomplete history uses the unchanged exact `ReadBacking` path.

The mechanism is separate from `InactiveProducerLinePayloadCapture`. gem5
rejects configurations that enable both because no interaction between their
ports, storage, or replay authority has been proven.

## Bounded organization and correctness

- Capacity is 0, 512, 1,024, 2,048, or 4,096 total entries; 0 is the default.
- `tokenTile[1:0]` selects one of four static lifetime descriptors and storage
  partitions. Each exact descriptor tag contains token, non-wrapping producer
  generation, non-wrapping payload incarnation, and backing base address.
- `line[1:0]` selects one of four fixed one-write-port banks. The remaining
  line bits directly index the selected lifetime/bank partition. There is no
  CAM, scan, map, queue, or latest-owner policy.
- One shared synchronous read port feeds one authoritative 64-byte output
  latch. Reads and writes take one MAA cycle and same-cycle reads observe the
  pre-write RAM value. The latch survives RAM replacement and descriptor
  clear until exact `take()` authentication.
- Non-overlapping words for the same exact line/lifetime merge. The completing
  fragment's exact transaction identifies the reconstructed line.
- Every live descriptor owns 2,048 logical-line poison bits. An overlap,
  different exact live tag collision, lost bank write, malformed fragment,
  stale lifetime event, or post-seal fragment poisons the affected line for
  that lifetime. A later fragment cannot repair it.
- Page seal is a control gate, not data authority: only a full-mask,
  non-poisoned, exact-tag entry from the sealed page can hit. Coherent backing
  remains the sole fallback and continues to contain the exact final data.

The four static partitions explain the offline first-owner coverage directly:

| Total entries | Entries/lifetime partition | First-owner coverage |
| ---: | ---: | ---: |
| 512 | 128 | 12.5% |
| 1,024 | 256 | 25% |
| 2,048 | 512 | 50% |
| 4,096 | 1,024 | 100% |

Latest-owner is intentionally absent because the offline evidence showed it
was dominated and because replacement would complicate irreversible poison
and output-latch authority.

## Packed hardware accounting

These equations are packed RTL lower bounds and never use host `sizeof`.

```text
key_bits                   = token 16 + generation 64
                           + payload incarnation 64 + backing base 64 = 208
entry_tag_bits             = valid 1 + key 208 + line 16
                           + word mask 16 + closing transaction 64 = 305
descriptor_bits_each       = valid 1 + key 208 + line count 16
                           + sealed pages 4 + stored-entry count 13 = 242
poison_bits                = 4 descriptors * 2,048 logical lines = 8,192
write_state_bits(C)        = 4 banks * (next cycle 64 + pending 1
                           + completion cycle 64 + index log2(C)
                           + payload 512 + entry tag 305)
read_state_bits            = shared next cycle 64
output_bits                = payload 512 + valid/key/line/transaction 289
counter_bits               = 13 64-bit event counters
                           + occupancy/high-water 2*13 = 858
MAA_lookup_control_bits    = 510
persistent_incarnation(T)  = 64*T
```

`control_bits(C)` includes RAM tags, four descriptors, all poison bits, four
write input latches, shared read-port state, output tag, counters, and the
13-bit configured capacity. `combined_bits(C,T)` adds RAM/output payload,
MAA lookup control, and persistent token incarnations. The table uses 32 token
tiles and rounds to bytes only after summing bits.

| Entries | Payload + output (B) | Control (B) | Combined bits | Combined (B) |
| ---: | ---: | ---: | ---: | ---: |
| 512 | 32,832 | 21,296 | 435,578 | 54,448 |
| 1,024 | 65,600 | 40,816 | 853,886 | 106,736 |
| 2,048 | 131,136 | 79,857 | 1,690,498 | 211,313 |
| 4,096 | 262,208 | 157,937 | 3,363,718 | 420,465 |

## Statistics and validation boundary

The gem5 surface separately reports accepted fragments, merged words,
reconstructed full lines, exact replay hits and misses, first-owner tag
conflicts, overlap poison, write-port poison, stale/untracked drops, shared
read-port stalls, clears, high-water occupancy, payload bytes, and control
bytes.

Focused optimized and ASan/UBSan tests cover merge and completion, overlap and
collision poison, lost-fragment poison, seal gating, stale descriptor/epoch,
four-bank conflicts, one-cycle read latency, output-latch replacement/clear
races, exact clear closure, all capacities, and the accounting equations.
Adjacent materializer and prior full-line-capture lifecycle tests remain
unchanged. No gem5 workload or performance claim is made by this handoff.
