# SoA/JIT context64/96/128 exact GZP scaling (2026-08-23)

## Decision

Accept the functional and accounting support for 96 and 128 active SoA/JIT
contexts as default-off sensitivity options.  Context128 is the measured
winner in this one exact full-GZP matrix, but it remains default-off and is not
a new frequency-qualified hardware promotion.  Its 1.0776% lower ticks versus
context64 are not free hardware: the provisioned context pool grows to four
32-line regions and carries the full byte charges below.

The accepted workload geometry remains logical16K Row/Offset with a physical
4K-element tile payload.  The result-context and lookahead arrays are separate
auxiliary state and must not be described as part of that physical tile
payload or as zero-cost virtualization.

## Repeated exact full-GZP matrix

Both replicas used masked-index mode, logical tile 16,384, physical tile
4,096, owner64, pre-A enabled, the same clean source commit, binary, guest,
checkpoint, selector, and Ramulator library, and no timeout.  Only active
SoA/JIT contexts changed.

| Contexts | ROI simTicks (r1/r2) | Lower ticks vs c64 | Context stalls (r1/r2) | Stall reduction vs c64 |
|---:|---:|---:|---:|---:|
| 64 | 6,634,051,589 / 6,634,051,589 | control | 2,822,582 / 2,822,582 | control |
| 96 | 6,582,035,997 / 6,582,035,997 | 0.7841% | 2,700,820 / 2,700,820 | 4.3139% |
| 128 | 6,562,562,389 / 6,562,562,389 | 1.0776% | 2,686,803 / 2,686,803 | 4.8105% |

Context128 is 0.2959% lower ticks than context96.  The replicas are identical
for every recorded row field.  Every run has wrapper exit 0, exactly one gem5
m5-exit marker, a complete first statistics window, output hash
`11225737641199706160`, 949,411 selected and 50,013 rejected elements,
509,830 A reads and writes with matching responses, balanced value
issue/response/fill ledgers, balanced pre-A issue/use ledgers, and all 61
terminal windows.  The masked-index exact-equivalence and native-reference
checks pass with no illegal/sentinel classification, reference, or nonfinite
errors.

## Exact hardware-byte accounting versus context32

The fixed maximum pool is charged even when a smaller active prefix is
selected.

| Fixed/incremental state | Bytes |
|---|---:|
| 128 result A-line payloads | 8,192 |
| 128 contexts x eight 8-byte lookahead values | 8,192 |
| Result/lookahead payload increment vs context32 | 12,288 |
| Context metadata increment vs context32 | 27,648 |
| Value-coalescer waiter-mask increment (256 to 1,024 waiter identities across 128 owner lines) | 12,288 |
| Total non-payload increment vs context32 | 39,936 |
| **Total modeled state increment vs context32** | **52,224** |
| Maximum transient WriteReq payload copies | 8,192 |
| Transient write-copy upper-bound increment vs context32 | 6,144 |

The fixed context array is 53,248 bytes and the fixed waiter masks are 16,384
bytes, for 69,632 bytes of modeled context-plus-waiter state.  The context32
reference is 13,312 context bytes plus 4,096 waiter-mask bytes.  Transient
WriteReq data are an upper bound on transport copies, not persistent context
storage.

The 128-context admission/response search and 1,024-waiter ownership search
are not banked, synthesized, or timing-validated at 3.2 GHz.  The matrix
therefore supports a default-off workload sensitivity point only; it does not
establish a general application win or a cycle-time-feasible implementation.

## Provenance

- Local source commit: `e6a17edd1d78b202372c2e459304d0840f7f9470`
- gem5 SHA-256: `f74513c51cf231eb8d17993858432490cf966ef5c0509ab86f19713e7001c621`
- Raw root: `/data1/nier/worktrees/codex-coordination/sessions/gzp-context-scaling-20260823-112716-5a1da36d/evidence/gzp-context64-96-128-e6a17edd-r1`
- Manifest SHA-256: `8f3b2339e603938aba511890c4db64a8d57a6f282e6be6782fae0c6a71837f2b`
- Results JSON SHA-256: `228d67749abc1c4d03a20fcc15c6d1ca694f227b0ce00568794ed3335e898cb0`
- Results TSV SHA-256: `db56a4a7ae5a4c592351b981c0d6fcbc58ebc73e886c60cff5429eb7f2fb214b`
- Checkpoint tree SHA-256: `1d617cd45b1835a3a11f39ae6b002efd12e33c2ed11cb6a98a3814f1caa4999e`
- Guest SHA-256: `00980813e3bbcd74aec84d4352c545f5ff956485cac99c456fadfddfcab8ecda`
- Selector SHA-256: `32ebe0418fb690b057b08babaf5d1e7b05e65705f2c6ec776576cd810e86190a`
- Ramulator library SHA-256: `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`

The durable user service was
`dx100-gzp-context64-96-128-e6a17edd-r1.service`.  It started at 12:21 EDT,
completed at 13:05 EDT, emitted `GZP_RESULT_CONTEXT_GATE_PASS`, and consumed
2 h 10 min 47 s aggregate CPU time.  The runner recorded an empty source
status and `timeout_seconds=0`.
