# Hybrid SoA/JIT vector RMW

This path is a guarded ABI form of ordinary `INDIR_RMW_VECTOR`. It exists to
keep a 16,384-element logical Row/Offset window while the visible SPD remains
4,096 elements. It does not virtualize an SPD payload.

## ABI

The 64-byte instruction record is unchanged. Words 0--2 retain their existing
meaning, word 3 carries `values`, word 4 carries `indices`, and word 5 carries
an optional `uint32_t` predicate address (`0` means selected). The guarded
shape has both SPD sources absent, no condition tile, no old-value destination,
three scalar range registers, and `dst2` as a completion-only token. The CPU
port waits for word 5 only after recognizing and validating this exact shape;
all pre-existing instructions dispatch at their original terminal word.

## Bounded execution

Indices and predicates enter through cache-timed line reads and populate one
full 16K Row/Offset epoch. A capacity drain is a contract violation. Range
passes, descriptor spools, global merge, hidden payloads, and zero-time host
dereferences are excluded.

Execution owns one `SoaJitContext`, statically limited to 128 bytes. Its only
payload is one 64-byte A line; the rest is address, generation, linked-offset,
word, count, and state metadata. For each claimed A line it performs:

The initial one-context limit is deliberate. The current indirect response
router keys an outstanding read by physical cache-line address. Multiple
contexts may request values from the same line, but the router has no bounded
fan-out identity that could return one coalesced response to every owning
context. Raising the limit to eight therefore requires an explicit per-request
context/generation tag or a finite coalesced-owner list; doing so without that
identity would make response ownership ambiguous. One context preserves the
requested ordering and exact response accounting without adding a port or a
logical-window payload.

Before assigning an internal generation, decode validates the complete byte
spans for mutable A, values, indices, and the optional predicate. All spans
must fit their registered regions and be pairwise disjoint. Because response
routing uses block-aligned physical addresses, every span must also translate
to a contiguous physical routing interval and those cache-line intervals must
be pairwise disjoint. Aliases, shared cache lines, overflow, and ambiguous
physical layouts fail closed before any timed data request is issued.
This full physical-span translation prewalk is a synchronous simulator legality
check, not a modeled hardware action. Its host checking and translation cost is
not charged to simulated time and must not be interpreted as represented
latency.

1. a normal timed `ReadExReq` for A;
2. one normal timed value-line `ReadReq` per retained logical `i`;
3. ADD, MIN, or MAX in Offset-chain insertion order; and
4. a response-bearing cache-side `WriteReq` for the modified A line.

The context is reusable only after a matching `WriteResp`. Instruction
completion additionally requires an empty context, empty Offset Table, all A
rows claimed, selected plus rejected equal to the logical range, exact equality
of every issue/response counter, and a live generation. Reset repeats the
empty predicate-line and context checks.

## Evidence contract

`test_hybrid_rmw_soa.cpp` performs two generations of an exact 16K FP32 ADD,
one with an explicit predicate stream and one with the optional pointer null.
It contains duplicate indices, false predicates with large poison values, and
the order-sensitive sequence `16777216, 1, -16777216, 1`; comparison is by
floating-point bit pattern.

`run_hybrid_rmw_soa_matrix.sh` runs four arms:

- ordinary native 16K metadata / 16K physical SPD;
- ordinary native 4K metadata / 4K physical SPD, using four ordered chunks;
- SoA/JIT 16K metadata / 16K physical SPD; and
- the identical SoA/JIT binary and checkpoint with 16K metadata / 4K physical
  SPD.

Only the physical-SPD geometry differs in the final pair. Because this SoA/JIT
RMW bypasses result-SPD payload entirely, its `simTicks` ratio is a
geometry-independence and hidden-dependency check, not a paging or
virtualization-overhead measurement. Ordinary-versus-SoA comparisons are also
not mechanism-only: their API, SPD staging, and old-value-output behavior
differ. All MAA setup and array publication in this test occur after ROI entry
and the stats reset; the checkpoint contains neither a prepublished SoA stream
nor an already-configured MAA. Promotion requires exact result hashes in all
arms, two distinct terminal generations per SoA arm, and exact value/A/write
issue-response drain counters, including dedicated index and predicate line
issue/response pairs.
