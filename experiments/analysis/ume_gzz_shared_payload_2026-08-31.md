# UME GZZ shared-payload recovery

## Result

The strict logical-16K/physical-4K GZZ path now completes with exact output and
bounded result storage. This is correctness and mechanism evidence, not yet a
performance result.

- Decision: `ACCEPT_GZZ_SHARED_PAYLOAD_CORRECTNESS`
- Output: `7602200327591349891`, exact match to the frozen reference
- Reference errors: zero volume errors, zero gradient errors, zero nonfinite
  values across 196,384 outputs
- Candidate `simTicks`: 23,909,444
- Simulator SHA-256: `d1f8a3d5a736ef645849efee6323f1a6aa8cdd392bdff8b9aeb4d0d4adc6db47`
- Guest SHA-256: `a0df8598868e3ba96d767f49ada7f648cab4469a154ff6cfaa95d6d690e2bacb`
- Sealed evidence: `/data1/nier/dx100-runs/2026-08-31-ume-gzz-shared-payload-r4`

The 4,096-word physical result budget is shared dynamically between source
responses and destination combining. Each response word transfers ownership to
the combiner when consumed; the two occupancies never exceed 4,096 in total.

| Mechanism check | Observed |
|---|---:|
| Shared-pool high water / capacity | 4,096 / 4,096 words |
| Response-to-combiner transfers | 16,384 |
| Transfer rollbacks | 0 |
| Strict descriptors | 16,384 |
| Full-line backing writes / ACKs | 1,024 / 1,024 |
| Partial writes | 0 |
| Pages ready | 4 |
| A issued before admission closed | no |

## What was fixed

1. Selector resolution moved before checkpoint creation, so restore no longer
   depends on reopening a host selector file.
2. Static response/combiner splits were rejected. Five 4,096-word partitions
   all stalled because arbitrary reordered completion can fill either side.
3. A shared payload pool replaced the static split. The first run exposed a
   stale Build-state response-only admission check; commit `596bf24f` makes
   both new and pending source lines use the combined credit predicate.
4. The CPU page loop crossed the physical SPD aperture. Commit `9d565402`
   moves GZZ page arithmetic into MAA using two extra physical tiles for DIV
   and MUL before the existing RMW.
5. The page materializer produced exact output but left the strict reference
   lifecycle open. Commit `bbb93a4b` records consumer begin at first-page
   context creation and consumer end after exact four-page retirement.

## Performance caveat

Historical controls used a CPU page consumer, while the accepted candidate
uses MAA DIV/MUL. Their instruction mixes are not matched. The reused-control
ratios are therefore orientation only:

- historical native16 ticks / candidate ticks: 2.201
- historical native4 ticks / candidate ticks: 1.447

Do not report either as virtualization speedup. The next performance experiment
must apply the same MAA page consumer to native16, native4, and the strict
hybrid, then compare exact-output runs from one current simulator binary.

