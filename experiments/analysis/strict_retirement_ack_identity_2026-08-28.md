# Exact virtual-retirement ACK identity (2026-08-28)

## Decision

**Accept exact bounded ACK identity for the selected strict CG mechanism.**

Each virtual retirement WriteReq now carries
`{physical line address, producer generation, non-recycled transaction}` in a
packet sender state. The fixed scoreboard retains the same identity with page,
backing-line, and word-mask metadata until WriteResp. A response with the
wrong address, generation, or transaction fails before completion counters or
page readiness change. Reusing the same address and generation cannot grant an
older delayed ACK authority over the new write.

The transaction allocator is 64-bit and never resets between operations.
After issuing `UINT64_MAX`, it enters an exhausted state rather than wrapping.
The selected run does not approach exhaustion; this is a source-level
fail-closed boundary, not a dynamically reached workload result.

## Selected replay

The exact selected geometry was replayed from the accepted NA1024 checkpoint:

- 16K logical Row/Offset reorder;
- 4K physical SPD;
- 64 direct-index feeder lines;
- 16 combiner lines, 4 ways, 4 banks, round-robin replacement;
- 32 acknowledged write credits; and
- masked 64-byte P retirement.

Result root:
`/data1/nier/dx100-runs/2026-08-28-lead-ack-identity-feeder64-na1024-r1`.

The replay exits zero at `m5_exit`, preserves the exact CG fingerprint and all
11 deterministic reductions, and closes 65 P timings, 65 Q timings, 65 whole
windows, 260 product pages, and 358,114 issued/completed P writes. It takes
exactly **1,249,282,534 `simTicks`**, identical to the selected pre-hardening
arm. The mechanism is therefore timing-neutral in gem5.

Trace identity validation independently finds:

| Invariant | Result |
|---|---:|
| Write issues | 358,114 |
| Write completions | 358,114 |
| Transaction range | 1..358,114 |
| Duplicate/zero transaction IDs | 0 |
| Issue/completion identity mismatch | 0 |

## Focused adversarial validation

The optimized and ASan/UBSan scoreboard tests reject duplicate insertion,
full capacity, wrong address, wrong generation, wrong transaction, missing or
duplicate ACK, delayed ACK after same-address/same-generation reuse, invalid
metadata, and reset while busy. They exercise both the accepted 32-credit
configuration and 64-credit compatibility. Source contracts require
scoreboard identity acceptance before completion accounting and page
readiness. Thirty Python storage/configuration/source contracts pass.

## Storage accounting

The packed semantic floor is 44 B per live write plus one 8-B transaction
allocator per indirect unit. At the selected 32 credits this is **1,416 B per
unit, 5,664 B across four units**. It is charged once in generic virtual
control regardless of the optional direct-retirement handoff; that handoff
references but does not duplicate the scoreboard.

With this corrected charge, the selected 64-line feeder configuration has a
563,444-B physical-SPD-plus-active-virtual payload/control lower bound, still
73.1329% below native SPD payload alone. This remains a bit-count lower bound,
not synthesized area, power, or Fmax.

## Remaining limits

- Packet sender state proves exact ownership in gem5, not a synthesized RTL
  transaction table or coherence-network encoding.
- Natural 64-bit allocator exhaustion is fail-closed in source but not reached
  dynamically.
- Competing CPU/MAA writers, cache eviction/retry, checkpoint/drain boundaries,
  lookup/mask timing, ports, and post-synthesis frequency remain separate
  hardware validation obligations.
- The result remains strict CG evidence, not native4 or suite-wide promotion.

The eight-entry artifact ledger is
`strict_retirement_ack_identity_artifacts_2026-08-28.sha256`, SHA-256
`1cb8e75cc2a0be2f8a25c69e48c92a81849326db2b469f2db698986e0c391161`.
