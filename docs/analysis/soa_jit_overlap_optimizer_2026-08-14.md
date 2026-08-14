# SoA/JIT bounded overlap vertical slice

This treatment overlaps timed alias-value reads without changing the exact
FP32 update order.  It is deliberately narrower than the original full
feeder/prefetch proposal: the measured values-warm diagnostic regressed from
807,872,406 to 869,822,618 simTicks (818,330,049 for the dummy control), while
27,641 of 29,689 baseline value reads already hit in LLC.  Consequently this
commit does not enable bulk value-stream prefetch and does not claim a timed
eight-line predicate feeder.

## Executable mechanism

- Eight A-line contexts are always compiled into every indirect unit.  The
  `soa_jit_active_contexts` parameter exposes one through eight contexts.
- Every context always contains eight scalar lookahead slots.  The
  `soa_jit_value_lookahead` parameter selects one, two, four, or eight active
  credits without changing provisioned context storage.
- All alias reads share one exact generation plus physical-address owner table
  with four 64-byte line entries.  Filling aliases merge; ready aliases hit;
  only ready, waiter-free lines may be evicted by bounded LRU.  Unknown,
  stale, duplicate, and wrong-owner responses fail closed.
- Offset entries are peeked for lookahead but consumed only when the ready
  slot equals the context's ordered head.  Out-of-order memory responses
  therefore cannot reorder FP32 updates.
- The unit has one fixed round-robin apply lane.  At most one alias is applied
  per indirect unit per modeled cycle; C8 does not imply eight adders.

The CLI/config parameters are:

- `--maa_soa_jit_active_contexts={1..8}`
- `--maa_soa_jit_value_lookahead={1,2,4,8}`
- `--maa_soa_jit_value_cache_enable`

## Storage and hardware-cost ledger

Each successful SoA arm emits an exact `soa_jit_storage` trace record from the
production binary.  It contains `sizeof(SoaJitContext)`, the complete eight
context array, `sizeof(SoaJitValueCoalescer)` (four owner payloads plus fixed
tags and the eight unused prefetch-credit tags), the one-lane arbiter,
the pre-existing predicate line, and the active direct-index data/tag budget.
The reported `incremental_overlap_bytes` is the fixed context array plus value
owner/coalescer plus apply arbiter.  It is invariant across C1/C8 and L1/L8.

The direct-index feeder predates this treatment and uses dynamic C++ maps.  Its
hardware-facing ledger is conservatively charged as active lines times 16
words times `sizeof(DirectIndexWord)`; allocator and `std::map` node overhead
is simulator implementation state, not modeled hardware.  I1 versus I8 is
therefore identified as a capacity-cost treatment rather than hidden in the
overlap speedup.  The predicate feeder remains the pre-existing one line in
all measured arms.  Value-prefetch credits are zero.  The generated
`storage_ledger.txt` distinguishes these active controls from fixed provision.

## Exact evidence protocol

`experiments/scripts/run_hybrid_rmw_soa_overlap_matrix.sh` retains native16,
native4, and serial SoA physical16 controls.  Its four physical4 treatment
arms are:

1. C1/I1/L1/V0 serial control;
2. C1/I8/L4/V4;
3. C1/I8/L8/V4;
4. C8/I8/L8/V4.

Every SoA command passes contexts and lookahead explicitly.  The gate requires
the exact duplicate/order-sensitive output hash, normal ROI and `m5_exit`, two
distinct terminal generations, predicate/A/value/write issue-response
closure, alias issue-delivery-apply closure, and fixed occupancy bounds.  It
records source commit, production gem5, guest, and resolved config hashes.
Only `simTicks` from completed runs are performance measurements.

The standalone unit test additionally covers eight contexts sharing one
line, a fifth miss behind four fills, ready hits, fill merges, LRU eviction,
reordered responses, retry, one unit-wide delivery per cycle, stale/unknown
responses, shared prefetch ownership, and fixed predicate bounds.

## Deliberate follow-ups

The standalone owner state includes fixed payload-free prefetch tags so a
future sequential prefetch implementation cannot send a duplicate read for an
alias-owned physical line.  That prefetcher and the fixed eight-line predicate
feeder are not timed-integrated here.  If the measured V4 `value_stalls` and
`lookahead_stalls` show that four owner lines cap useful overlap, the next
treatment should provision one fixed 16-line maximum and sweep active 4/8/16
credits; it must not introduce operation-sized or unbounded state.
