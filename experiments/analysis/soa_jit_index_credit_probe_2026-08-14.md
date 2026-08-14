# SoA/JIT index-credit probe (2026-08-14)

Raw evidence is frozen at
`/data1/nier/dx100-runs/2026-08-14-soa-jit-index-credit-probe`.
Both arms restore the same exact `soa` checkpoint used by the earlier value-warm
diagnostic. They use logical/metadata 16K, physical SPD 4K, and differ only in
`maa_virtual_index_buffer_lines`.

| Active index lines | `simTicks` | Change from the prior 8-line arm |
|---:|---:|---:|
| 8 (prior control) | 636,278,294 | baseline |
| 16 | 633,904,189 | -0.373% |
| 32 | 630,113,446 | -0.969% |

Both new arms report exact output hash `2761840269561229581`, `errors=0`, one
normal ROI terminator, and one normal `m5_exit`. The frozen gem5 and guest
SHA-256 values are respectively
`cd1217648670fde4bb84d7b1a4fe2346099e02d0cc30d1d78edaaabaec2eefa2` and
`c7fb4f8dd038cb129115f11a11390aa672bd4e9fba4f05573e4aa257e089c497`.

The result is diagnostic rather than a promoted performance claim: increasing
index-line credits from 8 to 16 or 32 has less than a 1% effect. Index ingestion
is therefore near saturation at eight active lines for this test. The next
implementation work should target the singular predicate feeder and the
serialized value/A response path, not provision a larger index buffer.
