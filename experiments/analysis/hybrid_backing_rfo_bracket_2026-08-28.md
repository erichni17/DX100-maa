# Hybrid backing RFO bracket — 2026-08-28

Raw evidence: `/data1/nier/worktrees/codex-coordination/sessions/hybrid-backing-rfo-bracket-20260828-20260828-120511-d8d67aec/evidence/hybrid-backing-rfo-bracket-v7`.
The raw `artifact_sha256.txt` ledger was rehashed after sealing; the shared
checkpoint identity is
`e2132e418dba41dcc9f42cb756193c3fc4b6382ac834e9ab9883f35ed03e856c`.

This is a dedicated API micro, not a modification of the accepted guest. One
binary and one checkpoint were used for all arms. The execution revision was
`85c8d9808a64f30048d91aa31c5027fdadb066ff`; the lead gem5 executable was
`/data1/nier/worktrees/DX100-virtualization-selected-integration-cont-20260826/build/X86/gem5.opt`, SHA-256
`182a6696a60983aa690fa6b4131592cff4408b380891fa31098f1f978cdada0d`.

Fixed configuration: logical 16K / physical 4K, strict two-phase, 64-line
feeder at issue width 1, 16-line combiner, eight response slots, masked
retirement, one MAA and indirect unit, and identical source/index/backing/
destination/input plus L1/L2/L3 and single-channel Ramulator geometry. All
arms use 16 initial row slices, 64 rows/slice, and 16 entries/subslice-row to
satisfy the lead binary's strict 16K row-slot guard.

| Arm | `simTicks` | Backing transactions | L3 MAA hits / misses / miss ticks | MAA cache RD / WR packets | Ramulator reads |
| --- | ---: | ---: | ---: | ---: | ---: |
| cold | 61,355,199 | 8,338 | 9,153 / 2,048 / 596,466,572 | 4,097 / 10,386 | 24,851 |
| ideal pre-ROI reset | 61,290,721 | 8,336 | 7,243 / 2,048 / 632,218,997 | 4,097 / 10,384 | 24,866 |
| charged in ROI | 309,916,011 | 8,335 | 7,245 / 2,048 / 614,485,356 | 4,097 / 10,383 | 24,873 |

Every arm produced output hash `7228541527853630339` with zero errors and
intact guards. The strict traces closed with B=16,384 words, 16,384
descriptors, 9,523 A issues/responses, four ready pages, ordered admission,
and coherent backing ACK closure. The fetch trace count was 1,025 cache lines
in each arm (the permitted aligned/unaligned trace boundary).

## Disposition

The exact 2,048 L3 MAA-region misses **do not disappear**: both warm arms have
the same 2,048 misses as cold. The ideal arm is only 0.11% faster
(`61,355,199 / 61,290,721 = 1.00105x`), while charging the identical volatile
read/write-self walk inside ROI is 5.05× slower than cold. Thus charged
preallocation does not win.

The ideal arm is strictly a cache-state bracket: it assumes a free prior
CPU-side writable-residency walk and resets statistics afterward. It is not a
realizable hardware optimization claim; moreover, in this configuration it
does not remove the MAA's RFO-miss mechanism in the first place. No production
simulator source and no accepted guest were changed.
