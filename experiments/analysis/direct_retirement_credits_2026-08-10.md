# Direct Retirement Credit Experiment

## Scope

This is a narrow terminal-dataflow experiment for:

```text
C[i] = A[B[i]] * scalar
```

It does not change the gather's 16K reorder mechanism. The virtual producer
still writes gathered A values to coherent backing. After each producer-page
write acknowledgement, the direct consumer reads a backing cache line, uses
the existing timed FP64 MAA ALU, and writes the transformed line directly to
the final dense destination. It therefore removes the result-SPD page fill,
result-SPD output page, and separate stream-store sequence.

The live scheduler has bounded cache-line credits. A credit owns one 64-byte
payload from backing ReadResp through ALU completion and final WriteResp.
Completion is exposed only after all 2,048 destination WriteResp events.
Unaligned or nonterminal operations fail closed to the existing transparent
consumer.

## Matched result

| Design | Replica 1 | Replica 2 | Result |
|---|---:|---:|---:|
| Transparent 4K consumer | 46,020,703 | 46,020,703 | control |
| Direct retirement, 16 credits | 43,334,224 | 43,334,224 | 5.838% lower latency, 1.062x speedup |

All four accepted runs produced exact output hash
`7228541527853630339`. They used commit `2f25602a`, gem5 SHA-256
`1e005eb7328536e2252f7e0d68f1413fb8f4d6b3451b9df02381b730998544e6`,
the same aligned workload, frozen Ramulator library, and shared checkpoint.
The checkpoint file-list digest was
`e1a9f7a0cdbc854d49d2c3a6a8a945e17360fe7740ba591a58301bffe0c4c32d`.

The intended mechanism closed exactly in both direct replicas:

- one descriptor and four producer-page acknowledgements;
- 2,048 backing reads and ReadResp events;
- 2,048 charged ALU operations and completions;
- 2,048 coherent destination writes and WriteResp events;
- zero fallback and zero exact-address conflict stalls;
- 16-credit high-water mark and three simultaneously active stages;
- 4,678,724 ticks with read, ALU, and write stages overlapping.

The direct arm read 811,264 MAA memory bytes versus 942,336 in the control,
and issued no consumer SPD reads or stream-store instructions. The gather
producer itself was unchanged.

## Why four credits failed

The first live design had four line credits, or 256 payload bytes. Its
simulation completed with the correct output and exact 2,048-operation
closure, but a trace-format bug prevented promotion. Its measured
`55,804,457` ticks are retained only as a diagnostic.

Four credits were 21.259% slower than the matched transparent control and
recorded 3,994 full-credit stalls. A credit could not be reused until the
cache returned the exact destination WriteResp, so four write round trips
throttled the whole read/ALU/write pipeline. Increasing the bounded provision
to 16 credits reduced latency by 22.346% relative to that diagnostic and made
the direct path faster than the control. This verifies that write-response
credit pressure, not ALU work, was the first implementation's bottleneck.

## Hardware accounting

The accepted design explicitly charges:

- 1,024 bytes for sixteen 64-byte line payloads;
- 2,856 bytes for conservative scheduler, line-state, execution, and address
  ownership control in the model;
- 3,880 bytes total MAA-side state for this path.

For scale, one 4K-element FP64 page is 32 KiB and a 16K-element FP64 result
tile is 128 KiB. This comparison does not mean the optimization removes all
virtualization storage: the producer's 128 KiB coherent result backing still
exists, and the 16K gather reorder state is unchanged. The result only shows
that this terminal consumer can avoid rematerializing those values in a
result scratchpad before the final store.

## Evidence

Accepted root:

```text
/data1/nier/dx100-runs/2026-08-10-direct-retirement-2f25602a
```

The accepted arms are `direct`, `direct-r2`, `transparent`, and
`transparent-r2`. Each contains a pass marker, command manifest, source
snapshot, binary hashes, final stats, exact output, mechanism table, and raw
trace. The four-credit diagnostic is under
`/data1/nier/dx100-runs/2026-08-10-direct-retirement-1a3c4f65/direct`.

This microbenchmark result is not yet a full XRAGE or general-workload result.
