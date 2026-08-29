# Safe bounded complete-line combiner (2026-08-29)

## Decision

Accept the 512-tag, 16-way, 1,600-word combiner as a **micro-level candidate**
that fixes the unsafe placeholder mechanism.  It retains partial result words
privately and publishes only complete cache lines.  It reproduces the 16.93%
micro improvement without writing non-semantic bytes to coherent memory.

Do not make a full-application or synthesized-hardware claim yet.  CG NA256
does not benefit from this optimization family, and tag/reference/payload
timing and area remain lower-bound models.

## Organization

- 512 line tags arranged as 32 sets x 16 ways;
- one shared 1,600-word FP64 payload pool (12,800 B/unit);
- eight response lines, 64 FP64 words (512 B/unit);
- 1,664 total result words, 40.625% of the enforced 4,096-word strict bound;
- feeder depth 64 at one generated line/cycle; and
- 32 exact acknowledged retirement credits.

The live high-water marks are 409 lines and 1,592 words.  Offline replay finds
408 tags/1,592 words as the exact trace knee; 512/1,600 rounds the structure to
a power-of-two tag count and a small payload margin.  Both the control and
candidate perform a 16-way lookup, so the candidate does not assume a wider
comparison than the 16-line fully associative control.

## Exact result

Same binary, guest, checkpoint, input, semantic work, and transparent consumer:

| Arm | `simTicks` | Full / partial writes | Transport bytes | Backing RFO misses |
|---|---:|---:|---:|---:|
| 16-line control | 56,868,031 | 0 / 8,668 | 554,752 | 2,048 |
| Safe 512x16 / 1,600 words | 47,241,090 | 2,048 / 0 | 131,072 | 0 |

The safe arm is 16.9286% lower latency (1.203783x), removes 6,620 backing
transactions, removes 2,048 L3 misses and 2,046 Ramulator reads, and preserves
the exact output hash, 16,384 B words/descriptors, 9,523 A issues/responses,
four pages, strict ordering, and all ACKs.

Against the same-binary feeder64 controls already sealed for this final
binary, safe hybrid is 2.579% faster than native16 and 38.702% faster than
native4x4.  As shown by the separate no-overlap experiment, the native16
crossing combines complete-line retirement with page-level producer/consumer
overlap; without overlap the hybrid remains slower than native16.

## Why this fixes the reviewer blocker

The rejected dense-initialization path sent the first non-full mask as an
unmasked line containing zero placeholders.  This candidate never does so:
all 2,048 writes have the full-line convention `valid_words=0`, and the live
partial-write count is zero.  Incomplete words remain in the private bounded
payload pool and therefore cannot be read or evicted through ordinary
coherent memory.

The independent review of the earlier fully associative safe416 point
(`experiments/reviews/2026-08-29_safe_combiner_review.md`) rehashed its evidence
and agreed that the observed execution closes placeholder publication.  Its
main hardware objection was the cost-free 416-way lookup.  The final r3 point
uses 16 ways, matching the control's lookup width.

The review's claim that the 4,096-word result bound was not source-enforced is
incorrect for the integrated source: `StrictTwoPhaseReference::begin()`
returns `ResultCapacityTooLarge` when `resultCapacityWords >
PhysicalElements`, and the focused unit test exercises that rejection.  The
earlier uncapped 256/512 CG attempts also dynamically failed at this exact
gate.  The final candidate is far below it at 1,664 words.

## Storage and remaining hardware cost

The exercised one-unit comparable lower bound rises from 805,392 B to 829,502
B, a 24,110-B delta.  It still reports a 65.683% reduction versus native
comparable storage.  The added payload is 11,776 B above control; the rest is
bounded tag/mask/reference/allocator metadata.

This is not an area/Fmax result.  Remaining obligations include:

- a timed implementation of 16-way tag match and set selection;
- reference/payload RAM read/write ports and full-line drain bandwidth;
- reset/epoch implementation rather than a free host-vector clear;
- victim, insert, drain, and ACK arbitration;
- competing-agent coherence and delayed/reordered ACK stress; and
- synthesis or a calibrated latency/energy model.

The total semantic payload work is unchanged.  Both arms insert and later read
16,384 result words; safe grouping changes when lines become coherent, not the
number of result words stored.

## Scope

CG NA256 is tick-identical when the rejected no-read mechanism activates, and
the earlier large-combiner CG sweep reduced transactions without improving
latency.  Do not launch full CG.  The next application gate should be XRAGE or
another dense direct-index result whose backing RFO misses are actually on the
critical path.

Evidence root:
`/data1/nier/dx100-runs/2026-08-29-hybrid-safe-combiner-pair-r4`.

- Result SHA-256:
  `f2b2840b06d3298510994fed9aa1c8567d901f6fd2eb9dc96d282bf2424e64eb`.
- Artifact-ledger SHA-256:
  `89c270bb33a59afd411d13ef01639952a087eddea16674396b3fd27b5aee8f0a`.
- gem5 SHA-256:
  `bb3702ec8fa8e9b328f0efd22da29f756d70679ab3aa69a080dd41e9f2ea4598`.
