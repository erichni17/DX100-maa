# CPU physical-SPD prefetch-boundary gate

## Contract

Logical-16K/physical-4K reserves a 16K-element CPU address aperture per SPD
tile, but only elements `[0, 4096)` have physical host-SPD storage.  Addresses
at and beyond element 4096 are permanently invalid host-SPD addresses: virtual
pages are materialized in coherent backing memory and never make the padding in
this cacheable SPD aperture architectural.

The CPU-side policy therefore drops only the observed downstream hardware
prefetch form: `ReadSharedReq` with
`taskId == context_switch_task_id::Prefetcher` and a byte-enable vector whose
length equals the packet size and whose every bit is enabled.  Reserved or
completion-owned lanes are rejected before this classification.  Valid
physical addresses keep the existing readiness retry path.  Every other
physical-boundary packet, including demands, writes, exclusive reads,
software-prefetch forms, partial masks, and malformed mask lengths, panics
before SPD or invalidator mutation.

The drop uses `makeTimingResponse()` followed by `setBadAddress()`.  gem5's
`BaseCache::recvTimingResp` calls `handleFill` only under
`is_fill && !is_error`, so this response cannot install zero or padding data.
Intermediate cache targets propagate the error, while the originating
`FromPrefetcher` target is deleted.  A demand coalesced behind such a request
also fails closed; an unmerged later demand reaches the MAA and panics.

## Provenance proof

`Queued::DeferredPacket::createPkt` creates the physical prefetch Request with
flags `0`, assigns `taskId(Prefetcher)`, and emits `HardPFReq`.
`Cache::createMissPacket` selects `ReadSharedReq` for the non-writable miss and
reuses `cpu_pkt->req`.  Request copying also preserves `_taskId`, while an
ordinary Request defaults to `context_switch_task_id::Unknown`.  The source
integration test fixes this chain and rejects a broader packet/request-bit OR
policy.

## Executable gates

- `tests/maa/run_cpu_spd_aperture_unit.sh` builds optimized and ASan/UBSan
  variants of the pure aperture test.  It covers the valid last physical line,
  speculative and architectural element 4096, physical/logical crossing, and
  invalid geometry.
- `experiments/tests/test_cpu_spd_aperture_contract.py` checks integration
  ordering, exact provenance, non-installing error semantics, stats/trace, the
  ready-page producer, and both live arms.
- `experiments/scripts/run_cpu_spd_prefetch_boundary_smoke.sh` creates separate
  positive and negative checkpoints.  Each guest first performs a bounded
  4096-word STREAM load and `wait_ready`, then resets statistics and scans the
  full physical page with the L1 StridePrefetcher enabled.  The positive arm
  requires exact sum/last/output, nonzero task-tagged `ReadSharedReq` drops,
  zero architectural rejections, and normal exit.  The negative arm then loads
  element 4096 and requires the aperture panic before any value or normal exit.

## Build dependency ledger

The isolated worktree contained empty Ramulator gitlink children.  The build
read the matching dependency trees and library from `/data1/nier/DX100`
without modifying that worktree.  Directory ledger construction excluded
`.git` metadata and used sorted `sha256sum` records:

| Artifact | SHA-256 |
|---|---|
| spdlog directory ledger | `cdee3e06be297278b8efc03637c9f3da0bfaf55dc75ff7dfdcb788cb4f2a7eeb` |
| yaml-cpp directory ledger | `225552afd0d4e6b6472161be1390443e106219a9ee1dff689fa3007a0b1a3219` |
| `libramulator.so` | `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753` |
| exact successor `build/X86/gem5.opt` | `703c1e1d756ada75306e7ed941f3dad967370cd4f224c092430b5b2b5fb0f1a5` |

The build used only extra include/link search flags for those read-only
dependencies.  No dependency trees or build products are tracked.

## Evidence policy

Dirty evidence root `evidence/cpu-spd-prefetch-boundary-r1` is diagnostic only.
Clean root `evidence/cpu-spd-prefetch-boundary-r2` at commit `fb842f75` closed
the provenance, non-installation, ready-page, and negative-demand contracts,
but final review found that its drop predicate did not bind the full
byte-enable mask.  Both r1 and r2 are superseded near-final evidence and are
not integration evidence for the successor.

After this report and all implementation/test paths are committed, the same
exact binary must run with `ALLOW_DIRTY=0` into the fresh coordination root
`evidence/cpu-spd-prefetch-boundary-r3`.  Acceptance requires its manifest
`source_commit` to equal the final repository commit, all runner-registered
source paths to match `HEAD`, the positive and negative contracts above to
pass, and the binary/library/dependency hashes to match this ledger.  No full
S22 launch is authorized by this gate.

## Terminal successor and integration

Clean `r3` passed at source commit `b3c27693` with binary
`703c1e1d...f0f1a5`: exact positive output, 128 task-tagged shared-read drops,
zero architectural rejections, and a separate element-4,096 demand that exited
134 before returning a value. All `artifacts.sha256` entries verify.

The source was cherry-picked into the lead as `5a20fe18` and `aa655e3b`; the
changed source-file hashes are identical to the tested worker commit. The
binary is archived at
`/data1/nier/dx100-binaries/gem5-703c1e1d756ada75306e7ed941f3dad967370cd4f224c092430b5b2b5fb0f1a5.opt`.

The matched full-cache small SSSP gate at lead commit `7bfb5c63` passes exact
output and four routed logical windows at `7,821,692,529 simTicks`, with 17,866
old-result writes/responses, zero aperture rejections, and zero boundary drops.
Zero drops are expected because this graph's four complete logical windows do
not execute the host-SPD tail scan; the dedicated `r3` aperture smoke provides
the boundary coverage.

Full S22 candidate root
`/data1/nier/dx100-runs/2026-08-24-sssp-aperture-full-s22-r1` is active with
the original pre-tail-replay guest, the archived aperture binary, full cache
configuration, `aperture_candidate_gate=true`, no native arm, and no wall
timeout.
