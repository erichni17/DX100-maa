# Logical stream response-path patch

Date: 2026-08-02
Patch: 3 of `logical_spd_cache_gem5_integration_plan_2026-08-02.md`
Base used for this slice: `b79f136606fddf03a93c909ed54f1f0ed836de66`

## Scope

This patch supplies the bounded response-bearing transport mechanism needed by
a later logical SPD cache scheduler. It adds no logical descriptor admission,
SPD allocation, hidden-slot mapping, indirect-producer conversion, scheduler,
or benchmark/API behavior. In particular, it is not wired to the logical
scheduler yet.

`logicalResponseManaged` is a new instruction-internal opt-in bit, false by
default. A future scheduler must set it only for one aligned 4096-element,
contiguous controller page micro-op and populate the existing logical IDs,
generation, transaction ID, page, and controller slot fields. The stream
unit then derives a full `{maa, transaction, action, logical, page,
generation, slot}` tag:

- A controller fill uses tagged `ReadReq` responses. The fixed ledger is
  completed only after every issued line has returned.
- A controller writeback still performs the stream unit's source `ReadExReq`
  step, but its written lines use `WriteReq`, are forced through the
  retirement-side cache path, and remain outstanding until their individual
  `WriteResp` callbacks.
- Every controller page has a preallocated `std::array` ledger of at most 512
  64-byte lines (4096 eight-byte elements). A 32-bit page uses exactly 256
  entries; an eight-byte page uses exactly 512. The ledger rejects duplicate
  issues and cannot grow.

The port copies the full tag into both outstanding and deferred metadata.
Exact address ordering remains, but an address never authenticates a callback:
the response sender state is cross-checked against the metadata tag, its line
address, and the active ledger before the outstanding entry is erased. Stale,
duplicate, wrong-kind, wrong-transaction, wrong-page/generation, wrong-slot,
wrong-MAA, and wrong-address callbacks are counted by the ledger and leave the
current transaction unchanged.

## Ordinary stream compatibility

The normal STREAM_LD/STREAM_ST paths are unchanged. Ordinary stream stores retain
their response-less `WritebackDirty` behavior. In particular, a normal store
still creates `WritebackDirty`, completes through `writePacketSent` on
successful send, and never receives the new sender state or retirement-cache
route. The existing transparent controller also leaves
`logicalResponseManaged` false, so its response-less completion behavior is
unchanged.

## Validation

`experiments/scripts/run_logical_stream_response_unit.sh` compiles and runs a
dependency-light C++ replay plus source-contract checks. The replay covers
delayed and reordered fills, exact final completion, duplicate responses,
wrong kind/transaction/page/slot/MAA/address responses, old-tag reuse of the
same address, and the fixed 512-line capacity. It does not run a gem5
simulation.

## Integration boundary and base status

The concurrent independent review rejected the ABI commit used as this
working base. This patch does not validate the rejected ABI base and does not
extend public ABI helpers, MMIO decoding, or logical scheduler policy. It is
intended to be cherry-picked onto a repaired ABI: the sole dependency is the
internal instruction metadata already described above. A follow-up scheduler
patch must supply transaction IDs and make its final controller completion only
after this path reports all matching write responses.
