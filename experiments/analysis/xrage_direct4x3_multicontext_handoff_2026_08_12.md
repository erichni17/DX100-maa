# XRAGE direct4x3 bounded multi-context line handoff

## Decision

The 12 August direct4x3 campaign validates the current direct-retirement
datapath and exposes its steady-state limit. The smallest bounded corrective
prototype is HybridConsumerContextQueue: four fixed consumer contexts, each
containing the existing finite line scheduler, with one globally owned ALU and
the existing cache ports. This is an executable state model and unit-tested
integration contract, not a live gem5 performance result.

The queue has no map, heap queue, page payload, producer payload copy, or
additional cache/ALU port. Context lookup is a linear scan over exactly four
entries.

## Evidence and diagnosis

Campaign:
"/data1/nier/dx100-runs/2026-08-12-xrage-direct-x3-line-handoff-64k-52ba9e98"

Both arms used source commit 8a5c771263a5ffee4c2df1ea0ad594aed8d3c73e,
the same direct4x3 input, and 16K logical / 4K physical geometry. Their
checkpoint and restore gem5 stages both returned zero. The page-gated control
also passed its wrapper and wrote exact output hash 5576400619275092867 with
ROI simTicks=105258457.

The line treatment's wrapper intentionally failed its line-only closure
check. Its raw stats still prove correctness and mechanism accounting:

| Counter | Value |
| --- | ---: |
| Direct descriptors | 4 |
| Expected direct lines | 8,192 |
| Exact line acknowledgements | 2,048 |
| Page-fallback lines | 6,144 |
| Read / ALU / write responses | 8,192 / 8,192 / 8,192 |
| Fallbacks | 0 |

Thus descriptor 1 received all of its 2,048 exact line handoffs, while
descriptors 2--4 each waited for their four final page acknowledgements. This
is not a producer-write correctness failure. In MAA, one
HybridConsumerPipeline directRetirement and one DirectRetirementExecution
directRetirementExecution represent only one active owner. While that owner
is active, submitDirectRetirementDescriptor returns false. Later descriptors
cannot retain their own producer token/generation context, so when they
finally admit, their pages are already ready and the conservative page
fallback exposes their lines.

No throughput or speedup claim follows from this incomplete line treatment.

## Executable bounded model

HybridConsumerContextQueue.hh implements:

- Capacity exactly four, matching direct4x3's four simultaneous descriptors.
- One HybridConsumerPipeline per context. Each preserves its existing 16 fixed
  64-byte line buffers and its exact producer/read/ALU/write state.
- An owner key of (tokenTile, generation, incarnation). Four distinct token
  tiles may concurrently be at generation 1. The admission-assigned
  incarnation rejects stale events after a slot is retired and reused, even if
  an upstream bug reuses a token/generation pair.
- A single computeInFlight token. The queue never permits two contexts to
  accept ALU work concurrently.
- Round-robin read, write, and compute selection across the four contexts. One
  read or write request is offered at a time; its normal four-port value stays
  in the inner request and must be sent through existing cache-port arbitration.

The test proves all of the following fail closed without changing state:
wrong token tile, wrong generation, wrong incarnation, duplicate active
generation, stale response owner, and producer traffic after context
retirement. It also proves four contexts can retain independent visibility
and that only one context can own the ALU.

## Persistent storage charge

All values below are exact C++ object sizes for the unit-test ABI
(g++, this source layout), and are enforced by charge helpers in the model.

| Persistent state | Bytes |
| --- | ---: |
| Four 16 x 64-byte line-buffer payload arrays | 4,096 |
| Four existing pipeline control regions (4 x 4,352) | 17,408 |
| Four context owner records plus required alignment (4 x 64) | 256 |
| Shared ALU owner, flag, and object tail alignment | 64 |
| **Total bounded queue state** | **21,824** |

The queue-specific control is 320 bytes; it consists only of active bits,
four 64-bit incarnations, four 24-byte owner keys, one 24-byte ALU-owner key,
and ABI alignment. The 4,096-byte payload is all line data. The remaining
17,408 bytes are existing per-pipeline control: descriptors, page/word
readiness, line phases, buffer state, counters, and padding. There is no
hidden page/16K payload and no unbounded container.

A live bridge must replace, rather than add beside, the current single
directRetirement/directRetirementExecution fields. Four current
DirectRetirementExecution records would add 1,728 bytes (4 x 432) for their
instruction identity, retry pointer, and macro record; the four cache ports
and their existing response/request credits must be reused, not replicated.
The existing unbounded directRetirementOutstandingAddresses set is not an
acceptable part of the multi-context design: replace it with exact ownership
in fixed cache-port request records and sender state.

## Precise live integration plan

1. Replace the single pipeline/execution pair in MAA.hh with the four-context
   queue and four fixed execution records indexed by queue admission.
   Admission returns the full owner key; copy it into every sender state and
   ALU callback.
2. In submitDirectRetirementDescriptor, admit all four eligible descriptors
   while retaining their own completion tile, producer token, address/range,
   scalar, and macro tracker. Keep the current transparent fallback for
   ineligible descriptors and full-queue backpressure.
3. In setVirtualLineWordsReady and setVirtualPageReady, construct the owner
   key from the producer token and generation, then deliver the exact
   acknowledgement only to the matching context. A missing key, changed
   generation, duplicate line word, or late response must panic/fail closed;
   never route by generation alone.
4. Replace the single serviceDirectRetirement selection with queue
   pendingWrite, pendingCompute, and pendingRead arbitration. Use the same
   aluUnitsIdle[maaID] and cache-side ports; queue compute acceptance gives
   the additional cross-context ALU guard.
5. Extend DirectRetirementSenderState and the ALU completion token with the
   owner key. Validate it before mutating the selected pipeline, and release
   a context only after every destination WriteResp, no retry packet, and its
   macro tracker is terminal.
6. Replace the direct outstanding-address set with a fixed cache-port record
   keyed by the full owner/request identity. A retry must retain that identity
   and a returned response must clear exactly that record.
7. Add a small gem5 direct4x3 trace test first. Require 4 descriptors, 8,192
   line acknowledgements, zero page-fallback lines, 8,192 exact
   read/ALU/write closures, and the existing exact output hash before any
   performance run.

## Recommended next experiment

After live wiring and the trace test, rerun one matched direct4x3 64K
page-versus-line pair from the frozen campaign checkpoint/input. Do not call
it a performance promotion unless both arms have terminal exits, matching
output hash, 8,192 exact request closures, and the line arm has exactly 8,192
line acknowledgements with zero page fallback. Only then compare ROI
simTicks; no large sweep is justified before that mechanism gate passes.
