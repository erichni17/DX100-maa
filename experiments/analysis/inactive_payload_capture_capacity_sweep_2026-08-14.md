# Inactive producer-payload capture: pre-review sweep plan

## Status

No gem5 simulation has been launched. This is a guarded handoff for review
after the conflict-policy correction. Focused optimized/ASan/UBSan unit tests,
related materializer/ledger/context tests, and the final MAA incremental build
have passed. The full optimized gem5 build reached MAA after restoring its
ignored Ramulator dependencies. Its unrelated `StreamAccess.cc`
incomplete-`FaultBase` blocker was accepted separately as parent commit
`3b6a8696`; the preserved `-j12` full link is still in progress and is not a
promotion result.

The ignored dependency copies are deliberately untracked: from
`/data1/nier/worktrees/DX100-virtualization-line-handoff-20260812/ext/ramulator2/ramulator2/ext/`
to this worktree's corresponding directory, `spdlog` tree digest
`5d7e476d115d0341c7efde8da498ce5e1b8c36d293d6dbc3e8341f98a7176d0d`
and `yaml-cpp` tree digest
`b2ae93f55a5ddda69fd37870fab0ae0572ffb3a3ccf45cfea3b156c2e51adf5b`.
`argparse` remains empty in both worktrees and was not copied.

The matched API control to preserve is key `7228541527853630339`: 2,048 full
producer writes, 492 forwarded lines, and 1,556 coherent `ReadBacking`
fallback lines. Capacity outcomes must be compared only against a same
checkpoint control with that exact closure.

Trace analyzer `784980f0` reports 288 uncontended fallback opportunities for
latest-owner/512 and warns that first-owner can retain the wrong epoch region.
That is why the review matrix compares both policies before judging the capture
mechanism; it is not evidence of a measured new treatment result.

## Fixed hardware model

- Default off: `--maa_inactive_page_payload_capture_lines=0`.
- Direct-indexed power-of-two line array; no associative scan and no page
  replay walk.
- One full-line write port and one selected-line read port, each accepting one
  MAA clock-cycle access. Same-cycle write collisions drop to coherent
  fallback; a busy read retries the selected line.
- A one-entry hit/miss lookup pipeline delays both outcomes one MAA cycle. A
  miss may issue `ReadBacking` only at pipeline completion.
- A lookup issued at MAA cycle N completes at N+1. A hit's output register
  directly feeds the existing materializer buffer at N+1 and is charged only
  the normal SPD data latency from that edge; the capture RAM access is not
  charged a second time.
- The pipeline has one 64-byte output register and one fixed request/tag
  latch. Neither expands a cache port, materializer buffer/commit pool,
  response/combiner pool, row/offset scope, or physical SPD.
- `first-owner` is the default direct-index policy. `latest-owner` overwrites
  the same selected entry, including its exact key, line, transaction, and
  payload, at the same storage and port cost. The evicted owner deterministically
  uses coherent fallback.

The runtime trace records `conflict_policy`, write/read ports, access cycles,
and `port_time_unit=maa_cycles`. It also records captures, replays, conflicts,
write-port drops, and fallback reads separately for logical pages 0--3.

## Provisioned capture storage

The table includes the capture array, exact tags/control, and the fixed
64-byte read-pipeline output register. The fixed lookup-latch control bytes
are also emitted by the runtime trace as
`inactive_payload_lookup_latch_control_bytes`; they are not hidden in this
table because they do not vary by capacity.

| Lines | Array payload (B) | Tag/control (B) | Read-pipeline payload (B) | Variable total (B) |
| ---: | ---: | ---: | ---: | ---: |
| 64 | 4,096 | 2,163 | 64 | 6,323 |
| 128 | 8,192 | 4,019 | 64 | 12,275 |
| 256 | 16,384 | 7,731 | 64 | 24,179 |
| 512 | 32,768 | 15,155 | 64 | 47,987 |

The tag/control value includes exact identity, transaction tag, validity,
four lifetime descriptors, page-attribution and policy counters, finite-port
next-cycle state, and the policy selector. It does not claim synthesized SRAM
periphery, wiring, or host-container overhead.

## Review-gated same-checkpoint matrix

After review, run the unchanged API selector from one frozen hybrid checkpoint:
one default-off control plus capacities 64, 128, 256, and 512 for both
`first-owner` and `latest-owner`. All treatment arms use
`token_stream_ld`; only these two capture options differ:

```text
--maa_inactive_page_payload_capture_lines={64,128,256,512}
--maa_inactive_page_payload_capture_conflict_policy={first-owner,latest-owner}
```

For every terminal exact arm, report overall and per-page values for:

- captures, replays, conflicts, first-owner conflicts, latest-owner
  overwrites/evictions, and write-port drops;
- `cache_read_fallback_lines`, plus `page0`--`page3` fallback reads;
- lookup hits, misses, and read-port stalls;
- `simTicks`, exact output key, producer-line ACK closure, and all terminal
  exits.

Select no point unless it exact-passes and reduces fallback reads in the
affected tail pages *and* reduces `simTicks` sufficiently to justify its
variable bytes. A weak first-owner result with later-page fallback concentration
is a placement-policy finding, not a mechanism rejection; compare its equal
cost latest-owner arm before any conclusion.
