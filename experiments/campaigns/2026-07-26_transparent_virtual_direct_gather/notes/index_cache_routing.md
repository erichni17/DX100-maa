# Direct-index cache-routing diagnostic

The direct-index 4K path normally sends its bounded sequential B-line feeder
directly to memory. `virtual_index_force_cache` is an attribution-only switch
that routes only those reads through the cache hierarchy. It does not change
the logical 16K reorder window, A request order, C retirement, physical SPD
capacity, or modeled private storage.

FLAG `static_2d/001.fp/config_00_gather.json` used one frozen simulator and
guest. Both arms produced exact output hash `17529267342572166465`, two stats
blocks, normal `m5_exit`, 3,996 issued/completed C writes, 31,923 B words, and
zero indirect SPD reads. The two A-request digests matched strictly across
12,297 source requests. A reverse-order replication from one shared checkpoint
reproduced every result and DRAM count exactly.

| Routing | ROI `simTicks` | Total RD | Total ACT | Total PRE |
|---|---:|---:|---:|---:|
| Direct memory | 36,840,100 | 30,505 | 4,781 | 3,204 |
| Cache hierarchy | 36,897,692 | 28,510 | 4,274 | 2,729 |

Cache routing reduced reads by 6.540%, activates by 10.604%, and precharges by
14.825%, but increased ROI latency by 0.156%. It shifted 1,995 accesses from
the memory counter to the cache-access counter while preserving total source
work. B fill increased from 14,537 to 14,715 MAA cycles, and all-pages-ready
increased from 77,309 to 77,493 cycles. The latter 184-cycle delay accounts for
almost the entire 57,592-tick ROI regression at 3.2 GHz.

This treatment is rejected for performance. Sequential B traffic is real, but
it was not the limiting critical path in this case; the cache route saved DRAM
commands while adding enough access latency to lose. The switch adds no data
storage, but a hardware implementation would also consume cache bandwidth and
capacity. It remains disabled by default and should not be presented as part of
the preferred virtual mechanism.

Evidence roots:

- `/data1/nier/dx100-runs/2026-07-29-flag00-index-cache-74365e5`
- `/data1/nier/dx100-runs/2026-07-29-flag00-index-cache-repeat-74365e5`

Simulator commit `74365e529589854e9c3f1dabc325438754d95a2c`, gem5 SHA-256
`5943d3c1d58f499f7edb165b1ed2d662dbdb21d6a2adf949b19df3144ac0678e`.
