# XRAGE bounded request-latency overlap

## Decision

The bounded logical-16K / physical-4K XRAGE design now beats the original
native-4K DX100 on the frozen `C[i] = 3 * A[B[i]]` workload. Its first-ROI
latency is 41,547,933 simTicks versus 51,676,926 for native 4K, a 24.379%
throughput improvement. It is also 1.840% faster than native 16K on this one
workload.

This is an end-to-end design result, not a pure virtualization speedup. The
candidate combines a 16K Row/Offset reorder window, a 4K physical SPD,
direct-index feeding, fused multiply/retirement, result-SPD bypass, early
producer-line handoff, four bounded consumer contexts, and finite response
storage. Native DX100 executes the original stream-read, indirect-read,
scalar-ALU, and stream-store sequence. The strongest isolated overlap result
is the matched page-ready versus line-ready comparison: line handoff improves
throughput by 8.179%.

The implementation still retains logical-16K Row/Offset metadata. It is not a
fully bounded 4K reorder engine and does not solve 64K-over-16K descriptor
virtualization.

## Mechanism

1. A bounded 128-cache-line (8 KiB) feeder reads sequential `B[i]` values.
2. The existing logical-16K Row/Offset engine sees the full tile and reorders
   the resulting `A[B[i]]` requests. The 4K SPD limit therefore does not reduce
   the source reorder window.
3. Four fixed consumer contexts retain exact `(token, generation,
   incarnation)` ownership. Each has sixteen 64-byte line credits; all
   contexts share one ALU and the existing four cache ports.
4. A producer line becomes visible only after its exact backing `WriteResp`.
   A compact 1,696-byte ledger preserves acknowledgements that arrive before
   the matching consumer descriptor is admitted. No speculative visibility is
   created.
5. Ready lines can be read, multiplied by three, and written to dense `C`
   while later producer lines are still completing. Four fixed retry slots
   prevent a blocked cache port from creating head-of-line blocking on the
   other ports.
6. Completion is exposed only after all exact destination `WriteResp`s. Any
   unsupported shape uses the conservative fallback rather than silently
   entering this path.

This path removes the native result-SPD and separate stream-store phase for
the frozen XRAGE expression, but it still materializes producer backing lines
before the consumer reads them. The direct-payload prototype that would also
remove that producer write/read pair remains a unit-tested legality contract,
not the live gem5 path measured here.

## Performance

All values below are first-ROI `simTicks`. Each arm was run twice; replicas
were bit-exact and had identical simTicks.

| Arm | Logical / physical | simTicks | Throughput vs. native 4K |
|---|---:|---:|---:|
| Native 16K | 16K / 16K | 42,312,279 | +22.132% |
| Native 4K | 4K / 4K | 51,676,926 | baseline |
| Page-ready, feeder 128, pool 1024 | 16K / 4K | 44,946,174 | +14.975% |
| Line-ready, feeder 1, pool 1024 | 16K / 4K | 100,785,374 | -48.726% |
| Line-ready, feeder 128, pool 480 | 16K / 4K | 42,098,813 | +22.752% |
| Line-ready, feeder 128, pool 1024 | 16K / 4K | 41,547,933 | +24.379% |

Matched treatment pairs isolate these effects:

| Treatment | Reference -> candidate | Latency change | Throughput change |
|---|---|---:|---:|
| Early line handoff | page-ready -> line-ready | -7.561% | +8.179% |
| B-feeder read-ahead | 1 line -> 128 lines | -58.776% | +142.576% |
| Response word pool | 480 -> 1024 words | -1.309% | +1.326% |

The page/line pair changes only readiness granularity. The feeder and pool
pairs likewise change only their named finite capacity. Native-versus-direct
rows are architecture comparisons and cannot attribute their difference to
virtualization alone.

The feeder-depth result identifies the dominant measured blocker in the old
line-ready design: one-line sequential-B feeding serialized index request
latency and expanded indirect fill cycles from 28,085 to 216,575. Increasing
the bounded feeder to 128 lines restores overlap without changing the ordered
source-request digest. Early line readiness then removes the page-tail wait;
the response-pool increase contributes a smaller final gain.

## Correctness and closure

Every one of the 12 performance runs passed these common gates:

- exact 65,536-element output hash `5576400619275092867`;
- checkpoint and restore exit status zero, terminal `m5_exit`, and two stats
  blocks;
- frozen simulator, guest, input, Ramulator, and config-tree hashes.

The four native controls correctly recorded zero direct-path activity. Both
page-ready direct runs recorded four descriptors, context high-water four, 16
page acknowledgements, 8,192 page-fallback lines, and zero line
acknowledgements. All six line-ready direct runs instead recorded 8,192 exact
line acknowledgements and zero page-fallback lines. Every direct run closed
8,192 read issues/responses, ALU issues/completions, and destination write
issues/responses, with zero mechanism fallback or early-ledger overflow.

A successor debug pair compared feeder depth 1 with feeder depth 128 using the
same final binary and configuration. All four logical instructions and all
8,638 issued source requests had strict per-instruction ordered digest matches.
This proves that read-ahead changed timing, not the ordered source-request
stream inside an instruction. It does not prove identical global interleaving
or completion order across concurrent instructions.

The original campaign aggregation terminated with status 1 because the frozen
analyzer incorrectly classified `gem5.provenance.txt` as a second gem5 binary.
No simulation failed. Commit `3ecf9568` restricts binary discovery to actual
`gem5.opt`, `gem5.fast`, and `gem5.debug` artifacts. The recovered comparison
was written to a new directory without changing the frozen campaign.

## Storage accounting

This is a capacity ledger, not a synthesized area/power result.

| Item | Capacity |
|---|---:|
| Native SPD payload | 2.00 MiB |
| Physical SPD payload | 512.00 KiB |
| B feeder + source responses + destination combiner | 40.00 KiB |
| Direct-handoff hardware lower bound | 10.25 KiB |
| Direct-handoff conservative C++ static view | 30.28 KiB |
| Retained configured Row/Offset/invalidator lower bound | 248.50 KiB |
| Native comparable lower bound | 2.31 MiB |
| Candidate comparable lower bound | 835.27 KiB |

The comparable lower bound is 64.615% smaller than native. Counting every
Row-Table organization that gem5 allocates, rather than only the active one,
reduces the modeled lower-bound saving to 51.651%. The candidate retains a
16,384-entry logical Row/Offset domain and therefore pays more than a true-4K
row-table design. Ports, arbitration, wiring, memory periphery, STL/allocator
overhead, and physical SRAM implementation are not included.

## Provenance

- Simulator source: `1ed89831b447758f7e29dc20630f8aac02335ef9`
- Analysis source: `3ecf9568685ecbf542246667b306bad6afca13dc`
- gem5 SHA-256:
  `b5674e3bb886d892ddbbadb7cdb6d6332ef327c432f9a38bc16a93a6adec935a`
- Input SHA-256:
  `70e3d82973d7a93300db950d2c81e9db5b6a37273b0f21da8344302ce53022d9`
- Performance campaign:
  `/data1/nier/dx100-runs/2026-08-13-xrage-integrated-overlap-4d46c271`
- Recovered aggregate:
  `comparison-recovered-3ecf9568`
- Storage ledger: `storage-3ecf9568`
- Digest successor:
  `/data1/nier/dx100-runs/2026-08-13-xrage-final-digest-3ecf9568`
- Digest evidence seal SHA-256:
  `54970285f2a909c6c1466c3f5f3ba646f15ffbd959d5af0b11b88e4524941b5c`
- Final-test successor:
  `/data1/nier/dx100-runs/2026-08-13-xrage-final-tests-68dca851`
- Final-test seal SHA-256:
  `6f62f116a66a2886d9b15c202284ad8eb388b08f6007ca0865042b5943a0dac6`
- Post-run raw-evidence seal:
  `/data1/nier/dx100-runs/2026-08-13-xrage-postrun-seal-68dca851`
- Post-run seal SHA-256:
  `ef284c3d21f8575cce2e3f490e48f730adf8627e00826859b69856b92f56aaf1`

Final verification at report commit `68dca851` passed seven bounded C++ unit
suites, including sanitizer variants, and 111 Python experiment tests. Their
logs, exit codes, source status, and runner are included in the sealed
final-test successor. A separate atomic post-run manifest seals 200 unique raw
campaign, comparison, storage, digest, and test-evidence files.

An independent read-only reviewer recalculated all four reported throughput
deltas and validated the storage and evidence seals. It accepted the three
controlled one-knob observations and accepted 24.379% only under the mixed
end-to-end qualification used in this report. Its handoff is recorded under
`xrage-promotion-review-20260813-023425-12406921`.

## Remaining limits

- Generalization is unproven. The live fused retirement path is specialized to
  exact 16K FP64 gather, multiply by 3, and dense store.
- A true 4K Row/Offset engine with LLC-backed merging remains open.
- The storage ledger must be followed by RTL SRAM/control synthesis before an
  area or power claim.
- Additional workloads need native and fused semantic controls before this
  result can support a broad architecture claim.
