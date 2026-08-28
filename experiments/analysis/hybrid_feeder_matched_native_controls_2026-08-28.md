# Feeder-matched native controls for the equal-work API micro (2026-08-28)

## Decision

Accept a read-only six-arm successor to the accepted equal-work r4 matrix.
Exactly two new API micro restores were launched: `native16_f64` and
`native4_f64`. Both use `virtual_index_buffer_lines=64`. No accepted arm was
rerun and no full workload was launched.

The feeder knob is applicable and active for native direct-index. The frozen
simulator classifies native `INDIR_LD_INDEX` as a direct-index load, uses the
configured feeder depth as the pending-plus-ready line capacity, and records
line and word high-water marks for the path. The new runs resolve 64 lines and
increase native high water materially while preserving exact output and work:

- native16: 1 to 64 live lines and 16 to 864 buffered words;
- native4x4: a summed 4 to 256 live lines and 64 to 3,056 buffered words.

Feeder matching changes the performance conclusion. Hybrid64 remains faster
than feeder-matched native4x4, but it is slower than feeder-matched native16.
Native16_f64 is the fastest arm in this one-observation micro matrix.

## Frozen authority and launch scope

Accepted predecessor root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-equal-work-micro-20260828-20260828-094827-85a96b10/evidence/hybrid-equal-work-micro-r4`

Read-only successor root:

`/data1/nier/worktrees/codex-coordination/sessions/hybrid-feeder-matched-native-controls-20260828-20260828-105718-247e11b9/evidence/hybrid-feeder-matched-native-controls-r1`

- Runner commit: `e46a5e571bedaabbd7d23d5b4e9122077d23417d`.
- Simulator source commit: `6c180e391e738dfd83376bd88d68a2fcaf48b3cc`.
- Frozen `IndirectAccess.cc` blob: `70c18986046234d706094dae7a09f1d369b8d3b1`.
- gem5 SHA-256:
  `2a672ecaef6cd6a273004312d80fdad4446ae880f7b46b41458d0f4e59d37009`.
- Workload SHA-256:
  `78099e9440f375c3c6cba04c31d3a376441730c40b88d769b34c775ddc13e12e`.
- Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
- Shared checkpoint identity:
  `e1858287768fd4926f8288d759de41611e2ac090bee4529ad9822eef0da2cbd7`.
- Predecessor result SHA-256:
  `d44609f28a30e46648dca4febfe7ff0b43d47fe08140dbb356c5597ebe01b870`.
- Predecessor artifact-ledger SHA-256:
  `d6bd4adcf1fdd22cc24884ab9421070125087ef556dfeb1462d6c98056873f82`.

Before launch, the predecessor ledger and independent classifier passed, the
checkpoint had no live restore owner, the committed runtime Python files
matched the frozen hashes, and the runner worktree was clean. The two commands
were derived from the accepted native16 and native4 commands. After removing
only output path and feeder depth, each command is byte-for-byte equivalent to
its predecessor command.

The checkpointed guest retains the predecessor selector's absolute path. Each
new restore used a private bubblewrap mount namespace: `/` and all predecessor
evidence were read-only, the successor output root was the only writable bind,
and the arm-local treatment was read-only-bound over the frozen selector path.
The predecessor selector SHA-256 was verified unchanged after both restores.

## Terminal and correctness evidence

Both new foreground process records bind PID, `/proc` start ticks, boot ID,
the complete wrapped-command hash, return code zero, end observation, and
absence of the original process identity. Each restore has exactly one
deferred-treatment record, one exact result, one ROI close, and one
`m5_exit`. Neither log contains panic, fatal, assertion, abort, segmentation,
or error text.

Every arm produces output hash `7228541527853630339` with clean guards.
Native work is conserved exactly:

| Arm | `simTicks` | `simInsts` | Indirect / scalar / stream-write ops | Index words | Output |
|---|---:|---:|---:|---:|---|
| native16_f1 | 63,325,847 | 32,429 | 1 / 1 / 1 | 16,384 | exact |
| native16_f64 | 48,487,143 | 32,429 | 1 / 1 / 1 | 16,384 | exact |
| native4_f1 | 91,978,180 | 32,768 | 4 / 4 / 4 | 16,384 | exact |
| native4_f64 | 77,011,459 | 32,768 | 4 / 4 / 4 | 16,384 | exact |
| hybrid1 | 71,866,678 | 32,952 | 1 / 4 / 4 | 16,384 | exact |
| hybrid64 | 57,330,645 | 32,952 | 1 / 4 / 4 | 16,384 | exact |

Both new `config.ini` files resolve `num_tile_elements=16384`,
`virtual_index_buffer_lines=64`, and `virtual_strict_two_phase=false`.
Native16_f64 resolves `physical_tile_elements=16384`; native4_f64 resolves
`physical_tile_elements=4096`. The classifier also checks all other frozen
RowTable, Offset, response, combiner, masked-write, partition, replay, spool,
merge, issue-order, and ACK settings exactly.

## Native feeder activation

| Native path | f1 line HWM | f64 line HWM | f1 word HWM | f64 word HWM | Capacity gate |
|---|---:|---:|---:|---:|---|
| native16, one operation | 1 | 64 | 16 | 864 | at most 64 lines / 1,024 words |
| native4x4, four operations summed | 4 | 256 | 64 | 3,056 | at most 256 lines / 4,096 words |

This is positive mechanism evidence, not merely a parsed option. The frozen
source constructor accepts and stores `virtual_index_buffer_lines`; native
`INDIR_LD_INDEX` satisfies `isDirectIndexLoad()`; `fillDirectIndexWindow()`
fills while pending plus ready lines are below the configured capacity; and
instruction completion exports the measured high-water counters. The exact
source blob is unchanged in the current tree.

## Fair simulated comparisons

Only first-ROI `simTicks` are used. Host time is excluded. Each row compares
matched feeder depths or isolates the feeder within one native path.

| Candidate vs reference | Candidate latency change | Reference / candidate | Decision |
|---|---:|---:|---|
| hybrid1 vs native4_f1 | -21.866% | 1.279845x | hybrid1 faster |
| hybrid64 vs native4_f64 | -25.556% | 1.343286x | hybrid64 faster |
| hybrid64 vs native16_f64 | +18.239% | 0.845746x | hybrid64 slower; native16_f64 is 1.182389x faster |
| native16_f64 vs native16_f1 | -23.432% | 1.306034x | feeder64 faster |
| native4_f64 vs native4_f1 | -16.272% | 1.194344x | feeder64 faster |

The successor intentionally does not promote unmatched hybrid64/native4_f1 or
hybrid64/native16_f1 comparisons as fair feeder-controlled conclusions. The
accepted r4 result and its original comparisons remain preserved unchanged.

## Read-only successor seal

The successor joins the four predecessor classifications by exact result hash
with the two independently classified new arms. All successor files and
directories have their write bits removed.

- Successor result SHA-256:
  `458bdbc0b6546dab353f07cc5b9588f7caec06c2bdee6ba6a0059392550eec95`.
- Successor artifact-ledger SHA-256:
  `26361a0457f07684542cc993449d8dd26a4881c2fda6692f9b0e6808fe891ae2`.
- Successor matrix SHA-256:
  `208e638158fab6b440a02f6d85c60969fd1071283fefd80561ecc1beb65ec748`.
- Successor gate SHA-256:
  `da432fb4afc0b01929daf33462e6c8ae0f0876bff1b7ef74d61059f0e99eea40`.

The independent read-only validator rehashes the complete successor artifact
set, revalidates r4 and its checkpoint tree, reconstructs both commands and
selector overlays, reclassifies both new arms from raw logs/config/stats/traces,
rechecks source applicability and activation bounds, recomputes the five fair
comparisons, and requires exact equality with the sealed result.

## Limitations

This remains one deterministic observation per arm in an API microbenchmark.
It is not variability evidence, a full-application result, or hardware
area/power/Fmax accounting for 64 feeder lines. Native4 is still four exact 4K
operations in the shared T16K logical aperture; it is not a true T4096 binary
or logical4K/API-aperture run. The result therefore corrects feeder fairness
within the accepted micro contract without selecting a default or claiming a
suite-wide architecture win.
