# Strict RowTable capacity sensitivity (2026-08-24)

## Verdict

**REJECT_CAPACITY_REGRESSION.** Expanding the fixed physical RowTable from
8,192 to 16,384 line slots makes the strict all-B-before-A schedule legal and
exact, but does not make the hardware sensitivity useful:

- current scheduling at 8,192 slots: **45,316,140** first `simTicks`;
- current scheduling at 16,384 slots: **46,449,200** first `simTicks`;
- strict scheduling at 16,384 slots: **46,449,200** first `simTicks`.

The capacity-only change is 1,133,060 ticks slower, a candidate speedup of
`0.975606469` (2.5004% regression). Strict scheduling is exactly neutral at
the expanded capacity (`1.000000000`). It therefore provides no scheduling
benefit to offset the capacity regression or metadata cost. No steep
application candidate was launched.

## Geometry audit

This one-channel DDR4 configuration reports `CH=1`, `RA=1`, `BG=4`, and
`BA=4`, hence 16 RowTable sub-slices. `IndirectAccessUnit::allocate` derives:

- minimum slices = `CH * RA * 2 = 2`;
- organizations = 2, 4, 8, and 16 slices;
- sub-slices per slice = `16 / slices`;
- columns per row = `entries_per_subslice_row * sub-slices_per_slice`.

With eight entries per sub-slice row, each organization has

```text
line slots = slices * rows_per_slice * 8 * (16 / slices)
           = 128 * rows_per_slice.
```

Thus row64 has 8,192 slots and row128 has 16,384 slots irrespective of the
alternative organization. The measured fixed configuration uses 16 slices,
so its active geometry is 1,024/2,048 row directories and 8,192/16,384 line
slots at row64/row128.

`reconfigure_row_table=false` makes the 16-slice organization active. Because
range passes are also disabled, current C++ allocates all four alternative
organizations (`allocate_row_table = !virtual_index_range_passes ||
configured_row_table`). Only the fixed 16-slice organization is active in the
experiment. The 2/4/8-slice C++ arrays are unused alternatives and are reported
separately; they are not claimed as simultaneous physical hardware.

The sensitivity uses the existing default-64
`num_row_table_rows_per_slice` parameter at 128. Physical SPD remains 4,096
elements, logical/Offset capacity remains 16,384, and all response, combiner,
write, index-window, and page-consumer capacities remain unchanged. No range
pass, descriptor replay/spool, global merge, backing descriptor store, or
operation-sized hidden payload was added.

## Modeled metadata cost

For a 16K iteration domain, the packed lower-bound model uses 15-bit Offset
heads/tails. Each RowTable line slot therefore needs 95 bits
(`64-bit address + 15-bit first + 15-bit last + valid`), and each row directory
needs 66 bits (`64-bit grow + valid + sent`). Doubling rows gives:

| Scope | Slot delta | Row delta | Packed delta | Semantic C++ array delta |
|---|---:|---:|---:|---:|
| active fixed 16-slice organization | 8,192 | 1,024 | 845,824 bits / 105,728 B | 1,294,336 bits / 161,792 B |
| all four C++ organizations | 32,768 | 1,920 | 3,239,680 bits / 404,960 B | 4,933,632 bits / 616,704 B |

The semantic C++ view counts 18 bytes per line slot and 14 bytes per row,
excluding object padding and allocator overhead. It describes simulator core
arrays, not an RTL packing or area result. The packed view is a storage lower
bound and likewise excludes SRAM/BCAM periphery, ports, and wiring. **Neither
number is synthesized area.**

An independent run of `experiments/scripts/report_maa_storage.py` on each
frozen `config.ini` reproduced the active row-array delta of 105,728 bytes and
the all-organization row-array delta of 404,960 bytes. Its full allocated-model
delta is 409,068 bytes, including 4,096 bytes of allocated claim bits and 12
bytes of other geometry-width effects. Raw JSON SHA-256 values are:

- row64: `40ceba45e4bd1e6b49b23c5d5f3702c8b9cfa8e818149da0eca7abc2e7e7323c`;
- row128: `3e81195cf635b26d9313295c6c10d6480788d21422b9fe6cc1535a916f8a9172`.

The active packed delta is 6.7220% of the 1,572,864-byte logical-to-physical
SPD payload saving (and 20.1660% of the 512 KiB physical SPD). The semantic
C++ active delta is 10.2865% of that saving. Storage alone therefore does not
erase the 4K-SPD hybrid benefit when only the active physical organization is
charged. Charging all unused C++ alternatives would be 25.7467% packed or
39.2090% semantic-C++ of the payload saving, but that is an allocation-model
sensitivity, not a physical-area claim. Performance independently rejects the
candidate.

## Matched API evidence

All formal arms use:

| Provenance | Identity |
|---|---|
| source/config checkpoint | `ca8657ff44d00a213fe9f4118af4e4105faf581f` |
| strict logical-scan repair | `3956444d7b736940167a96ae66c2abc06fb4f7cd` |
| gem5 SHA-256 | `fba2cf1b6f35777a93210cef388595d4794864ea94629e68819efa37a06677e6` |
| API binary SHA-256 | `963940eeaface13cb53f73b565a88b2994922c2ff3ef55f167d9577df210c559` |
| Ramulator SHA-256 | `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753` |
| shared-checkpoint identity | `fbf962fbf4e2856fa62c9fcdc3e1d2b9395b0db5eeeacdd8a3240400a4dcc3fd` |

The clean build used authenticated local dependency copies. Recorded
identities are spdlog tree `e4777b0f171881301fb874bf526ea64dde3ca411c16fea5b51542f7b6a528336`,
yaml-cpp tree `903ab36390849e06fa7a1ccfd1fc4e8256a10b957294a341b7f755e3a876340a`,
and x86 `m5op.S`
`fe20d70d689c341ee614121d7aac1431b81d2178943a113ea1aa1d7c5ef50c69`.
The final build and an up-to-date follow-up both passed. No dependency was
downloaded.

Raw roots:

- row64 control/checkpoint:
  `/data1/nier/worktrees/codex-coordination/sessions/strict-rowtable-capacity-20260824-155904-08b7a9bd/evidence/api-row64-base-ca8657ff`;
- row128 pair and final analysis:
  `/data1/nier/worktrees/codex-coordination/sessions/strict-rowtable-capacity-20260824-155904-08b7a9bd/evidence/api-capacity-ca8657ff-row128`;
- independent cost review:
  `/data1/nier/worktrees/codex-coordination/sessions/strict-rowtable-capacity-20260824-155904-08b7a9bd/evidence/cost-review-row{64,128}-ca8657ff`.

The final `pair.tsv`, `effects.tsv`, and `rowtable_cost.tsv` hashes are
`542e09cf33d3047f9bc0d16560b79bf7f456cd2cfb47a5158d8dbcf8da892ee7`,
`934ba4d538488f71869bce6319061df7e17aff92efca829684f407e3d027e3f3`,
and `9c53f5da215e45c533480ebf8bcaa2b0113fd8099e2e6bc59e07636e72a2f03c`.

Each formal arm has restore exit 0, one `m5_exit`, one ROI end, exact benchmark
success, and nonempty final stats. There was no native arm and no timeout.

| Arm | Slots | First `simTicks` | RT/Offset drains | Source issue digest |
|---|---:|---:|---:|---|
| current row64 | 8,192 | 45,316,140 | 852 / 0 | 9,668 requests; `4a022ae325134d657bf7d104d2f0fb885a3df93be0b52a22cd301b59c00c399b` |
| current row128 | 16,384 | 46,449,200 | 0 / 0 | 9,523 requests; `fc080820e1c926e7931f2a417382714dc65233f6451dd7cf10053899d759062f` |
| strict row128 | 16,384 | 46,449,200 | 0 / 0 | 9,523 requests; `fc080820e1c926e7931f2a417382714dc65233f6451dd7cf10053899d759062f` |

All arms have exact output hash `7228541527853630339`. The two expanded arms
also match exact instruction, source digest, write, page, and stream ledgers.
The row64 source digest differs because the 852 capacity drains change source
issue grouping; output remains exact.

## Strict proof

The strict summary proves:

- 16,384 B words and 16,384 descriptor insertions in one semantic B scan;
- admission closed at tick `3451619707` and first A issue at `3451620020`,
  313 ticks after the last Row/Offset insertion;
- zero RowTable-full or Offset drains and zero range/replay/spool/global-merge
  activity;
- A issues/responses `9523/9523`;
- backing issues/ACKs `5337/5337`;
- four pages ready and one closed `hybrid_consumer_macro` ledger;
- terminal exact output and source digest with `descriptor_backing=none`.

The original f84 proof attempted to require monotonically increasing physical
B addresses. The transparent 4K mapping legitimately wraps physical addresses
within its one logical scan, so `3956444d` removes that invalid assertion and
retains the exact logical B-word/admission/closure proof. Formal evidence uses
the rebuilt binary; the earlier f84 and pre-repair row128 attempts are retained
but excluded.

## Handoff

Keep strict mode and row128 configuration default-off as diagnostics. Do not
promote row128, do not report a strict speedup, and do not launch CG, IS, SSSP,
GZZ, or another steep candidate from this result. The micro evidence proves
feasibility at 16,384 active slots, but capacity itself regresses and strict
scheduling adds no benefit.
