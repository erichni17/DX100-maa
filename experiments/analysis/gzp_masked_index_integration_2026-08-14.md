# GZP masked-index integration — 2026-08-14

## Decision

Accept the optional fixed-input GZP volume treatment for exact matched
evaluation.  The full same-checkpoint pair measured a **1.466%**
baseline-over-masked `simTicks` speedup while preserving the exact scalar
reference, output hash, logical selection partition, index hash, 16K
Row/Offset metadata, physical 4K SPD geometry, and existing FP32 update order.

This does not change the legacy, separate-predicate volume-only, or full-SoA
selector arms.  It does not justify removing the general predicate feeder.

## Equivalence audit and fail-closed encoding

The starting commit `866150f9` did not actually contain the presumed inactive
index convention: inactive corners retained random legal `c_to_p_map` entries,
and only `corner_type >= 1` rejected them.  The integration therefore consumes
the original RNG draws first, preserves every active point index and every zone
index, and only then canonicalizes inactive point indices to signed `-1`, whose
32-bit representation is `UINT32_MAX`.

Before checkpoint creation, the guest independently scans the resulting index
and predicate arrays.  It aborts unless all of these hold:

- selected + rejected equals the corner count;
- selected equals the separately materialized `corner_type > 0` predicate
  count;
- no active lane contains `UINT32_MAX` or any other illegal point index; and
- no inactive lane contains a legal point index or any non-sentinel encoding.

The restored guest prints the ledger, and the runner requires the aggregate
MAA selected/rejected counters and every 16K terminal generation to agree.  On
the full input the guest reported 949,959 selected and 50,041 rejected overall;
the 61 complete windows consumed by the SoA/JIT volume operation reported
949,411 selected and 50,013 rejected.  All four illegal-state counters were
zero in both arms, with identical index hash `15605778284598092602`.

## Narrow treatment

The restore-only selector `token_stream_ld volume_masked_index` replaces only
the full-window volume call to
`maa_indirect_rmw_vector_soa_jit` with
`maa_indirect_rmw_vector_soa_jit_masked_indices`.  It keeps `point_volume`,
`c_to_p_map + c`, `corner_volume + c`, the explicit completion tile/wait, the
ordinary 4K gradient path, the tail path, and the duplicate FP update order.
For this treatment the guest does not register or name a separate predicate
region.  Existing selectors retain their original calls and registrations.

## Matched gem5 evidence

Accepted raw roots:

- one window: `/data1/nier/dx100-runs/2026-08-14-gzp-masked-index-one-window-a3d0bba5-r1`
- full fixed input: `/data1/nier/dx100-runs/2026-08-14-gzp-masked-index-full-a3d0bba5-r1`

Both use source commit `a3d0bba5b1173b16fa79253f6cf2fb005189af63`,
guest SHA-256
`00980813e3bbcd74aec84d4352c545f5ff956485cac99c456fadfddfcab8ecda`,
and masked-index gem5 SHA-256
`13c1a0d8497f6bd488534fe439a70834644d57893e6123d9238bdb34496094c2`.
The Ramulator library SHA-256 is
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
Within each pair, the guest, checkpoint, config, input, memory geometry, cache
settings, and all non-treatment MAA knobs are identical.  The selector is read
only after restore.  Wrapper exits are zero; each arm has one `m5_exit`, a
complete first stats window, one exact reference terminal, and no fatal marker.

### One-window exact gate

| Metric | Separate predicate | Masked index | Delta |
|---|---:|---:|---:|
| `simTicks` | 441,456,139 | 440,955,652 | -500,487 |
| fill cycles | 50,713 | 48,303 | -2,410 |
| request cycles | 467,371 | 467,443 | +72 |
| index lines | 2,049 | 2,049 | 0 |
| predicate lines | 1,025 | 0 | -1,025 |
| selected / rejected | 15,559 / 825 | 15,559 / 825 | equal |
| output hash | 12472729817211538253 | 12472729817211538253 | exact |

The observed gate speedup is 1.001135x (0.1135%).  The 64 KiB predicate
`std::vector` is not cache-line aligned, so its measured span is 1,025 lines,
not an assumed 1,024.

### Full fixed-input pair

| Metric | Separate predicate | Masked index | Delta |
|---|---:|---:|---:|
| `simTicks` | 9,531,535,470 | 9,393,821,730 | -137,713,740 |
| fill cycles | 4,523,605 | 4,095,761 | -427,844 |
| request cycles | 21,234,220 | 21,135,295 | -98,925 |
| index lines | 125,087 | 125,087 | 0 |
| predicate lines | 62,525 | 0 | -62,525 |
| selected / rejected | 949,411 / 50,013 | 949,411 / 50,013 | equal |
| output hash | 11225737641199706160 | 11225737641199706160 | exact |

The exact full-pair result is **1.014660x baseline/masked, or 1.466% speedup**.
Candidate `simTicks` are 1.4448% below baseline.  Fill cycles fall 9.4580% and
request cycles fall 0.4659%.  The treatment avoids 62,525 measured predicate
cache lines (4,001,600 line bytes) and one treatment-visible 4,000,000-byte
predicate publication.

## Hardware and accounting limits

The incremental modeled control is one 32-bit sentinel equality comparator,
one instruction-mode state bit, and zero additional MAA buffer bytes.  The
existing index line and Row/Offset storage are reused.  This is a logical
storage/control ledger, not a synthesized area, timing, power, or frequency
result.  The optional mode leaves the legacy predicate feeder and its storage
in hardware, so no feeder-area saving is claimed.

“Publication avoided” means the masked treatment neither registers nor names
the separate predicate buffer.  The matched guest/checkpoint must still contain
and initialize that buffer so the separate-predicate control can restore from
the exact same checkpoint; no checkpoint-size or guest-allocation saving is
claimed.  The 4,001,600 line-byte figure reflects the measured unaligned cache
span, while 4,000,000 bytes is the logical predicate payload.  Results are one
deterministic observation per arm, not a repeated/noise-characterized promotion
study.

Rejected raw attempts are not evidence: the earlier `...03090348-r1` wrapper
was interrupted after checkpoint creation, `...03090348-r2` exposed a
non-finite-formula parser bug, and `...6982f352-r1` exposed the invalid aligned
line-count assumption.  Both analyzer defects have focused regression tests;
only the two `a3d0bba5-r1` roots above are accepted.

## Validation

- optimized GZP guest build with `-Wall -Wextra -Werror`;
- focused direct-import tests for the GZP ledger/treatment/runner and the base
  masked-index ABI contract (the environment does not provide `pytest`);
- Python bytecode compilation, gem5 source style hooks, Black/isort/pyupgrade,
  and `git diff --check`;
- one-window and full same-checkpoint exact gem5 gates, including output,
  selection, request/response, generation, mechanism, and checkpoint identity
  closure.
