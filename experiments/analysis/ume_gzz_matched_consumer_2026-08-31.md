# UME GZZ matched-consumer controls (2026-08-31)

## Decision

Accept the fresh three-arm fixed-input matrix as exact-output, mechanism-valid
GZZ evidence. Native16 is fastest. The strict logical16/physical4 arm is
43.7734% slower than native16 and 10.7700% slower than native4x4 in this single
deterministic observation. No historical performance control is used.

The accepted evidence root is:

`/data1/nier/worktrees/codex-coordination/sessions/`
`gzz-matched-consumer-controls-20260831-20260831-175325-36f08396/`
`evidence/ume-gzz-matched-consumer-r4`

Its decision is `ACCEPT_FRESH_GZZ_MATCHED_CONSUMER`, campaign exit is zero,
and the independent read-only validator passes.

## Matched experiment contract

One opt-in `gradzatz` guest is compiled once. All three selectors are resolved
before their selector-specific checkpoints, preserving the existing GZZ
pre-checkpoint treatment contract. The three fresh restores share exactly one
gem5 binary and one guest binary:

- gem5 SHA-256:
  `d1f8a3d5a736ef645849efee6323f1a6aa8cdd392bdff8b9aeb4d0d4adc6db47`
- guest SHA-256:
  `906ad4273e547df8ae0f566b082259c2eca37fbe2e1a2a649d182e8c05df6c38`
- fixed input: 16,384 corners
- exact output hash: `7602200327591349891`
- scalar reference: zero volume errors, zero gradient errors, and zero
  nonfinite values across 196,384 outputs in every arm

Native controls retain ordinary DX100 SPD-indexed indirect gathers. A separate
native-only ROI applies MAA vector DIV and MUL after each natural arithmetic
page. Native16 uses one 16K page; native4x4 uses four 4K pages. The strict arm
keeps the accepted shared-payload production control flow and consumes four 4K
pages. No strict shared-payload source file changed from base `e69f432b`.

## Results and mechanism counters

| Arm | `simTicks` | Native/virtual index words | INDRD | INDRMW | ALUS | ALUV | Result writes | Strict ops |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| native16 | 20,823,577 | 0 | 2 | 2 | 2 | 2 | 0 | 0 |
| native4x4 | 27,027,863 | 0 | 8 | 8 | 8 | 8 | 0 | 0 |
| strict logical16/physical4 | 29,938,763 | 16,384 | 5 | 8 | 8 | 8 | 1,024 | 1 |

The ALUV counts prove one DIV and one MUL per arithmetic page: 2 for native16
and 8 for both four-page arms. Zero `IND_VirtIndexWords` and zero virtual
backing writes distinguish the ordinary native gather mechanism. The strict
arm records 16,384 direct-index words, 16,384 strict descriptors, four pages
ready, 1,024 full-line writes and ACKs, zero partial writes, and exact 16,384
scheduled/read payload words.

Strict admission closes with 16,384 B words and descriptors, zero buffered raw
B words, and zero A issues. Its first A issue follows the last row/offset
insert; A responses and backing ACKs close exactly; `order_ok=1` and
`terminal=1`.

The simulated comparisons are:

- native16 is 1.437734x as fast as strict;
- native4x4 is 1.107700x as fast as strict;
- native16 is 1.297945x as fast as native4x4.

## Fail-closed execution and rejected attempts

The runner freezes and rehashes simulator, Ramulator, config, guest, selectors,
and checkpoint trees. It requires process start identity, return code zero,
PID identity disappearance, exactly one terminal `m5_exit`, exact output,
predicted work/mechanism counters, strict timing invariants, and an immutable
artifact ledger. Each restore also has a 30-minute wall bound and a trace guard
that terminates a process if its trace grows for 60 seconds without simulated
tick progress.

- r1 rejected an interleaved runtime native/strict loop after detecting a real
  zero-time `request_complete`/`build_capacity` livelock. Its artifacts remain
  preserved; native execution was then isolated from the original strict
  control flow.
- r2 rejected before checkpoint because the mutable shared build-tree gem5
  changed identity. Later attempts use r1's frozen simulator inputs.
- r3 completed all restores and independently passes the final classifier, but
  its original seal rejected an incorrect expectation that native SPD-indexed
  gathers increment virtual direct-index words.
- r4 is a clean rerun from the committed corrected classifier and owns the
  accepted result, gate, and ledger.

## Provenance and seal

- implementation commits: `6fef6a6a`, `86785256`, `39e906b9`
- result SHA-256:
  `dbcb0da8cdd6e9c90c940796719734984922674dd26e3afb4c4db9c329568235`
- artifact ledger SHA-256:
  `720564969e102375a83547cc3186ab102e83e1784e54922d5758b6d5f52366c5`
- manifest SHA-256:
  `2c677de56d60fce80ac3ae014439d6561910fd598f7c74027fc4ef626deecfdf`
- gate SHA-256:
  `16e2aa71001292581c04e8b5786a81c531a556f1b7b03622e385b6dc06d3bff1`

This is one deterministic 16K-window GZZ observation, not variability, a full
application/suite result, or an area/power/Fmax claim. It does not cover GZP's
masked published-source SoA/JIT RMW path.
