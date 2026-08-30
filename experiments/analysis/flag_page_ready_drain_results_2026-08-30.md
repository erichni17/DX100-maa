# FLAG bounded page-ready drain result (2026-08-30)

## Decision

Accept the bounded page-ready queue across all 14 FLAG gathers. Queue-on is
exact and geometric-mean latency differs from slot-scan mode by -0.0002%.

Both arms use source `fa0a63f523abf17e03348495bf3f19bf02b7fe21`, gem5
SHA-256
`7afc44c7ff8bd8ca972ae2d7acd6ae15df3311220d8b1fd13fc27426f3c1e023`,
2,048 tags/8-way XOR7, lookup latency 3, and drain width 1.

Every queue-on case closes exact output, exact lookup tokens, full-line-plus-
tail publication, and write ACKs. It selects every full line through queue
metadata and records 27 to 600 later-page deferrals per case.

The 2,048-slot packed metadata floor is about 6.8 KiB: two 11-bit links, one
queued bit, and a 4-bit page id per slot, plus 16 head/tail pairs. This replaces
an untimed scan across all 2,048 tags; it still needs finite enqueue/dequeue
ports.

Evidence roots:

- scan control: `/data1/nier/dx100-runs/2026-08-29-flag-xor8-lookup3-r1`;
- bounded queue:
  `/data1/nier/dx100-runs/2026-08-30-flag-selected-ready-drain-r1`.

Artifact ledger: `flag_page_ready_drain_artifacts_2026-08-30.sha256`.

Remaining hardware gates are payload/reference RAM ports, exclusive backing
ownership/coherence, reset/epoch behavior, and synthesis-based area/Fmax.
