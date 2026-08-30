# XRAGE 8-way XOR combiner result (2026-08-29)

## Decision

Accept an 8-way XOR-folded combiner as a lower-complexity XRAGE design point.
At fixed 1,536 tags, 2,560 combiner words, 1,024 response words, and finite
one-line-per-cycle drain, shifts 7 and 10 both close exact output with no
partial victim. Their latency is within 0.025% of the 16-way control.

This halves tag comparisons per insertion lookup from 16 to 8; it does not
reduce payload capacity or tag count. FLAG breadth is a separate live gate.

## Offline falsification

The current-source trace contains exactly four operations, 65,536 unique
destination-word insertions, and 8,192 full-line publications. The offline
model replays the source's exact set formula, round-robin victim selection,
1,536 tag slots, and 2,560 useful-word pool.

| Organization | Full writes | Partial evictions | Final partials | Decision |
|---|---:|---:|---:|---|
| 16-way, low bits | 8,192 | 0 | 0 | passes |
| 8-way, low bits | 8,191 | 1 | 1 | rejects complete-line mode |
| 4-way, low bits | 8,159 | 33 | 33 | rejects complete-line mode |
| 8-way, XOR shift 7 | 8,192 | 0 | 0 | passes |
| 8-way, XOR shift 10 | 8,192 | 0 | 0 | passes |
| best tested 4-way (shift 11) | 8,169 | 23 | 23 | rejects complete-line mode |

Thus XOR indexing removes the single 8-way conflict, but no tested shift 1-20
makes 4-way legal.

## Live result

All arms use source `795ce077373d5880f68ce4923427229b83285f33`, gem5
SHA-256
`cfca5059935d70473ce853292385b3d40ef5361f8319f5fbecd75863b523f73b`,
the same input/verifier, and drain width 1.

| Organization | `simTicks` | vs 16-way | vs native16 |
|---|---:|---:|---:|
| 16-way, low bits | 37,252,008 | 0.000% | -11.959% |
| 8-way, XOR shift 7 | 37,247,939 | -0.011% | -11.969% |
| 8-way, XOR shift 10 | 37,242,931 | -0.024% | -11.981% |

The tiny differences are scheduling noise/order effects, not an XOR speedup.
The meaningful result is timing equivalence with half the lookup associativity.

Every live arm closes exact hash `5576400619275092867`, 8,192 full producer
issues/ACKs, zero partials, four exact direct-consumer contexts, and no
fallback/overflow.

## Hardware interpretation

The 16-way 1,536-tag design has 96 sets. The 8-way design has 192 sets and
requires one fixed XOR fold before the existing modulo/set decode. It keeps
the same useful-word payload and line metadata count while halving parallel
tag matches and the selected-way mux fan-in.

This does not yet time the 8-way match, XOR/modulo decode, payload/reference
ports, or ready selection. It is a complexity reduction before those timing
models, not a synthesized area/Fmax result.

## Evidence

- insertion trace and replay:
  `/data1/nier/dx100-runs/2026-08-29-xrage-complete-line-insertion-trace-r1`;
- live 16-way/XOR7/XOR10 matrix:
  `/data1/nier/dx100-runs/2026-08-29-xrage-combiner-xor-live-r1`;
- artifact ledger: `xrage_combiner_xor_artifacts_2026-08-29.sha256`.

## Next gate

Use 8-way shift 7 at the 2,048-tag/3,072-word FLAG coverage point. If all 14
gathers retain full-line-plus-tail closure, shift 7 becomes the selected
cross-workload map. Otherwise retain 16-way for FLAG or search a jointly legal
hash using FLAG insertion traces.
