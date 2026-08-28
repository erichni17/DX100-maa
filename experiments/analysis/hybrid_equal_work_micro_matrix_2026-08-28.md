# Hybrid equal-work API micro matrix (2026-08-28)

## Decision

Accept all four arms as one same-binary, same-checkpoint,
equal-semantic-work API micro matrix.  The selected 64-line hybrid is the
fastest arm in this single deterministic observation: 57,330,645 `simTicks`,
9.467% lower than native16, 37.669% lower than native4, and 20.226% lower than
the one-line hybrid.

Reject a stronger reading of the native4 arm.  A true logical4K/API-aperture
arm cannot share this T16K binary: the preserved `r2` attempt faults when the
compile-time T16K MAA address map exceeds the logical4K simulator aperture.
The accepted native4 arm is exactly four 4K operations in the shared T16K
binary under logical16/physical4 geometry.  It is valid semantic chunking
evidence, not true T4096-binary or logical4K-aperture evidence.

This is a microbenchmark result with one observation per arm.  It does not
select 64 lines as a hardware-cost optimum or architecture default, and no
full application was launched.

### Lead integration caveat

The native controls in this original matrix use the one-line direct-index
feeder. The successor in
`hybrid_feeder_matched_native_controls_2026-08-28.md` supplies feeder64 native
controls and rejects the unmatched claim that hybrid64 beats native16. At both
matched depths, native16 is fastest, the hybrid is in the middle, and native4x4
is slowest.

## Frozen evidence

Accepted raw root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-equal-work-micro-20260828-20260828-094827-85a96b10/evidence/hybrid-equal-work-micro-r4`

- Raw runner commit: `2adce87bf13f4fba00d03d29734bdebf69b08be5`.
- Corrected independent classifier/sealer commit:
  `9663f1ff296d45950635784807390eab55da2f2f`.
- Simulator source commit recorded for the frozen executable:
  `6c180e391e738dfd83376bd88d68a2fcaf48b3cc`.
- gem5 SHA-256:
  `2a672ecaef6cd6a273004312d80fdad4446ae880f7b46b41458d0f4e59d37009`.
- Workload SHA-256:
  `78099e9440f375c3c6cba04c31d3a376441730c40b88d769b34c775ddc13e12e`.
- Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
- Shared checkpoint identity:
  `e1858287768fd4926f8288d759de41611e2ac090bee4529ad9822eef0da2cbd7`.
- Exact output hash in every arm: `7228541527853630339`.
- Evidence ledger: 54 paths; ledger SHA-256
  `d6bd4adcf1fdd22cc24884ab9421070125087ef556dfeb1462d6c98056873f82`.
- Sealed result SHA-256:
  `d44609f28a30e46648dca4febfe7ff0b43d47fe08140dbb356c5597ebe01b870`.
- Matrix TSV SHA-256:
  `3f47aaf17cf43dc288f6765e3c46721a0cb39c2760bcc9fa52176c794e159d54`.

The workload initializes source, indices, destinations, and guards before a
single deferred-treatment checkpoint.  Every restore names the same frozen
guest and checkpoint.  The driver compares normalized commands and permits
only the declared geometry, strict-mode, and feeder-depth treatment deltas.
Each foreground process record binds PID, `/proc` start ticks, boot ID,
wrapper return code, end observation, and absence of the original process
identity.

## Independently classified arms

| Arm | Operation contract | `simTicks` | `simInsts` | Index words | Output | Decision |
|---|---|---:|---:|---:|---|---|
| native16 | one native direct-index 16K gather, multiply, store | 63,325,847 | 32,429 | 16,384 | exact hash, guards clean | ACCEPT |
| native4 | four native direct-index 4K gathers, multiplies, stores; logical16/physical4 aperture | 91,978,180 | 32,768 | 16,384 | exact hash, guards clean | ACCEPT WITH APERTURE CAVEAT |
| hybrid1 | one strict logical16/physical4 virtual gather; one feeder line | 71,866,678 | 32,952 | 16,384 | exact hash, guards clean | ACCEPT |
| hybrid64 | one strict logical16/physical4 virtual gather; 64 feeder lines | 57,330,645 | 32,952 | 16,384 | exact hash, guards clean | ACCEPT |

The native instruction counters establish one operation versus four chunks:
native16 has 1 indirect / 1 scalar-ALU / 1 stream-write instruction; native4
has 4 / 4 / 4.  Both ingest exactly 16,384 direct-index words and produce the
same full output hash.  The hybrids each have one producer operation plus four
4K consumer ALU/store actions.

## Strict ordering and work conservation

Both hybrid configurations resolve `virtual_strict_two_phase=true`,
`virtual_masked_writes=true`, logical16/physical4 geometry, fixed 32-slice
RowTable geometry, 16K Offset and Offset-epoch state, one index partition,
and no replay, descriptor spool, global merge, or idealized ACK path.  The
strict retained result capacity is 192 words, below the physical4K bound.

| Counter / trace field | hybrid1 | hybrid64 | Gate |
|---|---:|---:|---|
| configured feeder capacity | 16 words (1 line) | 1,024 words (64 lines) | exact treatment |
| observed index high water | 16 | 896 | positive and within configured capacity |
| B words admitted | 16,384 | 16,384 | exact once |
| B lines / responses | 1,025 / 1,025 | 1,025 / 1,025 | legal one-line unaligned overhead |
| descriptors retained | 16,384 | 16,384 | exact |
| A issues / responses | 9,523 / 9,523 | 9,523 / 9,523 | exact closure |
| ready pages | 4 | 4 | exact |
| semantic backing bytes | 131,072 | 131,072 | exact |
| Offset epoch drains | 0 | 0 | forbidden |

Admission closes before the first A issue in both traces:

- hybrid1: close tick 3,462,676,745; first A issue 3,462,677,058;
- hybrid64: close tick 3,447,823,017; first A issue 3,447,823,330.

Both terminal strict records state `exact_b_once=1`,
`raw_b_retained_bytes=0`, `descriptor_backing_bytes=0`, `replay_passes=0`,
`coherent_ack=1`, `order_ok=1`, and `terminal=1`.

## Masked retirement

| Counter | hybrid1 | hybrid64 |
|---|---:|---:|
| partial masked write issues | 8,640 | 8,698 |
| full-line write issues | 0 | 0 |
| write completions | 8,640 | 8,698 |
| transport bytes | 552,960 | 556,672 |
| semantic backing bytes | 131,072 | 131,072 |

Every issue receives an exact completion and the strict backing issue/ACK
counts agree with the retirement counters.  The 58-write transport difference
is not a semantic-work mismatch: feeder timing changes combiner fragmentation.
The invariant semantic backing payload, exact output, B words, descriptors,
A work, and pages are the conserved work gates.

## Simulated performance

Only first-ROI `simTicks` are used.  Host wall time is excluded.

| Reference -> candidate | Candidate latency change | Reference / candidate |
|---|---:|---:|
| native16 -> native4 | +45.246% | 0.688488x |
| native16 -> hybrid1 | +13.487% | 0.881157x |
| native16 -> hybrid64 | -9.467% | 1.104572x |
| native4 -> hybrid1 | -21.866% | 1.279845x |
| native4 -> hybrid64 | -37.669% | 1.604346x |
| hybrid1 -> hybrid64 | -20.226% | 1.253547x |

The direct feeder effect is visible in the strict B-fetch interval:
16,956,775 ticks at one line versus 2,103,047 ticks at 64 lines.  Other strict
semantic work remains conserved.  These are deterministic single observations,
so no variance or confidence interval is claimed.

## Preserved rejected evidence

The fail-closed predecessors were not overwritten:

1. `hybrid-equal-work-micro-r1`: rejected before ROI.  One memory channel
   cannot instantiate the fixed 32-slice strict RowTable; native16 aborts with
   `unsupported initial Row-Table slice count 32`.
2. `hybrid-equal-work-micro-r2`: rejected as a true logical4K same-binary
   matrix.  Native16 completes, but native4 restores the T16K guest into a
   logical4K aperture and faults at unmapped address `0x80400080`.
3. `hybrid-equal-work-micro-r3`: rejected before hybrid timing.  The legacy
   attribution capacities retain 4,576 result words, exceeding the strict
   physical4K bound.  This motivated pinning the accepted strict 16-line
   combiner and 8-line response capacities.
4. `hybrid-equal-work-micro-r4` initially recorded a classifier-only rejection
   because the first classifier required 1,024 B lines.  All four simulations
   had actually completed.  The source contract permits 1,025 for an unaligned
   deterministic index allocation while still proving exactly 16,384 ordinal
   words once.  The failure record remains in the accepted 54-path ledger;
   commit `9663f1ff` independently reclassifies and seals the same raw runs.

## Handoff boundary

Accepted: exact same-binary/same-checkpoint semantic matrix, exact outputs,
native one-versus-four operation counts, strict full-16K admission ordering,
masked retirement closure, feeder-depth activation, semantic work counters,
and `simTicks` comparisons.

Rejected or pending: a true same-binary logical4K aperture, repetitions,
hardware area/power/Fmax accounting for 64 feeder lines, selection of 64 as a
cost optimum/default, and any full-application performance conclusion.
