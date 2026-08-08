# Reorder-survival instrumentation findings — 2026-08-08

## Findings first

The native row64 16K arm does **not** preserve one unrestricted 16K
reordering domain. It selected, admitted, and issued exactly 16,384
descriptors in one 16K Offset/partition epoch, but the RowTable reported 845
finite full/drain events. The measured maximum jointly visible population was
11,487 descriptors. The fail-closed classification is therefore
`inherited/partitioned`, not `preserved`.

Instrumentation is treatment-neutral in the exact row64 off/on pair. Both
restores used the same committed gem5 binary, workload, private checkpoint,
Ramulator library, and architectural configuration. Their `result.tsv` files
are byte-identical: output hash `7228541527853630339`, `simTicks=40246479`,
`simInsts=31107`, 845 RT-full events, and 103 build rounds. The issue digest is
also identical (`count=9858`, FNV `0xbf33d13be55871f4`, mix
`0x4f1997886ed98f98`). The disabled arm emitted zero reorder-survival records.

The row128 arm is a **high-cost/no-drain diagnostic reference, not the native
baseline**. It proved 16,384 selected/admitted/issued descriptors, one epoch,
`max_joint_admissions=16384`, and zero mid-instruction drains, so it earns the
narrow `preserved` label. Exact output still matched, but it took 40,643,989
ticks, 397,510 ticks (+0.988%) more than row64 in this smoke. Its issue digest
changed to 9,523 lines (FNV `0x21f0e181cd818a97`, mix
`0x6f1607869fc2071d`), and row transitions fell from 9,856 to 9,522.
These order observations are descriptive; neither transitions nor the digest
alone establishes global reorder preservation.

## Claim boundary

A 16K reorder-preservation claim requires all of the following in the measured
record:

- exactly 16,384 actually selected descriptors and 16,384 admissions;
- exact admission-to-issued-entry reconciliation;
- one Offset/partition epoch and no predicate;
- zero RT-full, Offset, or partition drains; and
- `max_joint_admissions=16384`.

Any predicated, smaller, irreconcilable, or finite-drain case is reported as
measured visibility with classification `inherited/partitioned`. Nominal
Offset/Row capacity and `my_max` are not used as substitutes for actual
selection or joint visibility.

## Mechanism and fail-closed checks

`MAAReorderTrace` is opt-in and is deliberately absent from `MAAAll`. When it
is disabled, hooks return before mapping addresses or mutating tracker state;
they schedule no event and charge no simulated latency. Enabled state is
constant-size. It emits one record per Offset/partition epoch plus one summary
per indirect instruction, not one event per request.

Semantic selections are counted after predicate/partition filtering and use a
bounded retry identity so RowTable/Offset pressure cannot count the same
iteration twice. Successful RowTable insertions count admissions. A-line issue
records only the line and row transition; it cannot credit descriptor entries.
Entries are credited exactly once when the response chain consumes them.
`max_joint_admissions` is the high-water of admitted-but-not-yet-consumed
descriptors. RT-full events stay within the current 16K Offset epoch; Offset or
partition boundaries close an epoch only after its entries reconcile.

The simulator panics on selection/admission mismatch, RowTable pressure-count
mismatch, source-line mismatch, or admission/issued-entry mismatch. The Python
analyzer rejects missing fields, missing/duplicate summaries, noncontiguous
epoch IDs, identity drift, invalid boundaries, per-epoch or total
reconciliation failures, and overstated classifications.

## Row128 bounded storage delta

For this one-MAA/one-indirect-unit configuration, changing rows/slice from 64
to 128 doubles all four allocated RowTable organizations (2/4/8/16 slices):
32,768 to 65,536 entry slots and 1,920 to 3,840 rows. Using the existing
semantic array ledger (18 bytes per entry slot; 14 bytes per row), the bounded
delta is 616,704 bytes (4,933,632 bits). The active 16-slice organization alone
adds 161,792 bytes. These are semantic core-array counts; C++ padding and
allocator overhead are excluded, and the result is not synthesized area.

## Validation and provenance

- Source checkpoints: `fbc0cd781b1375a8c8a32dcf41745e002e608720`, then
  live-visibility fix `54be6daf3464bb5466f601282cfe14d1d69fb19d`.
- Optimized gem5 SHA-256: `e70247395b8c3233b4c5379ea5f6953fb652a09948292d00189d35047f33f3ef`.
- Workload SHA-256: `84062d31e627721bfdbe0501d0454c2474ff7e05494d6a3e0db64e4996ef2a94`.
- Ramulator SHA-256: `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
  The 615-file local and authenticated donor dependency manifests match at
  SHA-256 `1fa7e1f4ed5a82afa62028b66f3fbc3623f19165d2d487c08a931a87681e485a`;
  nothing was downloaded.
- Private matched-checkpoint manifest SHA-256:
  `3a326ffec2a7ad34279f0544d7e3d19d8fed5f62a345fe50e18cf83fe3f4c586`.
- Focused C++ unit/source-contract/analyzer suite: 15 tests pass; optimized
  incremental build passes.
- Final raw root:
  `/data1/nier/dx100-runs/2026-08-08-reorder-survival-54be6daf`.
- Its 170-entry raw-artifact manifest has SHA-256
  `688500b071c29a90772d7e631240a0c1033a1fa8b8897a1f3fa7dfb53456dcd4`.
- Machine-readable evidence:
  `experiments/evidence/2026-08-08_reorder_survival_smoke.json`.

The preliminary raw root ending in `fbc0cd78` is retained but excluded: its
first attempt exposed an absolute selector embedded by an older checkpoint,
and a later analyzer rejection exposed the initial RT-full aggregation bug.
No result from that root contributes to the findings above.
