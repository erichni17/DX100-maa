# XRAGE Row-Table Visibility Diagnostic

## Result

The matched diagnostic passed, but increasing RowTable rows per slice from 64
to 128 was not visible to this XRAGE gather0 execution. Row64 and row128 had
the same ROI `simTicks` (`1,284,958,900`), a row128 delta of `0` ticks
(`0.0%`). Their result summaries and complete reorder/issue trace files were
also byte-identical across arms and across the deterministic replay.

This is evidence that the row128 capacity treatment was not exercised in a
way that changed this workload: every indirect instruction admitted all
16,384 selected descriptors in one epoch, and neither arm recorded an
RT-full drain. Row128 remains a high-cost diagnostic and is never a baseline.
Issue counts, issue digests, and row transitions are descriptive mechanism
evidence; none is used alone to claim causality for `simTicks`.

## Matched design and validation

The simulator was built from integration commit
`f60a5b8da5cbb1a355dbca99b1cb721b3980953a`. The two arms used one frozen
instrumented gem5 binary, XRAGE verifier/input, authenticated Ramulator
library, and treatment-neutral pre-MAA checkpoint. Runs were serialized to
isolate restore output and selector state. All non-treatment settings were
identical: logical 16K, physical 4K, 16K offset entries, epoch 16K, one direct
index pass, one indirect unit, and both `MAAReorderTrace` and
`MAAIssueDigest`. The only treatment was 64 versus 128 rows per slice.

Frozen identities:

- gem5: `c0ae048c24b973085d6d85272b2fdae8e283392b6f862382cc182dbb34ef774c`
- XRAGE verifier: `c4acb453ec043c8d45f09f07333277da904d3929da5ff8627b6cbbe704d5d414`
- XRAGE input: `1a56db824f4fd58222d4246504e2a6fcdb0b691cd380ec18be5531ae76c1ccde`
- Ramulator: `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`
- checkpoint manifest: `9574cb33ad58a769808e218355d6f912555f063749dd6e8f6fdbc6d4cdf52293`
- frozen checkpoint-file manifest: `5f9e9b80fc2377bba8f348228339826a736c411e51336de39fb32e2aa0cbc8be`

Each of the four runs produced the exact 2,097,152-element hash
`11014995430510232451`, a terminal `m5_exit`, exactly two stats blocks, and
128 indirect-instruction summaries. The analyzer matched all 128 summaries to
all 128 `MAAIssueDigest` records by unit and operation tick in every run, and
the reorder audit reconciled every instruction. Binary, workload, input, and
checkpoint identities matched across all runs.

## Repetition decision

Rep1 produced a `0.0%` ROI delta, below the 2% threshold. One replay per arm
was therefore run. Within each arm, rep1 and rep2 had byte-identical
`result.tsv` and trace files and identical ROI/final ticks. The same identities
also held across arms:

- result SHA-256: `a7fa55fe70b326d4b5dbaf3ffdbbb5cd67e5fc8262d8693b1dd1d9c498b065fd`
- reorder/issue trace SHA-256: `03f2b006da7c27d6c1b44cdab4cb8a285823919ea038e8dfc57faf53b658a4d4`
- ROI/final ticks: `1,284,958,900` / `9,553,636,087`

The campaign consequently stopped after two reps per arm. Three reps per arm
were not warranted because deterministic replay was already byte/tick
identical.

## Mechanism distributions

The table reports the 128-instruction distribution for each arm. Every value
was identical in all four runs. Percentiles use nearest rank; full exact-value
histograms are retained in raw `analysis.json`.

| Metric | Sum | Min | P25 | P50 | P75 | P90 | P95 | P99 | Max | Mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| max_joint admissions | 2,097,152 | 16,384 | 16,384 | 16,384 | 16,384 | 16,384 | 16,384 | 16,384 | 16,384 | 16,384.000 |
| RT-full drains | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000 |
| issued A lines | 299,046 | 1,961 | 2,178 | 2,299 | 2,453 | 2,570 | 2,618 | 2,815 | 2,840 | 2,336.297 |
| row transitions | 297,946 | 1,953 | 2,171 | 2,292 | 2,450 | 2,565 | 2,607 | 2,806 | 2,828 | 2,327.703 |

Aggregate cycle and DRAM command evidence was likewise identical:

| Arm | Fill cycles | Request cycles | RD | WR | ACT | PRE | ROI simTicks |
|---|---:|---:|---:|---:|---:|---:|---:|
| row64 | 1,613,731 | 2,420,806 | 1,000,667 | 262,163 | 224,722 | 209,540 | 1,284,958,900 |
| row128 diagnostic | 1,613,731 | 2,420,806 | 1,000,667 | 262,163 | 224,722 | 209,540 | 1,284,958,900 |

The two DRAM channels contributed RD/WR/ACT/PRE of
`500,298/131,082/111,692/104,120` and
`500,369/131,081/113,030/105,420`, respectively, in every run.

## Semantic RowTable storage delta

For one MAA and the configured indirect unit, doubling rows per slice adds
1,920 rows and 32,768 entry slots across the four RowTable organizations
allocated by this configuration. Counting the semantic core arrays gives:

- active 16-slice organization: +161,792 bytes;
- all four allocated organizations: +616,704 bytes
  (`4,933,632` bits).

This accounting uses 14 semantic bytes per row plus 18 bytes per entry slot.
It excludes C++ object padding, allocator overhead, and control metadata, and
is not a synthesized area estimate.

## Evidence

Raw root:
`/data1/nier/worktrees/codex-coordination/sessions/xrage-row-visibility-20260808-20260808-113447-2b30ba1d/xrage-row-visibility-f60a5b8d`

- campaign manifest SHA-256:
  `358a253da8d5c5752b614d5d3d594900cd360a3c34de998f08b05af03c0986a6`
- fail-closed analysis SHA-256:
  `4ab9645bc4d3973344cb916fb0aec22dff01519c61f915211ccdc5ca01b6d6cd`
- raw evidence checksum manifest SHA-256:
  `b171ef04dde4933ee150338af048b52c31e891b05c2886bb7130ccf6c96c6803`

The committed compact evidence record is
`experiments/evidence/2026-08-08_xrage_row_visibility.json`.
