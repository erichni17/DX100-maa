# Independent final review: XRAGE complete-line hybrid (2026-08-29)

## Findings first

**Decision: accept the narrow functional/performance observation; do not
promote it to a paper-wide or hardware claim.**  The selected complete-line
run is a completed, exact-output XRAGE execution with a material improvement
over both the supplied native16 and bounded-control observations.  The source
does fail closed for the selected operation when it would evict or finally
drain a partial line, and its explicitly configured result pools fit within
the physical result-word capacity.

Two promotion blockers remain.

1. **The native16/safe timing ratio is not an exact same-checkpoint pair.**
   Native restores `cpt.136323941000`; control and safe restore
   `cpt.136324878000`.  The native checkpoint `m5.cpt` and pmem hashes differ
   from the other two.  Control and safe share the same checkpoint tick and
   pmem hash, but have different serialized `m5.cpt` hashes, as expected when
   their MAA configuration differs.  Thus the observations establish an
   exact-input, same-binary comparison with intentionally different
   architecture configurations, not byte-identical checkpoint state.  A
   promotion-grade native comparison needs a matched pre-ROI checkpoint/state
   attestation (or a documented replay construction) for every arm.
2. **The storage result is a capacity lower bound, not an implementation
   cost/timing result.**  The source ledger itself excludes ports,
   arbitration, wiring, memory periphery, and Fmax.  It does not establish a
   16-way tag lookup timing path, reset/epoch cost, full-line drain bandwidth,
   or physical RAM/register implementation.

No evidence supports a broader XRAGE-suite, paper, or synthesized-hardware
promotion now.

## Scope and provenance

Reviewed source commit `63795eaa0983c3caf16dde092ea398656e6cc34f`
(`maa: enforce complete-line virtual retirement`) as contained by final
branch commit `020b629ab8603b2ce7680e3356d0920f0725340b`.  This review did
not alter production source or launch gem5.

The frozen roots were exactly:

- `/data1/nier/dx100-runs/2026-08-29-xrage-native16-current-r1`
- `/data1/nier/dx100-runs/2026-08-29-xrage-complete-line-control-r2`
- `/data1/nier/dx100-runs/2026-08-29-xrage-complete-line-safe-1536t-2560w-r1`

From `/data1/nier/dx100-runs`,
`sha256sum -c experiments/analysis/xrage_complete_line_artifacts_2026-08-29.sha256`
reported **OK for every one of the 114 ledger entries**.  The recorded binary
SHA-256 is identical in all three roots:
`bb3702ec8fa8e9b328f0efd22da29f756d70679ab3aa69a080dd41e9f2ea4598`.
Each manifest pins source `63795eaa`, runner `d39773c4`, the same guest binary,
the same 64K XRAGE input, the same Ramulator configuration, and empty
`source.diff`/`source_status.txt` artifacts.

## Completion, output, and pair comparability

All checkpoint and restore exit files contain zero.  Each restore log ends in
`m5_exit`; each has a nonempty final stats block.  Independently read verifier
records show exact equality, rather than merely matching work counters:

| Arm | Exact verifier record | ROI `simTicks` |
|---|---|---:|
| native16 | `length=65536 hash=5576400619275092867` | 42,312,279 |
| bounded control | `length=65536 hash=5576400619275092867` | 56,159,086 |
| safe complete-line | `length=65536 hash=5576400619275092867` | 37,268,284 |

Consequently safe is 11.921% lower in ROI ticks than the supplied native16
observation (native/safe = 1.1353x), and control/safe = 1.5069x.  These are
single deterministic observations, not repetitions.

The command lines retain identical CPU, cache, clock, RAM, Ramulator,
input, feeder, partition, response-slot, response-word-pool, issue-width,
and Row/Offset settings.  Native versus control additionally changes exactly
the intended architecture selection: physical 16K versus 4K, native16x3
versus direct4x3 guest arm, transparent mode 0 versus 3, and direct-retirement
handoff off versus on.  Control versus safe changes only combiner tag/word
capacity (16/128 to 1,536/2,560) plus the complete-line fail-closed switch,
apart from output/checkpoint paths.  No unrelated command knob difference was
found.  The checkpoint distinction above remains material to the native
performance claim.

## Independent mechanism closure

The frozen result records close the selected direct-retirement path:

- safe has 65,536 direct-index words, four direct descriptors, 16 page ACKs,
  and 8,192 line ACKs;
- it has exactly 8,192 direct reads, ALUs, and destination writes, each with
  the same number of responses/completions;
- direct fallbacks and early-line overflows are zero; and
- safe reports 8,192 virtual write issues/completions, compared with 17,020
  for control.

The frozen final-stat summaries report control full/partial producer lines as
621/16,399 and safe as 8,192/0.  Their cache/DRAM deltas are consistent with
the reported write reduction: safe has 8,192 rather than 15,167 L3 MAA
misses (6,975 fewer), 239,000,227 rather than 830,212,781 L3 MAA miss-latency
ticks, and 20,764 rather than 27,739 Ramulator reads (6,975 fewer).  These
figures are directionally and arithmetically consistent with the exact-output
closure; they are still single-run simulator evidence.

## Complete-line enforcement audit

`virtual_complete_line_only` is carried from the command-line option through
`MAAConfig.py`, `MAA.py`, `MAA`, and `IndirectAccessUnit` in `63795eaa`.
The constructor rejects zero explicit combiner/response pools and rejects
`virtual_combine_words + virtual_response_word_pool > physical_tile_elements`.
For the selected point, 2,560 + 1,024 = 3,584 <= 4,096 words.

For the relevant unpredicated direct-index virtual-load operation, the source
then panics if:

- a capacity victim is not full;
- final drain retains any partial line; or
- terminal partial-write accounting is nonzero.

This is a real fail-closed restriction rather than a counter-only check.
The nearby strict reference adds the independent capacity invariant:
`StrictTwoPhaseReference.hh:116-119` source-enforces
`resultCapacityWords <= PhysicalElements`; its focused
`strict_two_phase_reference_test` covers `ResultCapacityTooLarge`.

The selected source uses the requested 1,536-tag / 16-way / 2,560-word point.
The payload store is explicitly sized by configured useful words and the
line-slot vector by configured tags; response credit is limited by the
configured response slots and response-word pool.  The source does retain
dynamic C++ containers (notably maps/vectors for reservations, page tracking,
and simulator histories).  In the selected source-reservation path, admission
is logically bounded by response-slot credit before map insertion, but the
host `std::map` representation is not a synthesized fixed structure.  I found
no extra selected-path result payload pool beyond the configured combiner and
response pools, but this source audit is not a whole-chip physical-netlist
proof.

## Storage ledger: what is and is not charged

The selected ledger records 618,387 B for physical SPD plus bounded virtual
payload/control, 889,235 B configured comparable lower-bound storage, and
2,417,152 B native comparable lower-bound storage.  The selected incremental
charge over the 16-tag control is 55,727 B.  It explicitly charges 20,480 B
of combiner payload and 8,192 B of response payload per unit.

The rest is only lower-bound accounting.  The ledger calls tags/masks/word
references, allocator/page/retirement metadata, direct-handoff metadata, and
packed control a bit-count or conservative C++ static view.  In particular:

| Resource | Review conclusion |
|---|---|
| 1,536 tags and 16-way lookup | Capacity/control lower bound only; no timed associative lookup or RAM macro/decoder implementation. |
| 2,560-word combiner and 1,024-word response pool | Semantic payload capacities are charged; port count, bandwidth, and physical implementation are not. |
| Word references/generations | References/control are lower-bound charged; generation checking is explicitly simulator-debug machinery, not a synthesized encoding claim. |
| Reset, page/retirement metadata, arbitration | Bounded source-model state exists and some metadata is counted, but reset/epoch sequencing and arbitration are not fully costed. |
| Ports, wiring, periphery, Fmax | Explicitly excluded; no synthesis, PPA, or Fmax evidence exists. |

## What can be claimed now; required next evidence

It is supportable to claim: on this exact recovered 64K XRAGE gather0 input,
the supplied binary/source configuration completed with an identical exact
output hash; the 4K direct-retirement implementation at the selected bounded
complete-line point had the listed single-run ROI tick observation and
zero partial producer writes; and source guards fail closed for the audited
partial-victim/final-drain conditions.

Before paper or hardware promotion, require (1) a state-attested matched
native/control/safe pre-ROI checkpoint or replay, (2) repeat runs and at least
one more dense backed direct-index application/XRAGE configuration, (3) raw
counter manifests that make the full/partial/cache/Ramulator derivation
machine-checkable without narrative summaries, and (4) RTL or equivalent
implementation evidence covering 16-way lookup, all payload/reference RAM
ports, reset/epoch behavior, full-line drain/ACK arbitration, area, power,
and target Fmax.
