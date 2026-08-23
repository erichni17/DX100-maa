# XRAGE backed-capacity attribution - 2026-08-23

## Result

The matched backed16/backed4 pair is accepted for the narrow physical-SPD
capacity question. Both replicas of both arms completed at **52,516,705 first-ROI
simTicks** with exact 65,536-element output key
`65536:5576400619275092867`. The pair therefore shows no timing difference at
this point; it proves that the controller-managed non-fused backed/direct-index
path is invariant across the admitted physical 16K and physical 4K payload
capacities.

The ordinary controls are valid but are not treatment-matched to the backed
instruction stream: native16 completed at 42,489,437 ticks and native4 at
49,396,095 ticks in both replicas. Backed is 23.599% higher latency than
native16 and 6.318% higher latency than native4. These values are first-window
`simTicks` only; host time and the later verification window are excluded.

| Arm | Replica 1 | Replica 2 | INDRD / STRRD / ALUS / STRWR | Materializer submit / page / retire / fallback |
|---|---:|---:|---:|---:|
| native16 | 42,489,437 | 42,489,437 | 4 / 4 / 4 / 4 | 0 / 0 / 0 / 0 |
| native4 | 49,396,095 | 49,396,095 | 16 / 16 / 16 / 16 | 0 / 0 / 0 / 0 |
| backed16 | 52,516,705 | 52,516,705 | 4 / 0 / 16 / 16 | 16 / 16 / 4 / 0 |
| backed4 | 52,516,705 | 52,516,705 | 4 / 0 / 16 / 16 | 16 / 16 / 4 / 0 |

Token-bound page materializers are intercepted before ordinary stream
dispatch, so they correctly do not increment `numInst_STRRD`. Their dedicated
16 submissions, 16 page completions, four context retirements, exact summary
closure, and zero admission/dispatch fallback events prove that all sixteen
controller-managed page loads executed. Every direct-retirement descriptor,
read, ALU, write, and fallback counter is zero: the earlier fused/direct-sink
optimization is not part of this result.

## Matched treatment and order

The two backed rows use the same 16K guest binary, the same `backedx3` argv,
the same immutable checkpoint, the same input, and the same simulator. After
normalizing output paths, their restore commands differ only in
`--maa_physical_tile_elements=16384` versus `4096`. The guest sequence is:

1. four direct-index virtual gathers into coherent backing;
2. sixteen controller-managed token-bound 4K materializations;
3. sixteen ordinary FP64 scalar multiplies; and
4. sixteen ordinary stream stores.

All backed replicas contain four per-unit instruction digests covering 8,638
source requests. The count/FNV/mix tuples match strictly across physical16,
physical4, and both replicas. This commits to exact request order within each
instruction; it does not claim identical global completion interleaving.

## Hardware-capacity boundary

The only defensible matched hardware claim is an exact **1,572,864-byte
physical SPD payload reduction**: 32 tiles x (16,384 - 4,096) elements x four
bytes. Backed16 and backed4 retain identical logical16 Row/Offset metadata and
the identical fixed instruction path.

| Capacity item | backed16 | backed4 |
|---|---:|---:|
| Physical SPD payload | 2,097,152 B | 524,288 B |
| Direct-index feeder | 8,192 B | 8,192 B |
| Source-response word pool | 8,192 B | 8,192 B |
| Destination combiner payload | 32,768 B | 32,768 B |
| Materializer line-buffer payload | 4,096 B | 4,096 B |
| `active_payload_capacity_bytes` subtotal | 2,150,400 B | 577,536 B |
| Separately emitted materializer C++ static control view | 33,952 B | 33,952 B |
| Separately emitted direct-stage control bytes | 640 B | 640 B |

`active_payload_capacity_bytes` is a payload-capacity subtotal, not total
hardware, area, or PPA. It excludes descriptor/header/readiness bits;
nonpayload tags and control beyond the separately emitted materializer control
views; ports, arbitration, wiring, and SRAM periphery; and synthesized area,
power, and timing. The 1,572,864-byte delta must not be described as total
DX100 cost.

## Provenance and disposition

- Accepted raw root:
  `/data1/nier/worktrees/codex-coordination/sessions/xrage-backed-attribution-20260823-123319-0bf18406/evidence/xrage-backed-matrix-95a6836e-r3`
- Guest source/build commit: `95a6836e8070cf0daeae579375f2c9e2df4ed73b`.
- Guest SHA-256: 16K
  `365aa7f2e9d83f0f5d789d3cc1357a98c31680244a50b75d92a9c193ff69726e`;
  4K `12f6c560196692d176963117975ffa6e569bbbb1a79ce1bfc6806e4bb3873dfc`.
- gem5 SHA-256:
  `44b6e86ebc86fd692ce02dcb2e1f627082ded10c741134ae49de5721e2edcb45`.
  Its `be77a62c` simulator tree is byte-identical to lead commit `5ba5bfe6`
  over `src/`, `configs/`, `SConstruct`, and `ext/ramulator2`.
- Input SHA-256:
  `70e3d82973d7a93300db950d2c81e9db5b6a37273b0f21da8344302ce53022d9`,
  matching the accepted 2026-08-13 XRAGE asset.
- Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.

All three checkpoints and all eight restores exited zero. The original r3
wrapper recorded campaign exit 1 solely because its first analyzer expected
ordinary `STRRD=16` for the controller-intercepted materializers. No simulation
was rerun or relabeled. The committed successor analyzer re-read the frozen
rows and emitted `analysis/report.pass`, with its own source commit and SHA-256
embedded in `analysis/report.json`. Roots r1 (pre-report-boundary, stopped) and
r2 (invalid FP64 destination tile pair) are rejected and are not evidence.
