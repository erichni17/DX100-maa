# Shared-source overlap repair result

Date: 2026-09-01

Disposition: accepted candidate-only mechanism screen. Exactly one strict GZZ
candidate was simulated; no native arm or determinism replica was launched.

## Implementation

The repair retains the existing single pending source/fanout latch and the
single four-descriptor-per-cycle scan token. Request drains returned response
work first, evaluates the scan-ready latch against the exact response-slot and
unified-payload credits, makes at most one existing legal partial-spill attempt
when unified credit is blocked, and returns to Build without waiting for older
sources to retire. Build commits the pending address/word reservation and read
packet before clearing that sole owner, then starts the next scan.

`SharedSourceOverlapScheduler.hh` contains the storage-free decision helper.
Production adds overlap-resume, response-slot stall, unified-credit stall/cycle,
and pending-latch HWM stats plus matching trace events. Address-keyed source
reservations, out-of-order response slots, exact `SharedPayloadTransfer`
commit/rollback, event coalescing, and the existing terminal predicates remain
unchanged.

The source implementation and focused tests are commit `08b598de`. After the
user's publication-policy correction, the gate corrections `b318d308` and
sealed-validation correction `d20f1165` are local only and were not pushed.
The previously pushed private worker checkpoint was left untouched.

## Focused validation

- Optimized and ASan/UBSan scheduler tests pass readiness, exact slot/pool
  blocking, same-tick wake, response/write progress, no-progress rejection,
  post-scan legality, out-of-order completion, and pending HWM one.
- The captured 1,025-line GZZ replay closes 1,025 responses and reproduces HWM
  15 at cycle 4,145 for the current latency vector and HWM 83 at cycle 4,640
  for the sealed-r6 vector. Geometry closes at 1,025 scans, 16,384 descriptors,
  and 4,096 scan cycles.
- The existing optimized and ASan/UBSan shared-payload transfer suite passes,
  including exact final-use rollback and retry closure.
- Fifty focused Python contracts passed before the candidate launch.
- `IndirectAccess.o`, `MAA.o`, and `gem5.opt` built successfully. The exact
  candidate simulator SHA-256 is
  `60665e42a37caa7d9f1c4f6957d93ce00b314b298a7a450b185fcbebbb8fc6f7`.

## Candidate identity and result

Immutable root:
`/data1/nier/dx100-runs/2026-09-01-ume-gzz-shared-source-overlap-r1`

- source commit: `08b598dec84770c2fe0699169698f541d16d9c51`
- manifest SHA-256:
  `f1b2830d51d44ec5286e6acee99d561e227ef6dedd8f61d63ac7762798b4b561`
- result SHA-256:
  `689dd335e6b0f1248a8bcee9a136806cd418a21558d78764b1f1007949e9167b`
- artifact ledger SHA-256:
  `b22fcc9d174023ef267214cee11599abf5ad4235bfb90085f989153b1f9b5046`
- decision: `ACCEPT_SHARED_SOURCE_OVERLAP_REPAIR`
- integrated classification: `SOURCE_MLP_RECOVERED`
- exact output fingerprint: `7602200327591349891`
- exact reference: 196,384 elements, zero gradient errors, zero volume errors
- checkpoint/restore returns: zero/zero; terminal marker: `m5_exit`

| Metric | 42,346,396-tick current | overlap repair | change |
|---|---:|---:|---:|
| total `simTicks` | 42,346,396 | 25,381,170 | -40.06% |
| B fetch | 2,228,560 | 2,231,690 | +0.14% |
| A first issue through last response | 19,086,114 | 3,573,521 | -81.28% |
| backing first write through last ACK | 15,533,877 | 3,669,299 | -76.38% |
| consumer | 7,183,036 | 5,869,688 | -18.28% |
| response-slot HWM | 1 | 128 | +127 slots |

The candidate records 1,025 overlap resumes, zero slot-stall episodes, 143
unified-credit stall episodes covering 861 modeled cycles, and pending HWM one.
The one scan engine remains exactly 4,096 cycles.

## Storage, ownership, and closure

- shared pool capacity/HWM: 4,096/4,096 words
- line shadow: zero bytes
- source issues/responses: 1,025/1,025
- exact shared transfers/rollbacks: 16,384/0
- response word HWM: 2,048
- backing issues/ACKs: 1,357/1,357
- semantic backing bytes: 65,536
- transport backing bytes: 86,848
- pages ready: four; coherent ACK and order flags: one/one
- raw B retained, descriptor backing, replay: zero/zero/zero

Credit pressure legally invoked the existing partial-spill path: 691 full-line
writes plus 666 partial writes replaced the prior 1,024 full-line-only shape.
This increased transport by 21,312 bytes but retained exact semantic bytes,
zero line shadow, exact transfer ownership, ACK closure, correctness, and the
required material A/backing/total improvement. The initial sealer rejected this
legal shape and also rejected the faster consumer because it required a
two-sided 1% consumer band. Commit `b318d308` corrects those gate assumptions:
B remains within 1%, the consumer may improve but may not regress, and backing
must retain exact semantic/transport and ACK closure.

## Limits

This is one deterministic reduced-input observation compared with the sealed
42,346,396-tick current candidate. The integrated report correctly labels the
comparison historical and cross-binary; it is a mechanism screen, not a fresh
paired causal or full-application promotion result. No area claim is made.
