# XRAGE 4K-physical metadata validation

## Outcome

XRAGE was the shortest honest validation path. Its exact gather verifier and
attested pre-MAA checkpoint are compatible with both metadata configurations.
The available NAS CG handoff is frozen, but its control and virtual arms use
different guest binaries/checkpoints, so it is not a matched metadata-only
comparison without rebuilding a baseline.

The matched XRAGE pair passed exact correctness. Both arms produced
`MAA_GATHER_VERIFY_PASS length=2097152 hash=11014995430510232451`, returned
zero, reached `m5_exit`, and produced two statistics blocks.

| arm | physical tile | metadata geometry | schedule | ROI simTicks | delta |
| --- | ---: | --- | --- | ---: | ---: |
| full metadata | 4K | rows 16x64, offsets/epoch 16384 | one pass | 1,284,958,900 | baseline |
| bounded metadata | 4K | rows 16x32, offsets/epoch 4096 | four modulo passes, finite 16-word/cycle filter | 1,456,213,094 | +13.327601% |

This is a **4K-physical full-metadata versus 4K-physical bounded-metadata**
comparison for a 16K logical tile. It is not a native-16K result.

Mechanism counters support the treatment distinction. The full arm issued one
index partition with no filter work or metadata-full events. The bounded arm
issued four partitions, processed 8,388,799 filter words in 524,479 cycles,
and recorded 191 offset-table-full events/epoch drains. Virtual write issues
equal completions in both arms (320,375/320,375 and 263,974/263,974).

The stage counters nearly close the timing delta:

| stage | full metadata | bounded metadata | bounded minus full |
| --- | ---: | ---: | ---: |
| Fill cycles | 1,613,731 | 1,745,409 | +131,678 |
| Request cycles | 2,420,806 | 2,836,185 | +415,379 |
| Fill + Request | 4,034,537 | 4,581,594 | **+547,057** |

At the matched 3.2 GHz clock, the 171,254,194-tick ROI difference is
548,013.421 cycles. The measured Fill and Request deltas therefore account for
99.825% of it, leaving 956.421 cycles elsewhere. The bounded arm also performs
four times as many index-line reads (524,288 versus 131,072), 524,479 charged
filter cycles of which 366,008 are non-overlapped waits, 191 Offset epoch
drains, and 6,590 rather than 4,480 RowTable build rounds. These counters
localize the loss to bounded metadata execution; they do not isolate a single
causal knob because the treatment jointly changes Row/Offset capacities,
partition count, index traffic, and filtering.

The bounded arm nevertheless records fewer DRAM commands: 486,560 reads,
87,457 activates, and 79,797 precharges, versus 500,298, 111,692, and 104,120
for full metadata. The slowdown therefore cannot be attributed to a larger
DRAM-command count or assumed loss of row locality. In this implementation,
the extra bounded-control work outweighs the lower DRAM traffic.

## Provenance

Raw evidence is outside Git at:

`/data1/nier/worktrees/codex-coordination/sessions/repr-virtual-workload-20260808-20260808-093807-f737e61d/xrage-pair-41fc3f1`

The evidence checksum manifest verifies in full. The simulator was built from
clean commit `41fc3f11c54713535edbeab5ebcedbf78e079c4d`; its copied ELF SHA-256 is
`cf8690d6811d6b9f9b8cb1c04a0967d65e5c3c92113c5bfdea86657620929da4`.
The frozen simulator provenance keeps the successful `scons
build/X86/gem5.opt -j4` command, zero-exit log, and empty source-status record.
The build used a prior build directory only as a compile cache. Failed
dependency-population/link attempts are retained beside the build record and
were not promoted.

The runner source commit is recorded separately from the simulator source
commit even though both are `41fc3f11...` in this run. The frozen Ramulator
provenance JSON sits beside `libramulator.so`; its
`frozen_library.sha256` exactly matches the copied ELF SHA-256
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`,
and both record ELF build ID `11302ea5a975fd6a8737915c11e06bd5ccede7f4`.

The same treatment-neutral pre-MAA checkpoint was used for both arms. Its
pre-treatment file-list identity is
`94a8ead604eafff7264486fb21f5f649e5c2c11d40fa91f052e9b26c640f62c9`.
No core MAA, SPD, or hybrid source was changed for this validation.
