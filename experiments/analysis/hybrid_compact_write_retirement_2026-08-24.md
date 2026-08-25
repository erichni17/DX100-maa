# Compact SoA/JIT write retirement: paused handoff (2026-08-24)

## Status

Paused cleanly for a professor meeting.  Do not classify performance or
promote the mechanism yet: no certificate-bound SSSP/HashJoin A/B reached its
terminal gate.  The mechanism remains explicit default-off and active SoA/JIT
contexts remain eight.

The current successor is `0d88fb41ec6b8fcb8e5c1640809616ee8cab3663`.
It supersedes, and must not be replaced by cherry-picking, the failed first
implementation `f35f9111ae49d8f6d48ebb715ccaf8ec3f5f3835`.

## Implemented contract

- Fixed eight-credit reserve, packet-queue transfer, and exact WriteResp
  acknowledgement.  A completed 416-byte context is cleared only after queue
  ownership transfer; full credits retain/retry it and count stalls.
- Request and Response perform two observational terminal checks.  Value
  generation clearing and tracker `finish()` occur once, after Response stats
  and trace publication.
- Compact outstanding writes are observed only as total/HWM/ticks.  They have
  no source-context region attribution and do not enter dual-region overlap.
- Installed tracker fields are 1,168 bits = 146 bytes per indirect unit, or
  584 bytes across four units.  Activity is derived from nonzero generation;
  there is no separate active bit.  Validation counters are instrumentation,
  not hardware.
- Hardware relies on reliable exactly-once responses.  A three-bit transient
  credit tag indexes persistent identity state and is not reused before exact
  acknowledgement: maximum tags are 24 bits = 3 bytes per unit.  Copied
  WriteReq payload is separately transient, at most eight x 64 = 512 bytes per
  unit.  Sender-state generation/sequence/address fields mirror charged
  tracker validation metadata, not another hardware allocation.

## Clean-build certificate

Certificate root:
`/data1/nier/worktrees/codex-coordination/sessions/hybrid-compact-write-retirement-20260824-071509-d5b4789c/certificates/compact-write-retirement-0d88fb41-r1`

- Source commit/tree/archive: `0d88fb41ec6b...` /
  `19a0ef7fc811...` / `63260489f4f8...`.
- Forced-link command: `scons --ignore-style build/X86/gem5.opt -j8`.
- Build log SHA-256: `e75c7b1db4fe78224e7819a4515d27ad32b26b6e6dc9cabfb1dbbc98914e0f10`.
- Changed-source ledger: 19 files, SHA-256
  `1dbc2003459676e4de9151c07360ccf68d27baa8d8678c7ed3f7781e719bf5f2`.
- MAA-object ledger: 16 objects, SHA-256
  `bb11014c4c6901cb93377e119e78075d043424e83c534123a0d940e9157e65aa`.
- gem5 SHA-256/mtime: `9fd99209470e51a8ee9e994b598969df4bf3480bdde8130ffd9bb14413a1c819`,
  2026-08-24 09:33:10 EDT.
- Certificate bundle SHA-256:
  `882d8dfc7f9938c494cc40ebb6f0d892a286afde6b954a0589a210d2fbf79002`.
- Frozen Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.

The runner refuses a dirty worktree and revalidates the certificate bundle,
HEAD/tree/archive, changed-source ledger, binary hash/mtime, and dependency
hashes before copying the certificate into an evidence root.

## Attempt ledger

Every root contains `ATTEMPT_STATUS.txt`.

| Root | Result | Admissible |
| --- | --- | --- |
| `compact-write-retirement-f35f9111-r1` | detached process reaped; empty stats and no rc | no |
| `compact-write-retirement-f35f9111-r2` | compact SSSP rc 134 at tick 4,923,936,338 due consumed first terminal check; baseline stopped | no |
| `compact-write-retirement-32017c35-r1` | stopped when review required active-bit removal/certificate | no |
| `compact-write-retirement-0d88fb41-r1` | certificate passed; stopped in SSSP ROI for requested pause | no |

No HashJoin arm launched in these roots.  No native arm, full application root,
or wall timeout was used.

## Validation at pause

- Compact tracker C++ test: pass.
- Result-pipeline C++ test: pass.
- Value/coalescer lifecycle C++ test: pass.
- Python contract functions: hybrid 19, result pipeline 4, A/B runner 4,
  certificate 3, context64 rejection 3; all pass.
- Normal gem5 build and forced certified relink: pass.
- Git source worktree: clean after the report checkpoint is expected.

## Exact restart

Use a fresh root; never resume or overwrite an attempted root.  From a clean
`0d88fb41...` worktree, first verify:

```bash
git status --porcelain --untracked-files=all
(cd /data1/nier/worktrees/codex-coordination/sessions/hybrid-compact-write-retirement-20260824-071509-d5b4789c/certificates/compact-write-retirement-0d88fb41-r1 && sha256sum -c certificate.sha256)
sha256sum build/X86/gem5.opt
```

The expected binary hash is `9fd99209470e...`.  Then launch exactly:

```bash
systemd-run --user --unit=dx100-hybrid-compact-write-retirement-0d88fb41-r2 --collect --working-directory=/data1/nier/worktrees/codex-sessions/hybrid-compact-write-retirement-20260824-071509-d5b4789c/DX100-virtualization-line-handoff-20260812 /usr/bin/bash experiments/scripts/run_hybrid_compact_write_retirement_ab.sh build/X86/gem5.opt /data1/nier/worktrees/codex-coordination/sessions/hybrid-compact-write-retirement-20260824-071509-d5b4789c/certificates/compact-write-retirement-0d88fb41-r1 /data1/nier/worktrees/codex-coordination/sessions/hybrid-compact-write-retirement-20260824-071509-d5b4789c/evidence/compact-write-retirement-0d88fb41-r2
```

Accept only if both kernels are correct/accounting-closed and nonregressing,
with at least one improving first `simTicks` by the predeclared 0.5% threshold.
Otherwise retain default-off and commit an explicit rejection.

## Terminal recovery

The fresh `r2` simulations completed, but the wrapper exited 127 because `rg`
was unavailable in user-systemd after all four gem5 arms. Direct frozen-artifact
recovery establishes exact correctness and rejects the mechanism: SSSP ties at
`9,976,182,331` ticks, while HashJoin PRO improves only 0.012326154%, below the
0.5% threshold. See
`experiments/analysis/hybrid_compact_write_retirement_recovery_2026-08-24.md`.
Do not restart the A/B or promote `0d88fb41`.
