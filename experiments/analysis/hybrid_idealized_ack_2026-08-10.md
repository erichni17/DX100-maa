# Hybrid idealized-ack upper bound

## Result

The diagnostic upper bound exposes each 4K output page when its final backing
write is issued instead of waiting for that write's exact `WriteResp`. Real
writes still execute, producer completion still waits for every response, and
the terminal accounting checks still require all issued words to complete.
This is not a candidate architecture.

| Arm | Replica 1 `simTicks` | Replica 2 `simTicks` |
|---|---:|---:|
| Exact-ACK baseline | 46,735,908 | 46,735,908 |
| Idealized visibility | 46,727,770 | 46,727,770 |

The idealized arm is only 8,138 ticks, or 0.017413%, lower latency
(1.000174x). Exact output hash `7228541527853630339` matched across all four
runs. Every arm issued and completed 5,097 backing writes and exposed all four
pages. The idealized counter fired exactly four times only in treatment arms.

This rejects backing-write acknowledgement exposure as the main cause of the
hybrid's double-digit gap. It does not say that backing traffic is free: the
intervention leaves issue bandwidth, cache traffic, producer work, page fills,
ALU work, and stores unchanged.

The same trace reports no useful consumer-stage overlap:
`active_stage_high_water=1` and `action_overlap_ticks=0`. However, the accepted
equal-area ping-pong experiment already found ping-pong2K 0.230451% slower than
serial4K. ACK-focused ping-pong is therefore not promoted.

## Evidence

- Source commit: `627f788c23ca8fd406d5a746fb97b9c59a6f8bb5`
- gem5 SHA-256: `b8855391facf8126221c8c32fb37abd0c7579f431da52c05a6da5c765be4cba8`
- Workload SHA-256: `6b0b8407cc919c32490ce2b5a3e47ce8545602056782a6f8dd7dc6f19e81de3d`
- Ramulator SHA-256: `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`
- Shared checkpoint identity: `ac30df33423770ea557a024d15d44a5863deec4294bede08c985af04476ec171`
- Raw root: `/data1/nier/dx100-runs/2026-08-10-hybrid-idealized-ack-627f788c`
- Matrix exit: 0; terminal matrix pass marker present.

These numbers use the aligned workload revision and are a matched causal pair;
they should not be substituted for the earlier hybrid/native16 endpoint matrix.
