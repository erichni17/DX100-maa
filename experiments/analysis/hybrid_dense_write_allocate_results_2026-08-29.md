# Dense backing write-allocation results (2026-08-29)

## Decision

**Reject the current implementation for promotion.**  It is a large measured
upper bound for the dense direct-index API gather, but its first unmasked
packet writes zero placeholders into ordinary coherent memory.  An unrelated
reader or dirty eviction can observe corrupt non-semantic bytes.  It is also a
tick-identical result on short CG.  Keep the flag default-off, make no
architecture speedup claim, and launch no full CG run.

## Mechanism

The unpredicated direct-index producer eventually overwrites every logical
backing word.  On the first fragment for each backing cache line, the MAA now
sends one aligned unmasked 64-byte request.  gem5 promotes that request to
`WriteLineReq`, obtaining writable cache ownership without fetching old line
data.  Placeholder bytes in the packet do not count as completed words; the
existing retirement scoreboard retains the real semantic mask, and page
readiness still requires exact ACKs for every real word.  Later fragments use
the existing masked path.  This preserves final output but is not a legal
coherent partial-write transformation: metadata cannot undo placeholder bytes
already installed in a visible cache line.

One initialized bit per logical backing line prevents repeated no-read
allocation.  The fixed maximum is 2,048 bits (256 B) per indirect unit, enough
for logical16K FP64.  FP32 CG uses 1,024 active bits (128 B) per unit.

## Equal-work API micro

All arms use one final binary, SHA-256
`14f9870e5bf337588d50e012a557e26ed51e99ccc9b07476991960d8cf4e1917`,
one guest/checkpoint, feeder depth 64 at one generated line/cycle, and exact
logical16K semantic work.

| Arm | `simTicks` | Relative to native16 |
|---|---:|---:|
| Native16, feeder64 | 48,491,838 | baseline |
| Native4x4, feeder64 | 77,068,112 | +58.936% |
| Hybrid control | 56,868,031 | +17.273% |
| Hybrid dense allocation | 47,265,504 | **-2.529%** |

Dense allocation lowers hybrid latency 16.8856% and is 1.02595x faster than
native16 in this micro.  The crossing has a measured explanation:

| Counter | Hybrid control | Dense | Delta |
|---|---:|---:|---:|
| Dense first-line writes | 0 | 2,048 | +2,048 |
| `WriteLineReq` accesses | 0 | 2,048 | +2,048 |
| L3 MAA misses | 4,097 | 2,049 | -2,048 |
| Ramulator reads | 26,874 | 24,828 | -2,046 |
| L3 MAA miss latency | 433,536,613 | 321,691,384 | -25.80% |
| Backing transactions | 8,668 | 8,659 | -9 incidental |

The gain is not transaction combining: write count is essentially unchanged.
It comes from removing one old-data fetch per backing line.

## Overlap attribution

The `transparent_ready` pair disables producer/consumer page overlap without
changing dense retirement:

| No-overlap arm | `simTicks` |
|---|---:|
| Hybrid control | 59,706,941 |
| Hybrid dense allocation | 49,918,179 |

Dense allocation still improves the no-overlap hybrid by 16.3947%, proving the
no-read mechanism itself matters.  Without overlap, dense hybrid remains
2.9414% slower than native16.  Re-enabling the ordinary transparent page
pipeline reduces dense latency another 5.3141%, which explains why the final
combined design crosses native16 rather than violating the expected
virtualization ordering without cause.

## CG gate

The same final binary was run from one accepted CG NA256 checkpoint:

| Arm | `simTicks` | P backing writes | Dense initializations |
|---|---:|---:|---:|
| Control | 246,463,712 | 26,672 | 0 |
| Dense | 246,463,712 | 26,672 | 10,240 |

Every output, reduction, B/descriptor/A count, page, and ACK gate passes.  The
dense arm doubles retirement-cache `WriteLineReq` accesses for the P lines,
but L3 misses, miss latency, Ramulator reads, strict phase counters, and
`simTicks` are identical.  CG's relevant lines already avoid the first-write
miss on the measured critical path.  Reject NA1024/full-CG promotion.

## Storage

In the FP64 API configuration, dense allocation adds exactly 256 B to the
configured comparable lower bound: 805,392 B to 805,648 B.  The resulting
storage reduction versus native comparable state is 66.670%.

In the four-unit FP32 CG geometry, it adds 128 B/unit, or 512 B total.  The
configured comparable lower bound becomes 1,597,224 B, still 49.717% below
native.  These are packed semantic counts, not synthesized area, energy, port,
or Fmax results.

## Blocking correctness finding

The independent review in
`experiments/reviews/2026-08-29_dense_write_allocate_review.md` rehashed all
raw evidence and reproduced the timing result, but rejects promotion.  Every
one of the 2,048 first writes has a non-full semantic mask; the staging line is
zero-filled outside that mask, and omitting byte enables publishes those
zeros.  The final checksum does not test an intervening coherent reader or
eviction.

The safe successor is to keep partial words private until a complete line is
authoritative.  A 512-tag, 4,032-word combiner plus 64 response words remains
within the 4,096-word result bound for FP64 and is being tested separately.

## Correctness boundary

- Enable only when every logical backing word is guaranteed to be overwritten
  before token completion.  Predicated, sparse, old-value-preserving, and
  conditional-RMW outputs are ineligible.
- Consumers must obey the completion/page-ready token.  Concurrent ordinary
  coherent reads of incomplete backing are not validated and could observe
  placeholder bytes.
- Exact address/generation/transaction ACK ownership remains mandatory.
- Bitmap lookup/write timing, cache invalidation bandwidth, competing-agent
  coherence, eviction/reload behavior, and synthesis remain open gates.
- The current fixed tracker supports logical16K only; row-table virtualization
  and logical64K remain separate work.

## Evidence

- Dense pair root:
  `/data1/nier/dx100-runs/2026-08-29-hybrid-dense-write-allocate-pair-r4`;
  result SHA-256
  `49f86c66498aa245936a03337f2ecdea0eec0841547d2c324aa816cea1e1ed7c`.
- Native controls root:
  `/data1/nier/dx100-runs/2026-08-29-hybrid-dense-native-controls-r2`;
  result SHA-256
  `b226ad535435ff680d214a4dbefc62332eb50e4866450ca9d166d596d12c5289`.
- No-overlap root:
  `/data1/nier/dx100-runs/2026-08-29-hybrid-dense-write-allocate-ready-pair-r1`;
  result SHA-256
  `6dc8a55f619d8a8fa259053fbf4b24761d7a3df4025c53dd1344ea16d779643a`.
- CG artifacts:
  `hybrid_dense_cg_na256_artifacts_2026-08-29.sha256`.

The API result is one deterministic performance upper bound.  It does not
justify XRAGE, application, or suite-wide promotion until the coherent-memory
bug is removed and adversarially closed.
