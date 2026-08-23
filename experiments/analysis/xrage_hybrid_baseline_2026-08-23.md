# XRAGE hybrid baseline - 2026-08-23

## Scope

This checkpoint imports the accepted repeated XRAGE gather configuration from
`/data1/nier/dx100-runs/2026-08-13-xrage-integrated-overlap-4d46c271`.
It is one exact 64K-element LANL XRAGE gather configuration, not the complete
`all.json` suite and not a GZP-style logical16 RMW result.

Every reported row passed exact output, artifact identity, terminal exit,
resolved configuration, and two-channel Ramulator validation. Architectural
performance uses first-ROI `simTicks`; host time is excluded.

## Repeated result

| arm | logical | physical | replica 1 | replica 2 | versus native16 |
|---|---:|---:|---:|---:|---:|
| native16 | 16,384 | 16,384 | 42,312,279 | 42,312,279 | baseline |
| native4 | 4,096 | 4,096 | 51,676,926 | 51,676,926 | 22.132% slower |
| direct-index hybrid | 16,384 | 4,096 | 41,547,933 | 41,547,933 | 1.806% lower latency |

The selected direct-index point is 1.01839673x faster than native16 and
1.24379054x faster than native4. It uses 128 index feeder lines, 1,024
response-pool words, four LLC ports, and the existing 16K logical / 4K physical
geometry. Both replicas are exact.

The hybrid removes the ordinary B-stream instructions and lowers first-ROI
memory-controller reads by 5,679 versus native16. Full-run Ramulator activity
has 1,611 fewer reads but 2,635 more activations and 2,440 more precharges, so
the result is not attributed to universally better DRAM row locality. The
measured improvement comes from the integrated direct-index feeder and bounded
response path as a whole.

## Goal interpretation

This result proves that a 16K-logical/4K-physical hybrid can match or exceed
native16 on one real LANL kernel. It counts as one application configuration
for the single-digit-gap objective, but broad XRAGE generality still requires
the complete multi-configuration suite or additional representative kernels.
Keep terminal direct-sink/fused XRAGE results separate because they change the
consumer dataflow rather than isolate payload virtualization.

Primary evidence:

- `comparison-recovered-3ecf9568/xrage_comparison.md`
- `comparison-recovered-3ecf9568/xrage_comparison.tsv`
- `comparison-recovered-3ecf9568/xrage_comparison.pass`
