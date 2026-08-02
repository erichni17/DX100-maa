# Bounded SPD-cache State Model

`spd_cache_state_model.py` is an executable exhaustive model of a two-logical-tile cache over one physical SPD slot.  Each logical tile has two pages and a descriptor generation counter bounded at two allocations.  The slot and its finite miss queue are tagged by `(tile, page, generation)`.

Run the bounded check with:

```bash
python3 experiments/analysis/spd_cache_state_model.py --depth 10
python3 -m unittest experiments/tests/test_spd_cache_state_model.py
```

The explored transitions include allocate/generation, backing ACK and ready, miss queueing, fill and fill responses, pin/read, dirty write, release, clean or dirty eviction, writeback acknowledgement, descriptor free/reuse, and stale fill/writeback responses.

For every transition reached through the selected depth, the model asserts that only one page can own the one physical slot, a dirty resident becomes a tagged writeback before it can disappear, a pinned resident cannot be evicted, and `ready` implies a matching backing acknowledgement.  Fill installation additionally requires the descriptor's live generation, so a late fill after free/reuse releases its obsolete transfer rather than installing old data.  Late/repeated writeback acknowledgements do not disturb a newer fill.  For every reachable state with no held client pin, the model also searches a finite drain path that supplies all pending memory responses; it rejects a terminal memory deadlock.

This proves properties of this deliberately small transition system, not of gem5 or a complete hardware implementation.  It abstracts timing, bandwidth, response reordering beyond tagged stale responses, multi-slot replacement policy, data values/coherence, client scheduling fairness, and unbounded generations.  The liveness assertion is conditional: client pins are eventually released and memory eventually supplies each accepted fill/writeback response.
