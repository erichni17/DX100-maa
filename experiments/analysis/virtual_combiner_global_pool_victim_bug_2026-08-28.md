# Global-payload/local-victim combiner bug (2026-08-28)

## Finding

A legal existing virtual-gather stress case reaches:

```text
virtual combiner has no valid victim
```

The failure is not insufficient total configured payload by itself. The line
slots are set-associative, while `VirtualCombinePayloadStore` is one shared
word pool across all sets.

In `insertVirtualCombineWord`:

1. `word_capacity_full` tests the global payload pool.
2. `line_capacity_full` tests only the incoming line's indexed set.
3. Victim search scans only that indexed set.

Therefore this state is possible:

```text
global payload pool: full
incoming set:         has a free slot, no valid local line
other sets:           own all payload words
```

The insertion needs payload capacity, but local victim search finds neither a
valid line nor a target. `victim_idx` remains `-1` and the simulator panics.

## Legal design choices

### Global payload victim

When the shared payload pool is full and no local victim exists, select one
bounded global line victim, retire it coherently, release its word references,
and update the replacement pointer belonging to the victim's actual set.

This preserves shared-pool utilization but requires a global candidate search
or a bounded global victim queue. Search/arbitration timing and ports must be
charged.

### Per-set payload partition

Assign each set a fixed payload quota. A free line slot then implies enough
local payload only if line and word allocations obey the same partition.

This removes global search but can strand payload in lightly used sets and may
increase write fragmentation.

## Rejected workaround

Increasing the global word-pool size without fixing ownership is rejected. It
only moves the panic threshold and violates the fixed-storage comparison.

## Required validation

- Unit case with global pool full and incoming set empty/free.
- Target-line update while global pool is full.
- Victim in a different set with correct replacement-pointer update.
- Masked partial victim and full victim.
- Write-credit stall followed by exact retry and ACK closure.
- Equal payload capacity before and after the fix.
- Existing CG and multi-unit output/transaction ledgers unchanged.

No stress-pattern correctness or performance result is accepted until this
state is handled or the configuration is explicitly rejected before launch.
