# Standalone bounded logical-SPD cache core

`LogicalSPDCacheController.hh` is the finite C++ successor to the separate
one-descriptor `TransparentSPDController` slice and the two-descriptor Python
`spd_cache_state_model`. It implements only control state. It stores no page payload
and is not wired into gem5.

## Fixed resources

All capacities are C++ template parameters and all backing objects are
`std::array`s. The default point is:

| Resource | Default capacity | Stored control |
| --- | ---: | --- |
| logical descriptors | 2 | allocated bit, 32-bit generation, 4 ready bits |
| physical page slots | 2 | phase, page identity, compute/writeback serials, publish bit |
| FIFO miss entries | 4 | generation-tagged page identity |
| client leases | 4 | active bit, slot, 64-bit serial, page identity, managed-pair role |

The controller also stores one global 64-bit last-allocated transaction serial.
The default object measured 312 C++ bytes with the repository's `g++`, and
`PageIdentity` measured 8 bytes. Those `sizeof` values describe this host ABI;
they are not a synthesis or area estimate. In particular, compiler padding,
the representation of `bool`, ports, arbitration, data SRAM, ECC, and timing
closure are outside this measurement. The existing SPD hardware ledger's 4K
physical payload remains a separate allocation and must not be inferred from
the 312-byte controller object.

## Ownership and completion contract

- `allocate` returns a descriptor handle with a new nonzero generation.
  Allocation fails permanently at generation exhaustion instead of wrapping
  into an old response's identity.
- A ready event or access names one exact `(logical, page, generation)`
  identity. Every accepted fill, overwrite compute, or writeback receives a
  globally unique, nonzero 64-bit serial. A response must match its action
  kind, physical slot, page identity, and serial; reordered, duplicate, stale,
  and mismatched responses are no-ops.
- A hit reports an exact clean/dirty resident. `pin` then returns one bounded
  lease capability; dirtying and releasing require that exact active lease.
  Duplicate or forged releases do not change residency.
- A fill action owns its slot until its exact response. If the descriptor was
  freed meanwhile, the response releases the obsolete transfer but cannot
  install its payload.
- A dirty victim becomes `Writeback` only after the caller accepts the explicit
  action. It retains the slot and old generation until the exact writeback
  response; only then can the slot be reused.
- An exact page identity has at most one slot owner across filling, residency,
  and writeback. A miss replay for a page in dirty writeback may remain in the
  finite FIFO, but the FIFO head cannot issue a fill until that writeback
  completes. This deliberately allows head-of-line blocking instead of
  creating two payload owners.
- Freeing a descriptor cancels its queued misses and drops clean pages. Active
  leases make free return `Busy`; dirty, filling, and writeback slots retain
  their tagged completion obligations.

## Atomic full-overwrite pair

`reserveFullOverwrite(source, destination)` is a finite all-or-nothing API for
the first logical-ALU slice. It requires a ready resident source and an unready,
unowned destination on a different live descriptor. It chooses a distinct
empty slot, or a distinct unpinned clean victim, without queueing a destination
miss or fetching old destination bytes. It then allocates two exact managed
lease records plus unique compute and writeback serials in one mutation, and
returns both distinct slot IDs in the exact capability used for dispatch.

Any invalid, stale, one-slot-impossible, destination-unavailable, lease-full,
or serial-exhausted result leaves the source unpinned and leaves slots, ready
bits, the miss FIFO, lease records, and the serial allocator unchanged. Generic
`markDirty` and `release` reject managed pair leases, so a caller cannot split
the capability. Active pair leases also prevent either exact descriptor
generation from being freed.

The destination lifecycle is `Reserved`, `Computing`, `Dirty`, then
`Writeback`. Reserved and Computing destinations are not hit-ready residents,
victims, or candidates for another reservation. `beginOverwriteCompute`
requires the exact paired leases and compute serial. `completeOverwrite`
requires that same capability, transitions only the destination to Dirty, and
atomically releases both managed leases. `cancelOverwrite` safely discards the
tentative destination and releases both leases before issue or after the caller
has quiesced a failed compute. Duplicate, forged, canceled, and late
capabilities do not mutate state.

The writeback serial is allocated with the pair, so an accepted computation can
still drain when that allocation reaches the terminal 64-bit serial. Completion
does not mark the destination ready. The dirty slot advertises its mandatory
writeback, remains owned through exact response matching, and publishes the
page only if the same descriptor generation is still live when that response
arrives. Free/reallocation during dirty writeback therefore cannot publish an
old generation into a replacement descriptor.

Miss insertion returns `Backpressure` when the finite FIFO is full. Lease
allocation also returns `Backpressure` when all finite records are owned.
Pending memory actions are observational: a downstream refusal causes no
mutation, and the same action is presented again. Arbitration is deterministic:
the FIFO head uses the lowest empty slot, then the lowest unpinned clean slot;
if neither exists, the lowest unpinned dirty slot is written back. Obsolete
dirty pages are explicitly written back before ordinary miss service.
When the 64-bit transaction serial reaches its maximum, `pendingAction`
returns no action that would require another serial allocation. A mandatory
overwrite writeback whose exact serial was allocated with its pair may still
drain. This fail-closed policy avoids serial reuse; there is no recovery or
epoch-reset mechanism in this standalone core.

## Validation and scope

The focused C++ test covers two logical descriptors, alternate one/two-slot
configurations, FIFO pressure, hits, independent page readiness, fill and clean
victim selection, bounded leases, dirty replacement, descriptor reuse,
same-generation reordered/duplicate/late fill and writeback responses,
writeback/fill exclusion, one-slot pair impossibility, two-slot full-overwrite
success, reservation/compute cancellation, forged and late compute capability,
pair lease pressure, terminal serial allocation, and old-generation writeback
after descriptor reuse. The Python contract rejects dynamic containers or
payload buffers and checks the serial-bearing action/response surface, atomic
pair lifecycle, source coverage, runner sanitizer flags, single-owner guard,
and finite fail-closed paths. The runner executes optimized and ASan/UBSan C++
tests plus the Python/source contracts:

```sh
bash experiments/scripts/run_logical_spd_cache_controller_unit.sh
```

This is not a timing model and does not claim cache bandwidth, fairness,
coherence, performance, or integration correctness. It is also not a synthesis
or area estimate. No gem5 simulation is part of this validation.
