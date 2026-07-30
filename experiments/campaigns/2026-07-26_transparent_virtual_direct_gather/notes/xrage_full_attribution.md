# Full-XRAGE Virtualization Attribution

This experiment revisits the original XRAGE headline on the complete
2,097,152-element `xrage_gather0_full.json` input. All seven arms use the same
frozen gem5 and runtime-selectable guest binary. Every arm passed exact output,
terminal exit, artifact identity, resolved configuration, and Ramulator
artifact checks.

## Result

| Treatment | Reference -> candidate | ROI tick delta |
|---|---|---:|
| Fused gather + dense retirement | unfused 16K -> `compact16` | -12.695% |
| Original 4K physical path | `compact16` -> direct4, 8 B lines | +5.723% |
| Deeper bounded B feeder | 8 -> 128 B lines | -5.877% |
| Bound Row state to 4K | 16K -> 4K Row window | -0.996% |
| Change Offset schedule to 4K epochs | 16K -> 4K epoch | -5.750% |
| Bound Offset storage at matched epoch | 16K -> 4K entries | +0.000% |
| Current bounded4 vs. `compact16` | combined | -7.146% |
| Current bounded4 vs. unfused 16K | combined | -18.934% |

Negative values mean lower simulated latency. The original eight-line direct4
arm took 1,233,455,376 ticks versus 1,166,690,598 for `compact16`, reproducing
the expected **5.723% virtualization overhead**. Its combined result remained
7.699% below the unfused baseline because the 12.695% fusion gain was larger
than the virtualization cost. The earlier roughly 11.93% headline described
this fused family; it was never evidence that storage virtualization itself was
faster.

## Why the current bounded arm wins

The current bounded arm takes 1,083,316,475 ticks. Its advantage over
`compact16` is not free paging and does not preserve the monolithic 16K reorder
window.

1. Increasing the direct B feeder from 8 to 128 cache lines reduces indirect
   fill cycles from 1,144,317 to 911,225. This recovers 5.877% while adding a
   bounded 7.5 KiB of B payload relative to the old eight-line point.
2. Bounding Row state changes when work drains. It reduces dense retirement
   writes from 342,732 to 327,924 and improves latency by 0.996% on this input.
3. A 4K Offset epoch reduces retirement writes again, from 327,924 to 262,903,
   while improving latency by 5.750%. A dense array of 2,097,152 64-bit values
   has a 262,144-line minimum, so this schedule is within 759 writes (0.29%) of
   ideal.
4. Shrinking the physical Offset array from 16K to 4K at that fixed schedule
   changes no request, write, DRAM command, or tick. The extra capacity was not
   live.

The 4K schedule trades away A-side reorder opportunity. Relative to the 16K
epoch, inserted A cache-line descriptors rise from 302,676 to 322,414 and row
descriptors rise from 39,830 to 43,452. However, the smaller epoch sends A
responses to the fixed C-line combiner in a much more favorable dense-output
order. Retirement writes fall by 65,021 and first-ROI memory-controller reads
fall by 43,728, outweighing the worse A-side locality. This is a legal
schedule/coalescing optimization specific to the gather-plus-dense-retirement
mechanism, not a general claim that virtual memory is faster than resident
storage.

## Scope

The result supports a bounded fused XRAGE gather that retires directly to dense
memory. It does not yet implement transparent backing and reload for arbitrary
later MAA instructions. Such producer/consumer chains still pay backing-store
cost; the displaced-data microbenchmark measured 19.77% overhead.

## Evidence

- Simulator source: `3b50cdb64fae484263305eeb56008677ac2f9990`
- gem5 SHA-256:
  `43cf815fa41fef0f89b75e91a37b1f7a1288fd4d9c4299318c6c23ae86f57097`
- Campaign:
  `/data1/nier/dx100-runs/2026-07-29-xrage-bounded-storage-attribution-3b50cdb`
- Fail-closed comparison:
  `/data1/nier/dx100-runs/2026-07-29-xrage-bounded-storage-attribution-3b50cdb/comparison-seven-arm`
