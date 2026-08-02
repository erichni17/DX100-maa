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
| physical page slots | 2 | phase and generation-tagged page identity |
| FIFO miss entries | 4 | generation-tagged page identity |
| client leases | 4 | active bit, slot, 64-bit serial, page identity |

The default object measured 184 C++ bytes with the repository's `g++`, and
`PageIdentity` measured 8 bytes. Those `sizeof` values describe this host ABI;
they are not a synthesis or area estimate. In particular, compiler padding,
the representation of `bool`, ports, arbitration, data SRAM, ECC, and timing
closure are outside this measurement. The existing SPD hardware ledger's 4K
physical payload remains a separate allocation and must not be inferred from
the 184-byte controller object.

## Ownership and completion contract

- `allocate` returns a descriptor handle with a new nonzero generation.
  Allocation fails permanently at generation exhaustion instead of wrapping
  into an old response's identity.
- A ready event, access, fill response, and writeback response names one exact
  `(logical, page, generation)` identity. Stale events are no-ops.
- A hit reports an exact clean/dirty resident. `pin` then returns one bounded
  lease capability; dirtying and releasing require that exact active lease.
  Duplicate or forged releases do not change residency.
- A fill action owns its slot until its exact response. If the descriptor was
  freed meanwhile, the response releases the obsolete transfer but cannot
  install its payload.
- A dirty victim becomes `Writeback` only after the caller accepts the explicit
  action. It retains the slot and old generation until the exact writeback
  response; only then can the slot be reused.
- Freeing a descriptor cancels its queued misses and drops clean pages. Active
  leases make free return `Busy`; dirty, filling, and writeback slots retain
  their tagged completion obligations.

Miss insertion returns `Backpressure` when the finite FIFO is full. Lease
allocation also returns `Backpressure` when all finite records are owned.
Pending memory actions are observational: a downstream refusal causes no
mutation, and the same action is presented again. Arbitration is deterministic:
the FIFO head uses the lowest empty slot, then the lowest unpinned clean slot;
if neither exists, the lowest unpinned dirty slot is written back. Obsolete
dirty pages are explicitly written back before ordinary miss service.

## Validation and scope

The focused C++ test covers two logical descriptors, alternate one/two-slot
configurations, FIFO pressure, hits, independent page readiness, fill and clean
victim selection, bounded leases, dirty replacement, descriptor reuse, and
stale fill/writeback/ready/release inputs. The Python contract rejects dynamic
containers or payload buffers and checks the external generation/completion
surface. Run both with:

```sh
bash experiments/scripts/run_logical_spd_cache_controller_unit.sh
```

This is not a timing model and does not claim cache bandwidth, fairness,
coherence, performance, or integration correctness. It is also not a synthesis
or area estimate. No gem5 simulation is part of this validation.
