# GZP dual masked-index RMW result (2026-08-23)

The opt-in `token_stream_ld dual_masked_index` treatment is the strongest
validated general-hybrid GZP arm in this matrix. It preserves the existing
selectors, uses logical-16K Row/Offset with physical-4K payload pages, executes
both full-window RMWs in masked-index SoA/JIT mode, and leaves the tail on the
existing physical path.

## Accepted exact matrix

Raw root:
`/data1/nier/dx100-runs/2026-08-23-gzp-dual-masked-rmw-86bbbfb-r1`

All three replicas of every accepted arm are bit- and tick-deterministic. Every
row has output hash `11225737641199706160`, zero nonfinite values, and zero
point-volume/point-gradient reference errors over 1,180,000 points.

| Arm | simTicks (each of 3 replicas) | Relative to dual |
| --- | ---: | ---: |
| native16 | 5,827,700,676 | dual has 8.484% more ticks |
| dual masked owner64/pre-A/context64 | 6,322,114,850 | 1.000x |
| volume masked owner64/pre-A/context64 | 6,732,976,178 | dual speedup 1.064988x |
| native4 | 7,640,934,403 | dual speedup 1.208604x |

Only simulated `simTicks` participates in these comparisons. Host time is not
an architecture metric.

## Exact mechanism and accounting

Each accepted dual replica closes with 61 volume RMWs, 244 gradient-page
publications, and 61 gradient RMWs. The trace contains exactly 244
`spd_publish_terminal` records and 122 masked SoA/JIT terminal records. First
stats-window counts are exactly:

- publisher issues = accepts = WriteResps = 62,464;
- publisher terminals = 244;
- SoA/JIT terminal completions = 122;
- predicate publication bytes = 0.

Four guest/OpenMP owners share one measured `StreamAccessUnit` publisher
(`--maa_num_maas=1`). Dedicated publisher state is 512 B retained payload plus
408 B control, 920 B total. The four per-core logical-16K FP32 gradient backing
arrays occupy 262,144 B of coherent LLC/DRAM address space and are not MAA
SRAM. One full pass publishes 999,424 values = 3,997,696 B = 62,464 exact 64 B
writes/WriteResps.

## Recovery provenance

Implementation and recovery commits are `c6c7cc19`, `8285fd09`, and
`c961d0e9`. Native16 replica 1 was adopted only after a stopped-parent/zombie-
child audit proved kernel exit code 0, a unique `m5_exit`, complete stats, and
the exact output/reference markers. The remaining independent restores were
run concurrently.

Volume and dual use separate checkpoint groups with separate immutable selector
paths. The earlier three dual rows enabled only `MAAVirtualTrace`; they are
preserved but rejected because `spd_publish_terminal` is emitted under
`MAATrace`. Exactly three replacement dual rows added
`MAAVirtualTrace,MAATrace`; no native or volume row was rerun for that
instrumentation repair.

Terminal artifact SHA-256 values:

- `recovery.json`: `decac502209d6ec148513a933abc7a6ade0210d339c861a3545db976ac555041`
- `results.json`: `01b76b100534b6ccf8c6c6f1ccede2ff1f076a40a41cb580d22f54f16daac789`
- `results.tsv`: `4f23c70f4a7b434bcb6775cdf09bf7977d3fd482aa4e04d27126f23f93f44689`
- `summary.txt`: `9b1c05ac9e396a79014ac1ff4f96360e79a127b675f9bed88be3b8debd786357`

The full checkpoint file hashes, selector hashes, accepted command hashes, and
rejected-row hashes are frozen in `recovery.json`.
