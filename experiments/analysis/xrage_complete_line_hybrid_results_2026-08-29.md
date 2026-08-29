# XRAGE complete-line hybrid result (2026-08-29)

## Decision

Accept one **real XRAGE application configuration** as positive evidence for
complete-line hybrid retirement.  The selected logical16K/physical4K hybrid
takes 37,268,284 `simTicks`, 11.921% below same-binary native16 and 10.301%
below the prior accepted hybrid.

This is not yet an XRAGE-suite or synthesized-hardware promotion.  Keep the
configuration explicit and retain the complete-line fail-closed contract.

## Compared configurations

All three current runs use one gem5 binary, SHA-256
`bb3702ec8fa8e9b328f0efd22da29f756d70679ab3aa69a080dd41e9f2ea4598`,
the same 64K LANL XRAGE gather input, exact verifier, two-channel Ramulator,
and feeder depth 128 at finite default issue width.

| Arm | Geometry / result storage | `simTicks` |
|---|---|---:|
| Native16 | logical16K / physical16K | 42,312,279 |
| Bounded hybrid control | physical4K; 16 tags, 128 combiner + 1,024 response words | 56,159,086 |
| Complete-line hybrid | physical4K; 1,536 tags x 16 ways, 2,560 combiner + 1,024 response words | **37,268,284** |

The selected hybrid is 33.638% lower latency than the matched bounded control
(1.5069x), 11.921% below native16, and 10.301% below the prior accepted
41,547,933-tick hybrid from August 13.

## Mechanism and attribution

The producer gathers four 16K logical windows into coherent backing.  The
consumer uses the existing four-context direct-retirement pipeline to read
complete lines, multiply by three, and write final output.  No fused direct
sink or placeholder write is used.

| Counter | Bounded control | Complete-line hybrid |
|---|---:|---:|
| Producer backing writes | 17,020 | 8,192 |
| Full / partial producer lines | 621 / 16,399 | 8,192 / 0 |
| Producer transport bytes | fragmented | 524,288 semantic/full bytes |
| L3 MAA misses | 15,167 | 8,192 |
| L3 MAA miss latency | 830,212,781 | 239,000,227 |
| Ramulator reads | 27,739 | 20,764 |
| Direct-consumer read/write lines | 8,192 / 8,192 | 8,192 / 8,192 |
| Direct-consumer overlap ticks | 15,735,136 | 21,112,163 |

The speedup comes from retaining scattered result fragments privately until
each line is complete.  This removes 8,828 producer transactions, 6,975 L3
misses, and 6,975 Ramulator reads while increasing overlap in the already
validated direct consumer.  It is not attributed to more Row/Offset reorder
capacity; both hybrids keep logical16K reordering.

## Fail-closed capacity selection

`virtual_complete_line_only` is a source-level contract:

- explicit combiner and response pools must fit within the physical element
  count;
- a partial victim under capacity pressure panics;
- a final partial drain panics; and
- terminal partial-write count must be zero.

The selected pools total 3,584 words, below the 4,096-word bound.  Failed
guards are preserved:

- 512 tags / 1,600 words: partial-victim rejection;
- 1,024 tags / 3,072 words: partial-victim rejection;
- 1,280 tags / 2,304 words: partial-victim rejection.

The 2,048-tag/3,072-word point succeeds at 37,258,581 ticks but costs more.
The selected 1,536-tag/2,560-word point is only 9,703 ticks slower (0.026%)
and is the better storage/performance knee.

## Storage

The selected one-unit packed ledger reports:

| Item | Bytes |
|---|---:|
| Physical SPD plus bounded virtual payload/control | 618,387 |
| Configured comparable lower bound | 889,235 |
| Native comparable lower bound | 2,417,152 |
| Increment versus bounded 16-tag control | 55,727 |

The selected hybrid retains a 63.211% comparable-storage reduction versus
native.  Its combiner payload is 20,480 B/unit and response payload is 8,192
B/unit.  The remaining incremental charge is bounded tag/mask/reference,
allocator, page, and retirement metadata.

These are semantic lower bounds, not area/Fmax.  A physical implementation
still needs timed 16-way lookup, set decoding, reference/payload RAM ports,
full-line drain bandwidth, reset/epoch handling, and ACK/drain arbitration.

## Correctness and scope

Every accepted run has one exact verifier pass with hash
`5576400619275092867`, terminal `m5_exit`, 65,536 direct-index words, and no
Row/Offset drains or fallbacks.  The selected hybrid closes 8,192 producer
full-line ACKs plus 8,192 direct-consumer reads, ALUs, and destination writes.

Only the recovered XRAGE gather0 64K configuration is covered.  CG NA256 is
tick-identical under dense allocation, and IS/HashJoin do not expose this
virtual-result edge.  Broader claims require more XRAGE/FLAG configurations
or another dense backed direct-index application.

Evidence roots:

- native16:
  `/data1/nier/dx100-runs/2026-08-29-xrage-native16-current-r1`;
- bounded control:
  `/data1/nier/dx100-runs/2026-08-29-xrage-complete-line-control-r2`;
- selected complete-line hybrid:
  `/data1/nier/dx100-runs/2026-08-29-xrage-complete-line-safe-1536t-2560w-r1`.

Combined ledger:
`xrage_complete_line_artifacts_2026-08-29.sha256`.
