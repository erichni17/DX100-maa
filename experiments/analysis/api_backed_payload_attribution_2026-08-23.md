# API backed-payload attribution repair (2026-08-23)

## Disposition

Accepted for the narrow backed-payload attribution question.  The repaired
`backed16` and `backed4` arms execute the same token-bound page materializer
and differ only in physical SPD payload capacity (16K versus 4K elements).
This result does not claim a performance advantage for either capacity.

## Root cause and fix

The rejected evidence root was
`/data1/nier/dx100-runs/2026-08-23-api-backed-attribution-b9d97b56-r1`.
Its `backed16` trace contained four materializer admission fallbacks with
`reason=static_geometry`; `backed4` instead completed one four-page exact
materialization lifetime.  All other admission fields matched.  The static
gate required `physical_tile_elements == 4096`, so a correctly formed 4K
producer-page request could not use a physically 16K destination tile.

Commit `be77a62ca992507d9145fe0d44c9ed491c8310a2` replaces only that equality
with an exact allow-list: logical capacity must remain 16K and physical
capacity must be exactly 4K or 16K.  The existing port-domain, line-handoff,
word-size, range, stride, token, destination, overlap, producer-generation,
backing-address, and bounded-resource checks remain unchanged.  Smaller,
intermediate, and different-logical geometries remain fail-closed.

## Accepted raw evidence

Raw root:
`/data1/nier/dx100-runs/2026-08-23-api-backed-attribution-be77a62c-r1`

- Campaign exit: 0; runner terminal: `PASS: 14 exact matched runs`.
- Source commit: `be77a62ca992507d9145fe0d44c9ed491c8310a2` with clean source status.
- gem5 SHA-256:
  `44b6e86ebc86fd692ce02dcb2e1f627082ded10c741134ae49de5721e2edcb45`.
- Guest SHA-256 (shared native16/hybrid binary):
  `72534264fe4b8bcaf21dcb1ca0f7bb2d69292c3a558df3e2c517b212ee6967ba`.
- Ramulator library SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
- Both backed selectors have SHA-256
  `c7abc1840f2b1bb55d990fe803539698d33619e599be388ed48619a08d280211`.
- After normalizing output, checkpoint, and selector paths, the two restore
  commands differ only in `--maa_physical_tile_elements=16384` versus `4096`.

| Arm | Replica | Output hash | Errors | ROI simTicks | Submissions/pages/retirements | Admission/dispatch fallbacks |
|---|---:|---:|---:|---:|---:|---:|
| backed16 | 1 | 7228541527853630339 | 0 | 23613972 | 4/4/1 | 0/0 |
| backed16 | 2 | 7228541527853630339 | 0 | 23613972 | 4/4/1 | 0/0 |
| backed4 | 1 | 7228541527853630339 | 0 | 23613972 | 4/4/1 | 0/0 |
| backed4 | 2 | 7228541527853630339 | 0 | 23613972 | 4/4/1 | 0/0 |

These ROI values are the first `simTicks` window and match
`analysis/report.tsv`.  Each raw `stats.txt` also contains a later cumulative
window of 582,694,885 ticks; that cumulative value is not used in performance
comparisons.

Every backed trace reports one exact summary with four pages, 2,048 lines,
370 forwarded lines, 1,678 coherent-read fallback lines, 2,048 producer line
acknowledgements, zero page fallback lines, and `exact_closure=1`.

## Validation

- `tests/maa/run_hybrid_page_materializer_unit.sh`: optimized and
  ASan/UBSan executions passed.
- The two focused backed-pair Python contract tests passed.
- Production `build/X86/gem5.opt` completed from the clean fix commit.
- The exact two-replica matrix completed all 14 planned runs.
- `git diff --check` passed before the source checkpoint.

The full general-hybrid Python contract module has one unrelated existing
failure in `test_cg_and_ume_materializers_use_immutable_page_registers`: the
current branch's imported UME source contains a page-register initialization
that this older source-text assertion rejects.  The focused backed tests pass,
and no UME source or test was changed for this repair.
