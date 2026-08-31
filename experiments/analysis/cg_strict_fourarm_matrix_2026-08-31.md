# CG strict four-arm matrix (2026-08-31)

## Contract

This report records the fresh `CG_NA=256` same-binary matrix.  The guest is
compiled once from the final CG source lineage and one checkpoint is captured
before the deferred selector is read.  Native16, equal-work native4x4,
original legacy 16K-logical/4K-physical hybrid, and strict page-fed two-pass
restores all use that guest, input, checkpoint, gem5, Ramulator, and matched
feeder depth.  Historical native4 evidence is not reused because its
provenance is mismatched.

The selector is supplied through a per-restore read-only mount namespace at
the fixed path captured by the checkpoint; restores therefore run in parallel
without mutating a shared selector. The treatment delta is explicit in the
machine-readable manifest. The strict arm is judged on the partial-
mask P-retirement path: 32-byte/cycle payload staging, one active line,
32 payload banks, 16 bounded combiner slots, 8 response slots, exact
scheduled/read-word closure, and `B-close-before-A` timing.  This is not a
complete-line direct-gather claim.

## Result

The fresh terminal root is
`/data1/nier/worktrees/codex-coordination/sessions/`
`cg-strict-fourarm-matrix-20260831-20260831-104028-a26c56c4/evidence/`
`cg-strict-fourarm-na256-r5`.  Its `matrix.complete`, immutable raw ledger,
and per-arm process identities are the acceptance gates.  The first four
attempts remain preserved as fail-closed diagnostics: `r1` rejected the
literal `cpt.%d` template directory, `r2`/`r3` rejected selector-FD paths, and
`r4` rejected a `/tmp` guest-visible selector path before the accepted path
namespace was used.  No full CG run was launched.

| Arm | Selector | Physical tile | Strict | `simTicks` | Decision |
|---|---|---:|---:|---:|---|
| native16 | `native_16k` | 16,384 | no | 93,534,103 | ACCEPT |
| native4x4 | `native_4kx4` | 4,096 | no | 98,347,730 | ACCEPT |
| original_hybrid | `legacy_4k` | 4,096 | no | 183,294,991 | ACCEPT |
| strict_two_pass | `page_fed_product_soa_jit` | 4,096 | yes | 266,578,031 | ACCEPT |

All four restore logs contain the same exact `CG_FINGERPRINT` line and the
same 11 deterministic reduction records.  Relative to native16, native4x4 is
5.1464% slower, original_hybrid is 95.9659% slower, and strict_two_pass is
185.0062% slower in this deterministic first observation; these are bounded
CG_NA=256 measurements, not full-application or variability claims.

Strict two-pass is **45.4366% slower than original_hybrid**. This is an
end-to-end treatment comparison, not a strict-flag-only or hardware-only
delta: `legacy_4k` is the original CPU-after-SPD hybrid, whereas the strict
arm uses page-fed physical-page MAA operations and a different instruction
decomposition. The exact fingerprint and reductions prove equivalent CG
results, but they do not make the internal instruction streams identical.
This result therefore rejects promotion of the current strict CG path.

The strict arm's partial-mask retirement counters are `26,672` issues and
`26,672` completions, with `26,667` partial and `5` full lines.  Its finite
payload staging reads exactly `163,840` scheduled words in `38,571` serialized
cycles with `5,536` bank-conflict cycles and zero backpressure.  The strict
timing trace has ten p and ten q records; every record has
`A_FIRST_ISSUE >= ROW_OFFSET_LAST_INSERT`, `order_ok=1`, and `terminal=1`.

The immutable raw-ledger SHA-256 is
`1c13e2b93f489e6958d880fcfa0c55785e7847bb8932fee8dd01086eb2bc0881`.
The supplemental timing restore is retained under `r5/strict_timing`; the
original virtual-only trace is preserved as `strict_trace.virtual_only.log`.

The sealed machine-readable manifest records gem5 SHA-256
`aa5c70b140b6fb66bfb9f4a28b34f009f025cf639eb288c01dbb91b0d2f609bb`,
Ramulator SHA-256
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`,
same guest SHA-256
`e93288cc8c9ff74f97783f5bbc0b9e8a65769e55374c61a76c6ef01760740001`,
and same-checkpoint identity
`c72c0075a7628cecb49419e3e2acb200f5deb501b7ac910e771c58029644e40f`.

The committed manifest is the authoritative handoff once the small matrix
passes; raw logs and stats remain outside Git under the coordination evidence
root.
