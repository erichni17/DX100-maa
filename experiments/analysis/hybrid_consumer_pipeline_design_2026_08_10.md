# Finite hybrid consumer-tail pipeline model

## Outcome

`HybridConsumerPipeline.hh` is a self-contained executable control model for
the most promising legal generic treatment: cache-line direct retirement for
an independent element-wise consumer. It retains the accepted 16K reorder
producer and its architectural backing writes, waits for the exact producer
write acknowledgement for each 4K page, reads acknowledged backing lines into
four finite 64-byte buffers, hands one buffer at a time to a line-fed ALU, and
holds the transformed bytes until an exact full-line destination-write ACK.

The model is deliberately not live-wired. It cannot make a performance claim.
Its timing helpers are optimistic arithmetic bounds and are separate from all
scheduler state.

## Audit boundary

The audit read, but did not modify, `TransparentSPDController.hh`, the
LogicalSPD cache controller/runtime/datapath/transport, `ALU.cc/.hh`,
`StreamAccess.cc/.hh`, and the hybrid macro-event changes at `be2faaa4`.

- `TransparentSPDController` has one STREAM lane and one ALU lane. Its 2K
  ping-pong mode can overlap an ALU action with a STREAM action in the other
  half, but fill and store still share the STREAM lane. It reserves one 4K
  input and one 4K output payload; a legal 4K double buffer would require more
  visible payload and is not modeled here.
- `LogicalSPDCacheRuntime` authenticates controller/transport completion and
  never publishes a fill or writeback before the exact action completes. Its
  current `pageCorrelation` and `computeCorrelation` rules serialize a page
  transport action against compute, so the current runtime is correctness
  substrate, not evidence of consumer overlap.
- `LogicalSPDCacheTransport` supplies the reusable finite transport shape:
  four ports, four response credits, four exact 64-byte line buffers, retry
  state, request incarnation, route token, and per-line ACK state. A live
  version of this treatment must reuse those resources; instantiating another
  transport would not be the iso-area treatment analyzed here.
- `ALUUnit` remains SPD-facing. Even controller-managed instructions read and
  write SPD and charge SPD latency. A live direct path therefore requires an
  explicit line-buffer ALU input/output handshake; setting
  `controllerManaged` alone does not implement it.
- `StreamAccessUnit` implements stores as destination-line acquisition plus a
  dirty write and retires only after all sent requests have responses. The
  direct path is legal only when the consumer overwrites every byte of an
  aligned destination line; conditional/partial stores must retain the
  read-modify-write path. No completion in the new model bypasses an ACK.
- `HybridMacroEventTracker` and the schema-1 parser now distinguish producer
  registration from operation, match four-page readiness to the producer's
  last acknowledged backing activity, require fill/ALU/store issue-completion
  closure, and report overlap, idle, retry, queue, and post-ready fields. No
  macro-profile result or raw arm directory is committed on this branch, so
  these changes define fresh evidence instrumentation, not fresh measured
  performance evidence.

The fresh corrected macro arm supplies the measured timing inputs used below:
post-ready fill 674,828 ticks, ALU 320,512 ticks, store 4,360,716 ticks, and
serialized controller actions 5,356,056 ticks. Its matched endpoints are
native16 40,062,748 ticks and hybrid 45,282,023 ticks, a 5,219,275-tick gap.
These are measured inputs to arithmetic; they are not live evidence for the
new candidate. Additive accounting is not assumed to be eliminable latency.

## Implementable scheduler contract

The scheduler owns exactly 256 bytes of line payload (`4 * 64 B`). It owns no
4K page slot, no 16K payload, and no implicit backing copy. The fixed line
phase array is control metadata; live wiring should reuse the accepted reorder
completion/line metadata rather than add a second full metadata structure.

Each line follows this finite state machine:

`producer page ACK -> read accepted -> exact read response -> ALU accepted ->
ALU completion -> full-line write accepted -> exact write ACK -> done`

Four buffers bound the sum of reads, ready values, ALU operands/results, and
writes in flight. The single ALU token prevents two compute actions. A buffer
is not recycled at ALU completion or write acceptance; only the exact write
ACK releases it. Requests carry generation, line, action, address, port, size,
and buffer identity. Stale, forged, duplicate, short, or wrong-action events
return false without advancing architectural state.

The model permits reads for any producer page whose supplied transaction was
acknowledged, so the consumer can overlap with later producer pages without
pretending that they are visible. It exposes the same address-to-port mapping
as `LogicalSPDCacheTransport`: `(address >> 6) & 3`.

Eligible operations are independent, aligned, full-line maps whose operands
fit the line-buffer contract. Reductions, cross-line dependencies, a second
vector source, conditional holes, exceptions requiring replay, and partial
destination updates are not eligible without a separately costed finite
extension.

## Required live wiring

The merge-path owner would need to implement all of the following before a
gem5 performance experiment is meaningful:

1. Route the accepted reorder producer's real per-page backing transaction and
   final write response into `notifyProducerWriteAck`; registration or issue is
   not an ACK.
2. Reuse the LogicalSPD transport's four request records/credits/line buffers
   to issue backing reads and preserve its retry, callback-port, incarnation,
   and exact response checks.
3. Add a line-fed ALU handshake that charges the existing ALU lane and performs
   the real typed operation in the owned buffer. Do not call the host-side
   standalone datapath from a timing event as a substitute for ALU work.
4. Issue aligned full-line destination writes through the real cache-side
   ports. Keep the buffer owned through the real response. Fall back to the
   current StreamAccess read-modify-write store for any partial line.
5. Retire the architectural instruction and release dependencies only after
   every line is in `Done`; preserve faults, retry, squash, and generation
   handling. Add macro fields for direct reads, ALU actions, full-line writes,
   responses, credit stalls, and overlap.

No changes to `IndirectAccess`, `MAA`, `StreamAccess`, configuration, or runner
files are part of this checkpoint.

## Timing bounds, not predictions

Using the corrected post-ready totals, perfect ALU/store overlap while fill
remains serialized can save at most 320,512 ticks. Perfect three-stage overlap
with the observed 4,360,716-tick store unchanged can still save at most 995,340
ticks. Both are below the exact 1,213,001-tick sub-10% screen. The screen is
derived in integer ticks: a strict 10% gap over native16 permits hybrid at most
44,069,022 ticks, so 45,282,023 must fall by 1,213,001 ticks. Therefore overlap
alone, without changing the store mechanism, does not clear the screen even
under a zero-startup/zero-drain bound.

Direct retirement can clear the screen only if full-line writes materially
replace the current store path. As a deliberately optimistic proxy, assigning
the candidate full-line write stage the observed 674,828-tick fill duration
gives a three-stage envelope of 674,828 ticks and a 4,681,228-tick savings
upper bound versus the 5,356,056-tick serialized chain. This is an upper bound,
not an expected speedup: it assumes perfect stage overlap, equal read/write
service, no startup/drain, no contention, no translation or IF stalls, hot
enough source locality, and no store retries. The macro-profile matrix must
measure candidate full-line write service before promotion.

The appropriate decision gate is thus: reject ALU/store overlap alone; retain
finite direct retirement as promising but unproven; require exact-output,
physical-traffic, real-ACK, and macro-event evidence from matched arms.

## Unit validation

`tests/maa/run_hybrid_consumer_pipeline_unit.sh` builds with warnings as
errors, runs optimized tests, and repeats under ASan/UBSan. Tests cover both
4-byte and 8-byte full 16K geometries, all 1,024/2,048 lines, four-credit
pressure, ALU/write overlap, buffer ownership through ACK, incremental producer
visibility, exact transaction rejection, payload retention, validation, full
retirement, and the separate timing bounds.
