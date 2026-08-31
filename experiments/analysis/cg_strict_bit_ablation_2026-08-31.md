# CG page-fed strict-bit ablation (2026-08-31)

## Decision

The strict-ordering flag is **not** the source of the CG regression. The
same page-fed `CG_NA=256` treatment takes exactly `266,578,031 simTicks` with
strict ordering enabled and disabled.

| Arm | Page-fed selector | Strict B-before-A flag | `simTicks` |
|---|---|---:|---:|
| page-fed nonstrict | `page_fed_product_soa_jit` | off | 266,578,031 |
| strict two-pass | `page_fed_product_soa_jit` | on | 266,578,031 |

The arms use the same frozen gem5 binary, guest, input, checkpoint, feeder,
Row/Offset geometry, combiner, 32-bank/32-byte payload path, and backing
configuration. The derived command removes exactly
`--maa_virtual_strict_two_phase`; normalized commands otherwise match.

Both arms preserve the exact CG fingerprint and all 11 deterministic
reductions. They also match exactly on 9,832,530 simulated instructions,
20 indirect instructions, 40 stream writes, 163,840 index words, 26,672
backing writes/completions, 38,571 payload-read cycles, and 5,536 bank-conflict
cycles. Only strict diagnostic counters differ.

## Interpretation

The page-fed producer/consumer implementation already imposes the effective
ordering exercised by this workload. Removing the explicit strict fence does
not create additional overlap or reduce work. Therefore the prior 45.4366%
strict-versus-legacy slowdown must be attributed to the broader page-fed
execution/publication path, not to B-before-A serialization.

The next CG optimization should target page-fed product publication and
consumer work: 20 page-fed operations, 40 stream writes, 26,672 backing
transactions, and the additional CPU/MAA instruction decomposition relative
to `legacy_4k`. Relaxing correctness ordering is rejected as ineffective.

## Evidence

Raw root:
`/data1/nier/dx100-runs/2026-08-31-cg-na256-pagefed-strict-bit-r1`.

- result SHA-256:
  `9af5e1a882226dc398773d9623e9e6a699a439961caa6000f53671b2590eafa3`;
- raw artifact-ledger SHA-256:
  `af4fce14e4567f22b0d07efcbd5e10afaa96a9795793b8c25d966167a9f5de24`;
- nonstrict stats SHA-256:
  `cabdc87bf1f82b5a9d16189bcb3d3d96a899fe77171ffb61f75d98f6945a2f9a`;
- nonstrict restore-log SHA-256:
  `e52e10723d3621bc37bbd32546f250d0b9b5f86e310abbe469785ec7887a52f6`.

This is one deterministic small-CG observation, not full-application or
variability evidence.
