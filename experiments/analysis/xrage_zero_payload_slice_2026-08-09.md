# XRAGE strict-4K zero-payload terminal chain

Date: 2026-08-09
Base: `6374f753daf6d0968a4e0cb0a1eda37505ca055f`
Implementation: `e6325d408e76a8267631d85be90b6c83d48fe2a9`
Validated source: `318f9e1f05a4017bbe3eaf713d33758b3330a490`
Read-only precursor audit: `e2dfb15dc5e8` (separate session)

## Verdict

The narrow chain is implemented and correctness evidence passes. Opcode 18
performs `C[i] = A[B[i]] * scalar` for 1--4096 FP64 elements per descriptor.
It reads B directly from coherent memory, retains only finite reorder state,
uses the shared timed MAA ALU, crosses the finite result link, combines finite
C lines, and retires through acknowledged coherent writes. It has no index or
result SPD payload and never falls back to opcodes 13/17 or generic range-pass
virtualization.

The exact matched performance experiment is valid but negative: native16x3
takes 21,443,317 ROI simTicks and zeropayload4x3 takes 51,108,831 simTicks.
Native/fused is 0.419562x; equivalently, the fused arm takes 2.383439x the ticks
(138.344% more). This implementation is correctness-ready, not a performance
promotion.

## Architectural contract

- New opcode `INDIR_LD_VIRTUAL_INDEX_SCALAR = 18` accepts five descriptor
  words naming min, max, index stride, scalar, A, B, C, and one completion-only
  tile. It has no source/result SPD tile and no RF destination.
- Only FP64 multiply is legal. N must be in `[1, 4096]`; offset and epoch
  entries, active row-line slots, live response words, combiner words, and
  outstanding write words must each remain within the explicit strict-4K
  limits. Index lines are bounded to 1--256 and ALU lanes to 1--64.
- Generic range passes must be disabled and the direct-index partition count
  must be exactly one. Invalid shape or capacity panics before execution.
- A and C must be separately registered and their complete registered
  half-open ranges must not overlap. The exact consumed B span
  `[B + min*4, B + (min + (N-1)*stride)*4 + 4)` must not overlap the exact C
  span `[C, C + N*8)`. A/B are both reads and may share or overlap. B/C may use
  a common larger registration only when the consumed spans are disjoint.
  Address arithmetic overflow and out-of-registration B/C spans fail closed.
- Same-MAA hazards cover A:R, B:R, and C:W. The global invalidator holds one
  normalized compound A:R/B:R/C:W lease until retirement, so other MAAs cannot
  mutate A or B or observe/write C inconsistently while the operation is live.
- The descriptor's fourth source register is tracked by RF hazards. The only
  SPD object is a 32-bit completion token. It becomes ready only after all
  source responses drain, the shared ALU and result link are empty, the C-line
  combiner is empty, and every issued C write has returned its ACK.
- Live checkpoint/drain and ROI stats reset fail closed because serialization
  or partial-operation accounting of retained terminal-chain state is not
  supported.

The new retained B-line payload, response payload, ALU batch, result-link
state, and combiner/write state use preallocated fixed-capacity storage. There
is no operation-sized host vector. Existing maps/vectors used as lookup
scaffolding are credit-bounded by the opcode-local validation below.

## Exact semantic-capacity ledger

The ledger is bit-packed semantic storage. It deliberately excludes C++
container nodes, allocator padding, SRAM periphery, ports, and wires. The
completion-only token is charged as completion metadata, not result payload.

Matched zeropayload4x3 configuration: N=4096 per descriptor, offset=4096,
epoch=4096, 1,024 active row-line slots, one B line, eight response slots with
a 64-word global live pool, 16 combiner lines/128 words, eight write credits,
16 ALU lanes, and a one-word/cycle one-bank result link.

| Metadata class | Bytes |
| --- | ---: |
| Operation fields | 64 |
| Offset table | 14,336 |
| Row table | 11,520 |
| Direct-B feeder | 41 |
| A-response reorder | 194 |
| ALU and result link | 33 |
| C-line combiner | 170 |
| Acknowledged writes | 81 |
| Completion state/token | 12 |
| **Metadata total** | **26,451** |

| Internal payload class | Bytes |
| --- | ---: |
| Direct-B feeder | 64 |
| Preallocated A responses | 4,096 |
| ALU lanes | 128 |
| C-line combiner | 1,024 |
| Write buffers | 512 |
| **Internal payload total** | **5,824** |
| **Index/result SPD payload** | **0** |
| **Metadata + internal payload** | **32,275** |

The deliberately tiny backpressure test uses all 4,096 active row slots but
only one response slot/line, one combiner word, one write credit, and a
one-word/cycle link. Its exact charge is 60,613 metadata bytes, 296 internal
payload bytes, and zero SPD payload bytes. The larger metadata total is due to
charging 4,096 row-line slots instead of the matched arm's 1,024.

## N=20,000 payload and control traffic

| Timed payload path | Bytes |
| --- | ---: |
| Coherent B reads | 80,000 |
| Selected coherent A reads | 160,000 |
| ALU-to-combiner result link | 160,000 |
| Acknowledged coherent C writes | 160,000 |
| Index/result SPD payload | 0 |
| **Timed non-SPD payload total** | **560,000** |

Removed relative to native16x3 semantic SPD transfers:

| Removed SPD transfer | Bytes |
| --- | ---: |
| Index tile write + read | 160,000 |
| Gather write + ALU read/write + stream-store read | 640,000 |
| **Removed SPD traffic** | **800,000** |

Timed useful MMIO/control bytes are fully charged. Native uses two 16K chunks,
eight instructions, 192 descriptor bytes, 20 bounds-register bytes, and four
completion-read bytes: 216 bytes total. Zero-payload uses five 4K chunks, five
instructions, 200 descriptor bytes, 44 bounds-register bytes, and ten
completion-read bytes: 254 bytes total.

## Validation

Production build:

- `scons build/X86/gem5.opt -j16` passed from the exact validated tree.
- Final rebuild: 247.19 seconds, peak RSS 10,313,108 KiB.
- gem5 SHA-256:
  `9cb0ce0b36c80da3d0e481f62e1accc3b736d0d921af78b07c4282bd18e6b3ff`.
- Exact 16K native and 4K zero-payload Spatter guest targets both built.

Focused validation:

- 27 source-contract tests pass.
- ASan/UBSan accounting and bounded multi-range tracker tests pass.
- Bash syntax, JSON parsing, `git diff --check`, Python formatting, and gem5
  style checks pass.
- Boundary microbenchmark suite passes 8,240 elements over N={1,15,16,17,
  4095,4096}, six descriptors, positive/negative/signed-zero/bit-sensitive
  scalars, mixed indices, NaN/infinity/signed-zero inputs, guard checks, and
  bitwise output checks.
- Split N=4,097 passes as two legal descriptors. N=4,097 in one descriptor,
  A/C alias, consumed B/C alias, live drain, and live reset each terminate with
  the exact expected panic (exit 134).
- Suite counters are exactly index=ALU=result-link=8,240, SPD reads/writes=0,
  writes=ACKs=8,240. Split counters are exactly 4,097 for each word path,
  SPD reads/writes=0, and writes=ACKs=4,097.

## Matched performance evidence

Both arms use clean source `318f9e1f`, the same exact input SHA-256
`192d007a05e4af8ecc7a44e429a8b8cd5c1060ec52bf31539ff0aa0e364b655e`,
seed 1, `UNIFORM:20000:1:NR`, and result scale 3. They ran concurrently. Both
verified 20,000 outputs with hash `8118948097720528131`.

| Arm | ROI simTicks | MAA instructions | SPD read/write cycles | Direct B / ALU / link words | C writes / ACKs |
| --- | ---: | ---: | ---: | ---: | ---: |
| native16x3 | 21,443,317 | 8 | 1,250 / 2,500 | 0 / 0 / 0 | 0 / 0 |
| zeropayload4x3 | 51,108,831 | 5 | 0 / 0 | 20,000 / 20,000 / 20,000 | 2,500 / 2,500 |

The zero-payload arm also records zero stream-read, stream-write, and separate
scalar-ALU instructions; all five MAA instructions are opcode-18 indirect
operations. Its 317 build rounds, 69,860 fill cycles, and 124,361
all-pages-ready cycles identify the finite 4K reorder/retirement path as the
current performance cost. No speedup claim is supported.

## Evidence

- Correctness controller:
  `/data1/nier/worktrees/codex-coordination/sessions/xrage-zero-payload-slice-20260809-003057-0dff8b0e/artifacts/correctness.controller.log`
- Correctness artifacts:
  `/data1/nier/worktrees/codex-coordination/sessions/xrage-zero-payload-slice-20260809-003057-0dff8b0e/artifacts/correctness/`
- Matched summary:
  `/data1/nier/worktrees/codex-coordination/sessions/xrage-zero-payload-slice-20260809-003057-0dff8b0e/artifacts/matched/summary.tsv`
- Matched artifacts:
  `/data1/nier/worktrees/codex-coordination/sessions/xrage-zero-payload-slice-20260809-003057-0dff8b0e/artifacts/matched/`
- Production build log:
  `/data1/nier/worktrees/codex-coordination/sessions/xrage-zero-payload-slice-20260809-003057-0dff8b0e/artifacts/build/gem5_checkpoint_rebuild.log`

## Limitations

- This is intentionally separate from generic true-4K virtualization and
  rejects range passes or more than one index partition.
- It supports only FP64 multiply and at most 4,096 entries per descriptor.
- Live-state checkpointing and mid-operation stats reset are unsupported and
  rejected.
- The semantic byte ledger is not a synthesized SRAM-area or C++ heap-footprint
  estimate; its exclusions are stated above.
- Correctness and traffic removal are validated, but the matched arm regresses
  performance and should not be promoted as a speedup.
