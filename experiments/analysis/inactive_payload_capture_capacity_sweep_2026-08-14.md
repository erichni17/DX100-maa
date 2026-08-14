# Inactive producer-payload capture: bounded repair contract

## Status and scope

This is a default-off hardware contract for retaining selected authoritative
64-byte producer `WriteResp` payloads until their materializer page becomes
active. It does not expand SPD visibility, cache ports, materializer line
buffers, commit records, or response pools. A retained hit is copied into the
existing charged materializer buffer after the modeled lookup cycle and uses
the existing delayed SPD commit. Every miss or drop uses the unchanged exact
coherent `ReadBacking` path.

No gem5 workload result is claimed here. Promotion still requires an exact
same-checkpoint default-off comparison and full correctness closure.

## Constant-time lifetime and RAM organization

- Capacity is exactly 0, 64, 128, 256, or 512 lines; 0 is the default.
- There are four direct-mapped lifetime descriptors. `tokenTile[1:0]` selects
  one descriptor and the complete `(tokenTile, generation,
  payloadIncarnation, backingAddress)` tag is checked at that index. There is
  no four-way CAM, priority encoder, or descriptor search.
- A descriptor collision replaces the selected descriptor in one operation.
  The displaced lifetime immediately becomes coherent-fallback-only. Its RAM
  tags may remain stale; replacement and retirement never walk the RAM. The
  descriptor's stored-line count reports the exact number displaced, excluding
  at most the one hit already authenticated in the output latch; only that
  unlatched count is added to the explicit global drop counter without
  inspecting any entry.
- A probe can hit only when the selected exact descriptor is active. A probe
  after descriptor replacement or clear consumes the ordinary RAM read port
  opportunity and completes as a coherent backing miss after one cycle; it
  cannot authenticate a stale RAM tag.
- A write that selects a stale RAM tag reclaims exactly that entry. A write
  that selects another live tag is rejected: the first retained owner remains
  authoritative and the challenger follows coherent fallback.
- Clear/retirement invalidates only the selected exact descriptor. It performs
  no entry-wide clear and no page replay walk. The host-only exhaustive
  `assertInvariants()` diagnostic may scan entries in unit tests; it is not a
  lifecycle or modeled hardware operation.
- Every MAA registration allocates a monotonically increasing payload
  incarnation before any consumer-context incarnation exists. That payload
  incarnation is present in all descriptor/RAM/output identities and in begin,
  capture, lookup, replay, summary, and retirement-related trace provenance.

## Port, timing, and collision semantics

The storage is one direct-index 1R1W RAM. Each port accepts at most one access
per MAA cycle and each access takes one MAA cycle.

A write issued at cycle N is held in the one-entry write input latch and
becomes the RAM value at N+1. If read and write select the same index at N, the
read returns the pre-write value regardless of software call order
(read-before-write SRAM semantics). A second same-cycle read retries; a second
same-cycle producer write cannot be retained and is counted as a global drop
before coherent fallback.

A probe at N copies an active-descriptor exact hit into the sole 64-byte output
register and reports completion at N+1; a miss is delayed by the same cycle.
The output tag contains the complete payload lifetime, line, and producer
transaction. It is authoritative until `take()`. A live direct-index conflict
cannot replace the RAM entry, so a capture/probe pair has the same first-owner
result in either call order. The latched payload counts as a replay, not a
drop. The same rule holds if its lifetime descriptor is replaced before delayed
`take()`; every other line displaced with that descriptor is a coherent-
fallback drop.

## Outcome and trace attribution

Materializer closure remains exact per execution:

```text
forwarded_lines + staged_direct_lines + cache_read_fallback_lines
    == producer_lines
producer_line_acks + page_fallback_lines == producer_lines
```

Capture conflicts, descriptor displacement, untracked/stale/invalid attempts,
port drops, lookup hits/misses, and high-water occupancy can cross overlapping
materializer lifetimes. Their summary fields are therefore explicitly named
`global_inactive_payload_*`; they are cumulative device counters, not
per-owner fields. Per-owner page fields are limited to that execution's exact
coherent fallback reads. `Captured` counts a successful new retention and
`Conflict` counts the rejected challenger and its coherent-fallback drop.
`Full` remains in the trace enum ABI but the direct-mapped descriptor
implementation never returns it. The legacy
`global_inactive_payload_latest_owner_{overwrites,evictions}` trace/stat
fields are reserved ABI and always zero.

## Exact packed hardware lower-bound equations

The equations below use packed RTL bits and never host `sizeof` values.

```text
key_bits                 = token 16 + generation 64
                         + payload incarnation 64 + backing address 64
                         = 208
RAM_tag_bits_per_entry   = key 208 + line 16 + transaction 64 + valid 1
                         = 289
descriptor_bits_each     = valid 1 + key 208 + line_count 16
                         + seven 16-bit lifetime counters
                         + four pages * four 16-bit page counters
                         = 593
four_descriptor_bits     = 2,372
read_port_state_bits     = next_available_cycle 64
write_port_state_bits    = next_available_cycle 64 + pending 1
                         + completion_cycle 64 + RAM_index log2(capacity)
                         + write_payload 512 + write_tag 289
                         = 930 + log2(capacity)
output_latch_bits        = output_payload 512 + output_tag 289 = 801
global_capture_control   = capacity 10 + occupancy 10 + high_water 10 = 30
MAA_lookup_control_bits  = context owner 144 + exact request 170
                         + payload incarnation/backing suffix 128
                         + valid/completion/result 68 = 510
MAA_persistent_payload_incarnation_bits(T) = 64 * token_count T
```

The complete capture lower bound is:

```text
capture_bits(C) = C*512 RAM payload + C*289 RAM tags
                + 2372 descriptors + 64 read-port state
                + (930 + log2(C)) write-port state
                + 512 output payload + 289 output tag
                + 30 global capture control
combined_bits(C,T) = capture_bits(C) + 510 MAA lookup control
                   + 64*T persistent payload incarnations
```

The concrete table uses the configured 32 token tiles, so the persistent term
is 2,048 bits (256 bytes).

| Lines | RAM payload (B) | RAM tag bits | Non-payload control bits excluding RAM tags | Capture total including 64B output (B, rounded once) | MAA lookup bits | Persistent incarnation bits (32 tokens) | Combined (B, rounded once) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 64 | 4,096 | 18,496 | 3,691 | 6,934 | 510 | 2,048 | 7,254 |
| 128 | 8,192 | 36,992 | 3,692 | 13,342 | 510 | 2,048 | 13,662 |
| 256 | 16,384 | 73,984 | 3,693 | 26,158 | 510 | 2,048 | 26,478 |
| 512 | 32,768 | 147,968 | 3,694 | 51,790 | 510 | 2,048 | 52,110 |

The trace reports every term independently in bits, including
`64*num_tiles`, plus the exact combined total and rounded legacy byte fields.
`host_capture_object_bytes` and `host_lookup_object_bytes` are labeled
diagnostics only and are never added to the packed hardware equations.

## Review-gated experiment matrix

Offline GZP evidence selects first-owner. Its retained fractions scale with
the evaluated capacity points as follows:

| Capacity point | First-owner retained fraction |
| ---: | ---: |
| 512 | 12.5% |
| 1024 | 25% |
| 2048 | 50% |

`latest-owner` retained 0%, 0%, and about 0.019% at those same points, so it
is rejected rather than offered as a policy. After independent acceptance,
compare one default-off control with the supported capture capacities from one
frozen checkpoint:

```text
--maa_inactive_page_payload_capture_lines={64,128,256,512}
--maa_inactive_page_payload_capture_conflict_policy=first-owner
```

An arm is promotable only if correctness and terminal closure pass, ordinary
SPD visibility is unchanged, exact output identity matches, and any reduction
in coherent fallback or time justifies the explicit combined storage above.
