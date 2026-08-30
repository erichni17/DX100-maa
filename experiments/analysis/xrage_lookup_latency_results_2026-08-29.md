# XRAGE combiner lookup-latency result (2026-08-29)

## Decision

Accept a pipelined 8-way lookup latency of at least eight MAA cycles as a
noncritical sensitivity for the selected XRAGE point. Latency 8 closes exact
output and 65,536 lookup issues/completions at 37,359,367 `simTicks`, only
0.299% above latency 0 and still 11.706% below native16.

This is a timing sensitivity, not a claim that an 8-way SRAM/CAM lookup takes
eight cycles. It shows that the measured application margin survives a broad
latency range when lookup throughput remains four starts and four completions
per MAA cycle.

## Fixed comparison

All arms use source `fa0a63f523abf17e03348495bf3f19bf02b7fe21`, gem5
SHA-256
`7afc44c7ff8bd8ca972ae2d7acd6ae15df3311220d8b1fd13fc27426f3c1e023`,
the same XRAGE input/verifier, logical16K/physical4K, 1,536 tags, 8-way XOR7,
2,560 combiner words, 1,024 response words, and drain width 1.

Only `virtual_combine_lookup_latency_cycles` changes.

| Latency | `simTicks` | vs latency 0 | vs native16 | Lookup issues/completions | Peak pending sum |
|---:|---:|---:|---:|---:|---:|
| 0 | 37,247,939 | 0.000% | -11.969% | 0 / 0 | 0 |
| 1 | 37,250,443 | +0.007% | -11.963% | 65,536 / 65,536 | 16 |
| 2 | 37,279,552 | +0.085% | -11.894% | 65,536 / 65,536 | 32 |
| 3 | 37,297,706 | +0.134% | -11.851% | 65,536 / 65,536 | 48 |
| 8 | 37,359,367 | +0.299% | -11.706% | 65,536 / 65,536 | 92 |

The peak is summed across four sequential logical operations. No arm records a
ready-token wait cycle, so the finite completion ports keep up with downstream
combiner insertion for this workload.

## Modeled mechanism

For nonzero latency:

- at most the existing four response words start lookup per MAA cycle;
- each exact response-slot/Offset token becomes eligible after `L` cycles;
- at most four eligible tokens complete and insert per MAA cycle;
- response payload remains in the already-counted response pool; pipeline
  tokens duplicate metadata only, not data;
- pending metadata capacity is bounded by the response-word pool; and
- stale identity, generation mismatch, capacity overflow, early slot release,
  or terminal issue/completion imbalance fails closed.

Token metadata is bounded but not yet charged in the packed storage ledger.
Source permits as many pending tokens as response words (1,024 selected),
whereas measured peak at latency 3 is 12 per logical XRAGE operation. A
hardware implementation should use a much smaller fixed queue with
backpressure and charge its identity/ready-cycle bits explicitly.

Latency 0 exactly reproduces the prior 8-way/XOR7 run at 37,247,939 ticks,
closing the default-off neutrality check.

## Remaining boundary

The sensitivity still does not synthesize or time the XOR/set decode, tag RAM,
same-set hazards, payload/reference RAM ports, ready-line selection, reset, or
scoreboard. It proves only that a bounded pipelined lookup delay is largely
hidden by current memory/consumer overlap.

Evidence root:
`/data1/nier/dx100-runs/2026-08-29-xrage-lookup-latency-r1`.

Artifact ledger:
`xrage_lookup_latency_artifacts_2026-08-29.sha256`.

## Next gate

Run latency 3 on all 14 FLAG gathers with 2,048 tags/8-way XOR7. Then test a
bounded ready queue/payload-port model or competing destination ownership.

Both immediate gates now close: FLAG lookup-3 adds 0.155% geometric-mean
latency, and XRAGE's bounded page-ready queue is exact at 37,291,759 ticks.
See `flag_lookup_latency_results_2026-08-30.md` and
`xrage_page_ready_drain_results_2026-08-30.md`.
