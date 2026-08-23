# XRAGE fused direct-sink attribution - 2026-08-23

## Result

The exact repeated `direct4x3` arm is accepted for fusion attribution. Both
replicas complete at **40,895,954 first-ROI simTicks** with exact 65,536-element
output key `65536:5576400619275092867`. The accepted non-fused `backed4` row is
52,516,705 ticks, so the decisive fixed-physical4 comparison is **1.284154x**
for direct4x3. This is a fusion/direct-sink gain over the non-fused backed path;
it is **not** a virtualization gain.

The ordinary controls remain context rather than treatment-matched attribution:
native16 is 42,489,437 ticks and native4 is 49,396,095 ticks. Direct4x3 is
1.038964x and 1.207848x faster than those controls, respectively. All values
are first-window `simTicks`; host time and the later verification window are
excluded.

| Arm | Replica 1 | Replica 2 | Logical / physical | Role |
|---|---:|---:|---:|---|
| native16 | 42,489,437 | 42,489,437 | 16K / 16K | ordinary control |
| native4 | 49,396,095 | 49,396,095 | 4K / 4K | ordinary control |
| backed4 | 52,516,705 | 52,516,705 | 16K / 4K | non-fused backed path |
| direct4x3 | 40,895,954 | 40,895,954 | 16K / 4K | fused direct sink |

## Exact path and closure

The direct arm has its own immutable checkpoint command. Its checkpointed argv
contains exactly one `--maa-arm direct4x3` and no `backedx3`. Both restores use
the same checkpoint, exact accepted 16K guest, accepted `xrage_gather0_64k`
input, accepted gem5/Ramulator, logical16/physical4 geometry, transparent SPD
mode 3, and direct-retirement line handoff. After normalizing only the output
directory, the two restore commands are identical.

Each direct replica matches accepted backed4 exactly for all **8,638 source
requests** in address order within each of the four indirect instructions. The
count/FNV/mix instruction digests also match. This commits to source request
order per instruction, not global completion interleaving.

The first ROI closes with:

- four indirect gathers and no ordinary stream-load, ordinary ALU, or ordinary
  stream-store instructions;
- four direct-retirement descriptors and four simultaneously live contexts;
- 16 exact producer page acknowledgements and 8,192 exact producer line
  acknowledgements;
- 8,192 read issues/responses, 8,192 ALU issues/completions, and 8,192 write
  issues/responses; and
- zero early-line overflow, page-fallback line, context-full stall, or
  descriptor fallback.

Every direct replica has four exact direct-retirement summary events. It has no
page-materializer lifecycle event and every materializer activity stat is zero.
Thus the accepted `backed4` controller-managed materializer is not active in the
direct arm.

## Hardware-capacity boundary

| Capacity/control item | direct4x3 |
|---|---:|
| Physical SPD payload | 524,288 B |
| Direct-index feeder | 8,192 B |
| Source-response word pool | 8,192 B |
| Destination combiner payload | 32,768 B |
| Incremental direct-handoff payload | 4,096 B |
| Active payload-capacity subtotal | 577,536 B |
| Separately emitted direct-handoff control view | 27,168 B |

The 4,096-byte payload and 27,168-byte control values are exact modeled C++
persistent charges emitted by the accepted simulator. The 577,536-byte value is
a payload-capacity subtotal, not total hardware or area. It excludes ports,
arbitration, wiring, SRAM periphery, physical implementation overhead, and
synthesized area, power, and timing. The direct and backed control views overlap
configurable structures and must not be subtracted as an area delta.

## Provenance and fail-closed disposition

- Raw root:
  `/data1/nier/worktrees/codex-coordination/sessions/xrage-fusion-attribution-20260823-135841-2a24461c/evidence/xrage-fusion-matrix-3cf9ad3b-r1`.
- Accepted-control manifest SHA-256:
  `a5c9efdbf955fcd24e58b72bdaefb9a93210f9cb27eba1a6365281011be3754d`;
  accepted-control report SHA-256:
  `346ec9d1d92973eac170296c134d629a5326ef191624c7adb35dfcae8e3e8d50`.
  Six per-row aggregate identities are independently pinned in the analyzer.
- Direct manifest SHA-256:
  `c43969a348d3294a294e0bd5f0d71f964aa75b027b77365ea2b074a29265fd0d`;
  direct checkpoint-command SHA-256:
  `b343416f605ff10021e998e555ea547200463f664804d84820df8e060cc6e8e1`.
- Analysis report SHA-256:
  `d60d47ee977f7d1c68e0eb6fd800922b1039bc527a04f2ab6d1103ad133677ea`;
  analyzer SHA-256:
  `13088e2fc5feeeb66ea7f43254170781252714193b226958872be671fa20e2ea`.
- Guest source/build commit: `95a6836e8070cf0daeae579375f2c9e2df4ed73b`;
  current XRAGE guest/API bytes match that commit. Guest SHA-256:
  `365aa7f2e9d83f0f5d789d3cc1357a98c31680244a50b75d92a9c193ff69726e`.
- gem5 SHA-256:
  `44b6e86ebc86fd692ce02dcb2e1f627082ded10c741134ae49de5721e2edcb45`;
  its `be77a62c` simulator source tree matches the current lead. Ramulator
  SHA-256: `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
- Input SHA-256:
  `70e3d82973d7a93300db950d2c81e9db5b6a37273b0f21da8344302ce53022d9`.
- Runner commit: `3cf9ad3bfda18ea74c522f02bf08f2387203feb3`;
  successor analyzer commit: `a2d14ce36f45aa2d05a846f310666d0d7095f345`.

The checkpoint and both restores exited zero. The original wrapper correctly
left `campaign.exit=1` because its first analyzer called a helper from the wrong
module; it emitted no PASS report. The committed successor analyzer repaired
only that analysis helper and updated the direct control charge from the
simulator-emitted 26,912-byte historical value to the observed **27,168-byte**
accepted-simulator value. It re-read the frozen checkpoint and restore rows,
then emitted `analysis/report.pass` without rerunning or relabeling simulation.
The result is accepted only through that successor report and its embedded clean
source commit/hash; a missing hash, row, terminal marker, exact output/order
match, closure counter, or zero-materializer proof fails analysis.
