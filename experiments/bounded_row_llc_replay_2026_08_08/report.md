# Finding: reject coherent-LLC B replay as the next vertical slice

## Decision

Do not implement this mechanism in gem5. On the exact 16K physical-admission
trace, stable LLC replay preserves bounded modulo's A ordering and DRAM-row
grouping, but therefore cannot recover the measured 20.47--21.26% gap to the
earlier full-metadata controls. It changes only B selection traffic.

The hardware-general replay record is 64 bits: `{logical_iteration:u32,
B_value:u32}`. After charging full-line coherent stores, replay reads, and the
eventual dirty writeback, it moves **5,654** B/backing cache lines, **37.90%
more** than modulo's 4,100 B-line reads. A workload-specialized 32-bit packing
can reduce that subtotal to 3,344 lines (-18.44%), but it is legal only for the
proven 14-bit iteration and 17-bit source-index bounds. Even granting that
specialized engine zero store/replay cost, the unmatched selector work bounds
the possible latency reduction to **1.155646%** (speedup 1.169158%), below the
predeclared 5% materiality gate. No simulator source was claimed or edited.

The professor's idea is treated here as a collaborative hypothesis. This result
rejects this mechanism on this matched input; it is not a general rejection of
metadata virtualization.

## Exact evidence boundary

The model consumes 16,384 ascending
`dx100.physical_admission.v1` records from the source-commit `0108d9b` control.
The raw JSONL SHA-256 is
`1c68340c0e87a53240905389c1c0e5bf451a0645b8ceaf5f92d4e34edaba5424`.
Each row supplies the actual iteration, B physical address and value, A physical
line, decoded slice/row/grow, and word ID. The frozen control used gem5
`90858e29...1ee9295`, workload `f87d7206...ca6dfc5`, Ramulator
`76ea3a9c...a15753`, RoBaRaCoCh/DDR4-3200W config
`aca6e27b...f68731b`, and checkpoint identity
`ef60d62c...93f3b5c`; its exact workload output was
`7228541527853630339` with zero errors.

`input_manifest.json` contains the complete hashes and paths. The raw trace is
not a timing oracle. The model validates it and uses only physical admissions;
it does not claim to reproduce the benchmark multiply/store output.

The earlier matched bounded-control table is also frozen by hash. Its modulo
arm observed 62,456,646 `simTicks`, 199,542 CPU cycles, 4,101 filter cycles,
65,536 examined B words, and exact output hash `7228541527853630339`. Those
observations are context for the conservative filter-only upper bound, not a
candidate timing prediction.

## Mechanism modeled

The finite datapath has 4,096 Offset entries, 16 Row slices, 32 row slots per
slice, and eight A lines per row slot: at most 512 live row slots and 4,096 live
line slots. Full metadata has 16,384 Offset entries and 128 row slots per slice,
providing 16,384 line slots. Both use the native slice traversal
`0,4,8,12,1,5,9,13,2,6,10,14,3,7,11,15` and stable first-free row/line
placement.

All finite arms use the existing online, implementable partition
`grow_addr % 4`. The replay treatment makes no offline decision:

1. Scan the original B stream once. Admit partition 0 into finite Row/Offset
   state and append every future-partition record to that partition's coherent
   backing queue.
2. Drain partition 0 in native row-aware order.
3. Read the stable queue for partitions 1, 2, and 3 in turn, recompute A address
   translation, insert into the same finite tables, and drain.
4. Carry the original logical iteration in each queue record so each result
   retires exactly once to its original destination.

The simple hardware-legal queue layout reserves three fixed per-partition
address regions. Generic records require a 393,216-byte virtual address span;
98,648 bytes contain valid records on this input. Packed records reserve
196,608 bytes and contain 49,324 valid bytes. Three 64-byte tail assembly
buffers permit full-line stores without read-for-ownership; the model still
charges the eventual dirty writeback. It assumes no beneficial LLC residency,
compression, eviction avoidance, or hidden bandwidth.

## Matched model result

| Arm | B/select work | Finite epochs | A-line requests | DRAM row groups | Order |
|---|---:|---:|---:|---:|---|
| True full Row/Offset | 1,025 B lines | 1 | 9,523 | 129 | `7c6ca88e...e7689` |
| Bounded modulo | 4,100 B lines / 65,536 words | 6 | 9,575 | 138 | `156a15d7...b53` |
| LLC replay | 16,384 original + 12,331 replay records | 6 | 9,575 | 138 | `156a15d7...b53` |

Modulo populations are `4053, 4177, 4100, 4054`. Thus a nominal four-pass 4K
policy is not four 4K epochs: partitions 1 and 2 overflow, producing populations
`4053 / 4096+81 / 4096+4 / 4054` and six finite epochs. Peak live state is
exactly 4,096 offsets and 310 of 512 row slots. Replay has the identical
placement digest `f967c51f...65e9c`, A issue digest, A-line request count, row
groups, drain causes, and epoch populations as modulo.

The model's 9,575 A-line requests are two below the prior gem5 modulo counter
of 9,577 because this compact screen models deterministic epoch grouping, not
the simulator's response/service microevents. The rejection does not depend on
that difference: replay and modulo are compared by the same model and are
byte-for-byte identical in admitted streams and modeled A issue order.

## Why this does not repeat the failed policies

Fixed global ranges, equal-width source-relative ranges, and the offline
balanced oracle are not re-evaluated or renamed. Their prior matched result
already showed 49.72%, 24.10%, and 20.47% overhead versus the reported hybrid
control; the offline oracle was only 0.658% faster than modulo and did not pay
to discover its boundaries. This study retains modulo solely as an online row
subset function and isolates a new question: whether dense coherent spill can
make later subset selection cheaper. It cannot make the oracle implementable,
and no such claim is made.

## Next viable mechanism

Stop changing the partition policy on this input. A follow-up should target the
larger causal cost: producer/backing-store completion and source-service overlap.
The smallest next experiment is an **issue-ready page handoff** screen: expose a
4K page after every write has issued, retain generation/address ownership until
the final write response, and prove that an exact-address reload cannot pass an
older incomplete write. Run it against the same checkpoint with exact output,
terminal completion, outstanding-write ownership, page-order, traffic, and
`simTicks` gates. This is a proposed experiment, not an accepted mechanism; it
must first resolve the current tail audit's causal uncertainty.

This direction can affect multi-million-tick producer/consumer tail overlap,
whereas replay's zero-cost filter-only ceiling is about 1.16%. If that ownership
proof fails, the next honest outcome is rejection rather than another metadata
partition surrogate.
