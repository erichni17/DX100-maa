# XRAGE virtual-backing ownership guard (2026-08-30)

## Decision

Accept the live-producer overlap guard as behavior-neutral for selected XRAGE.
The ownership-enabled binary reproduces 37,291,759 `simTicks`, exact output
hash `5576400619275092867`, and all selected mechanism counters.

## Contract

At virtual-producer registration, source now:

- validates the logical backing span without integer wrap;
- rejects reuse of the same completion token before all expected page writes
  receive ACKs; and
- rejects a backing span that overlaps any other live virtual producer until
  that producer's expected pages are all acknowledged.

The check reuses existing token generation, backing address, word size, and
page-readiness arrays. It adds no payload storage. Registration may compare the
fixed token table; this is control-path work, not per-word lookup.

## Evidence

The selected run uses source `33892a24cd4c60868d99c043fb9158f2a9a58457`,
gem5 SHA-256
`663e7d0631e94c4743fdcfab0e6c2ee8bb3ef14f05f7b64c883f4c0fefa1d681`,
8-way XOR7, lookup latency 3, drain width 1, and bounded page-ready selection.
It matches the predecessor's 37,291,759 ticks exactly.

Evidence root:
`/data1/nier/dx100-runs/2026-08-30-xrage-selected-ownership-r1`.

Artifact ledger: `xrage_ownership_artifacts_2026-08-30.sha256`.

Optimized plus ASan/UBSan unit tests cover valid, adjacent, overlapping,
zero-size, multiplication-overflow, and address-wrap spans.

## Remaining boundary

The guard compares registered virtual spans. A CPU write or a differently
aliased virtual address can still conflict with privately retained fragments.
Until a snoop/merge mechanism exists, software must provide exclusive
destination ownership from producer registration through final WriteResp.
