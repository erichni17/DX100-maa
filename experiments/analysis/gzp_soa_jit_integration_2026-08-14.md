# GZP SoA/JIT RMW integration handoff — 2026-08-14

## Decision

The narrowest fair end-to-end GZP treatment is to replace **both** full-window
page-local RMW sequences with two guarded SoA/JIT RMWs, while leaving the
576-element final tail on the existing ordinary path.  Do not add an
RMW/gather/ALU fusion for the first experiment.

For each full 16,384-corner window:

1. use the existing `c_to_p_map` as both RMWs' `index[]`;
2. use the existing `corner_volume` as the `point_volume` RMW's `value[]`;
3. publish the four physical `tileCond` pages into one per-core 16K
   `uint32_t` predicate buffer;
4. publish the four physical `tile2` pages into one per-core 16K FP32
   gradient-value buffer;
5. wait for exact, response-acknowledged publication of both buffers;
6. issue the `point_volume` SoA/JIT ADD, then the `point_gradient` SoA/JIT ADD;
7. wait for both completion tokens before either buffer is reused and before
   the normalization barrier.

This keeps the current condition, gather, `csurf * zone_field` multiply, full
16K alias ordering, address-region exclusion, and exact FP32 result.  It moves
the index and volume-value fetches from 4K SPD stream loads to timed SoA/JIT
cache requests, and it charges the otherwise missing predicate/product
publication and reread traffic.  There is no operation-sized private payload:
the RMW holds one bounded A-line context, while the two logical source arrays
live in ordinary registered memory.

The integration is not ready to implement until an ACKed SPD-to-memory
publisher exists.  Ordinary `maa_stream_store` is not that publisher.

## Audited source and evidence boundary

The repository handoff is based on source commit
`c579c6b8` (`analysis: require backed RMW attribution controls`).  The guarded
SoA/JIT implementation was separately inspected in the active recovery
worktree based on `0d507155`; it was dirty and therefore is not provenance for
a result.  The inspected files included these exact snapshots:

- `MAA_gem5.hpp` SHA-256
  `263647c3ed32d0493c8b9ac5c3b2bdc14f7dffd359852463d029c0fcbf4b324b`;
- `CpuSidePort.cc` SHA-256
  `beafdb7b79d6bab3500e3ae2c30bb2967bec7789373429666f48d60af60ef3b9`;
- `IF.hh` / `IF.cc` SHA-256
  `fc7cb3e9d2dac01d9864aafa5ca7b5455d0283bc6b722156dd6d70efdbb9fef8` /
  `85776ba731cd52301131858613546d014d0a39803e8d3776bbe8de520231bdf7`;
- `IndirectAccess.hh` / `IndirectAccess.cc` SHA-256
  `356acd543b0f36b1aa0ddfe7b2131ee238301c83e54ba3c687ad658c6face06d` /
  `4cca4a7ca892c59736d9d1763a48c0f175133ba2c3579cc4e63b53aac0965fe5`.

No live GZP result exists for this treatment.  The inspected SoA/JIT
microbenchmark and four-arm shell runner are functional scaffolding only and
were also uncommitted.  Their matrix compares ordinary native16, ordinary
native4, SoA metadata16/physical16, and SoA metadata16/physical4; it does not
replace the required GZP native16/native4/current-hybrid/new-hybrid matrix.

Three published coordination checkpoints bound, but do not replace, this
handoff.  `f02c66be17a1` adds a full-scope backed vector-RMW oracle,
`905619ab514c` restores its explicit runner matrix checkpoint, and
`b9a74c0f4a9e` flushes its correctness markers.  Reuse that oracle's
duplicate/predicate/range cases and marker discipline where the guarded ABI
agrees, but do not treat its backed/AoS mechanism as GZP SoA/JIT performance
evidence.
Checkpoint `90b4fd59cfea` proposes eight A-line contexts plus a four-line value
cache (1,250 bytes per indirect unit) to overlap JIT latency.  That is a
separately costed successor: the narrow primary integration pins the inspected
single, at-most-128-byte A-line context so context/cache expansion cannot be
silently mixed into the GZP treatment.

The frozen current GZP attribution is
`/data1/nier/dx100-runs/2026-08-14-general-hybrid-gzp-tailfix-lean-r1152p2304-c512w16-b8-wpc4-901daab8-r1`.
Its successor analysis reports exact correctness key
`11225737641199706160`, zero nonfinite values, zero point-volume and
point-gradient errors, and 1,180,000 checked elements in all arms.  The
selected ticks are:

| arm | `simTicks` | `numInst_INDRMW` | `cycles_INDRMW` |
|---|---:|---:|---:|
| native16 | 5,826,750,095 | 124 | 11,124,864 |
| native4 | 7,636,382,131 | 490 | 15,258,781 |
| current hybrid token materializer | 7,351,221,603 | 490 | 15,153,010 |

The hybrid/native16 MAA-cycle gap is 4,870,516 cycles; the RMW-cycle delta is
4,028,146 cycles, or 82.7047% of the gap.  This makes the two RMWs the right
target, but the matrix has one replica, profile-specific checkpoints, and a
stale top-level `campaign.exit=1`.  Its numbers are attribution, not promotion
evidence.

The exact-commit retention campaign
`/data1/nier/dx100-runs/2026-08-14-general-hybrid-gzp-masked-grayhash-47f4260c-r1`
has `campaign.exit=0` and exact terminal correctness.  Eliminating all 62,464
materializer backing-read fallbacks was 0.0552% slower, while leaving 490 RMWs
unchanged.  It supports changing the RMW organization, not another gather
retention treatment.

## Existing GZP values and the missing publications

The current MAA kernel registers its memory at
`benchmarks/UME/gradzatp.cpp:258-278` and computes each physical page at
`:343-404`.

| SoA/JIT role | GZP source | Already a stable memory array? | Action |
|---|---|---:|---|
| volume A | `point_volume` | yes, registered | use directly |
| gradient A | `point_gradient` | yes, registered | use directly |
| shared index | `c_to_p_map` | yes, registered | pass `c_to_p_map.data() + c` with local range `0..16384` |
| volume value | `corner_volume` | yes, registered and immutable | pass `corner_volume.data() + c` directly |
| active predicate | `tileCond = (corner_type >= 1)` | **no**; only a 4K SPD result | publish four pages as exact `uint32_t` 0/1 words |
| gradient value | `tile2 = csurf * gathered zone_field` | **no**; only a 4K SPD result | publish four pages as exact FP32 products |

`corner_type` is not a legal substitute for `predicate[]`.  The guarded ABI
uses `uint32_t != 0` as true, whereas GZP initializes inactive corners to `-1`.
Reinterpreting `-1` as `uint32_t` selects the lane and corrupts both outputs.
The published predicate must contain the exact GTE result, normally 0 or 1.

The gradient value must be the bit pattern produced by the existing FP32
`MUL_OP` at `gradzatp.cpp:398-399`.  Recomputing it on the CPU, precomputing it
before the ROI/checkpoint, or replacing it with a fused mathematical
expression changes the measured workload.  A timed publish followed by the
SoA/JIT timed read preserves the current multiply and charges the data
movement.

The two added per-core buffers are each 16,384 words or 64 KiB.  Four owners
therefore add 512 KiB of ordinary external memory:

```text
predicate publication = 4 cores * 16,384 * 4 B = 256 KiB
gradient publication  = 4 cores * 16,384 * 4 B = 256 KiB
total external source storage                     = 512 KiB
```

They are not hidden hardware storage.  Each per-core subarray must be its own
registered region so owners can publish and consume independently.  The
current seven fixed MAA regions plus nine GZP regions plus four per-core
virtual-gather backing regions use 20 registrations.  Eight new per-core
regions bring the total to 28, within `MAX_CMD_REGIONS == 32` in
`src/mem/packet.hh`, but the implementation must assert the configured limit
rather than rely on this count.

## Required publication and visibility contract

The current stream store cannot establish source visibility:

- `StreamAccess.cc:492-540` reads the old line, overlays SPD words, and emits
  `MemCmd::WritebackDirty`;
- `StreamAccess.cc:447-459` increments its response/completion count when the
  write packet is accepted by the transport; and
- `StreamAccess.cc:386-415` can finish the instruction and release its source
  tile at that acceptance boundary.

Consequently, `wait_ready(source_tile)`, IF retirement, a CPU `mfence`, or
registering the destination range does not prove that a later cache-timed
SoA/JIT read sees the published bytes.  The guarded RMW's IF record captures
the value/index/predicate region bounds, but its only access permit is the A
range `addrRangeID`; there is no dependency edge from an earlier store into
`backingAddrRangeID`, `indexAddrRangeID`, or `predicateAddrRangeID`.

Add a narrowly guarded response-bearing SPD-to-memory publisher, or an
equivalent existing controller-managed action, with this contract:

1. input is one physical 4K SPD tile, one destination base/range, and one
   completion-only token;
2. every emitted line is a timed cache-side `WriteReq`, owns its copied packet
   payload across retry, and has exact owner, generation, page, line, and
   physical-address identity;
3. a bounded credit table, sized to transport concurrency rather than 4K
   elements, owns all outstanding line writes;
4. SPD source reuse may follow packet payload capture, but publication is not
   visible and the completion token is not ready until every authenticated
   `WriteResp` has arrived;
5. duplicate, stale, wrong-address, and wrong-generation responses fail
   closed; and
6. the token becomes ready only after all four pages of that logical buffer
   close, or the guest explicitly waits on four page tokens before RMW submit.

For one full window the two publications write 128 KiB, or 2,048 cache lines.
Across 61 full windows they issue 124,928 response-bearing line writes
(7,995,392 bytes): 62,464 predicate lines and 62,464 gradient lines.  These
counts must appear as mechanism evidence.

The simplest safe schedule waits for both publication tokens before writing
the SoA/JIT instruction words.  The helper's final `mfence` then orders the
instruction MMIO record after that software wait.  Do not infer visibility
from region registration.  Do not overwrite either per-core buffer until both
RMW completion tokens for that window are ready, because values are fetched
just in time after Row/Offset reordering.

## Guarded SoA/JIT and IF contract

The inspected ABI encodes the existing `INDIR_RMW_VECTOR` opcode in a guarded
shape:

- word 2: A base;
- word 3: `value[]` base;
- word 4: `index[]` base;
- word 5: optional `uint32_t predicate[]` base, with zero meaning no predicate;
- three source registers: min, max, positive stride;
- no SPD sources, no condition tile, and no register destinations;
- `tdst1` absent, so old-A results are unsupported; and
- `tdst2` is a completion-only token.

`CpuSidePort` waits through word 5 only after recognizing this exact shape and
resolves A/value/index/predicate to registered half-open ranges.  `IF` marks
`tdst2` completion-only, applies normal tile hazards to it, and does not permit
it to be consumed as SPD data.  The indirect unit fetches indices and
predicates through timed cache requests, fills one full 16K Row/Offset epoch,
then holds one no-larger-than-128-byte context containing one 64-byte A line
while it fetches each selected value.  A modified A line uses a
response-bearing `WriteReq`; the context and instruction survive until the
matching `WriteResp`.

Terminal completion must continue to require:

- all logical lanes classified exactly once;
- all Row entries claimed and Offset occupancy zero;
- selected + rejected equals the logical range length;
- predicate, A-read, value-read, and A-write issue/response equality;
- value reads and applied aliases equal selected lanes;
- all contexts empty and the generation live; and
- the last A `WriteResp` received before IF finishes and makes `tdst2` ready.

The guarded form must reject an old-value destination rather than silently
returning a zero-sized tile.  It must also reject overlap between A's byte
range and any index/value/predicate byte range.  Ordinary RMW reads its SPD
operands before modifying A; a JIT value read from an aliasing A region would
otherwise observe earlier updates and change snapshot semantics.  GZP's
vectors are disjoint, so fail-closed disjointness is the narrow solution.

## Exact predicate and alias semantics

For a call with local logical lane `i` and source position
`s = min + i * stride`:

```text
selected(i) = predicate == null || uint32(predicate[s]) != 0
if selected(i): A[index[s]] = FP32_ADD(A[index[s]], value[s])
```

False lanes are classified once before Row/Offset admission, generate no A
access and no value read, and cannot affect an old-value destination because
that destination is forbidden.

For every selected duplicate that resolves to the same A word, apply values
in increasing logical-lane insertion order across all four pages:

```text
page 0 lanes 0..4095,
then page 1 lanes 4096..8191,
then page 2 lanes 8192..12287,
then page 3 lanes 12288..16383.
```

Row/DRAM order may differ between distinct A lines because those updates do
not alias.  It may not reorder occurrences within one A-word Offset chain.
The local `0..16384` range is equivalent to the current global `c..c+16384`
range because `index`, direct volume values, and the two publication bases are
all advanced to the current window before submission.

Across owners and windows, retain the existing write-region permit and atomic
RMW serialization for duplicate point indices.  A permit must remain held
until the exact final A `WriteResp`, not merely until its write is issued or
accepted.  Each per-core source buffer remains immutable from publication
completion through both RMW completions.  Volume and gradient target disjoint
arrays and may be independently ordered architecturally; issue volume then
gradient for a deterministic implementation and to match the source program.

## Exact guest schedule

For each `gather_size == 16384` iteration of the existing general-hybrid loop:

1. Start the existing direct-index virtual `zone_field` gather.
2. For each 4K page, keep the `corner_type` stream load and GTE operation.
   Publish `tileCond` to the page of this owner's predicate buffer.
3. Remove the now-unused 4K `c_to_p_map` stream load: the SoA/JIT operation
   fetches `c_to_p_map + c` through its timed direct-index path.
4. Remove the now-unused 4K `corner_volume` stream load: the volume SoA/JIT
   operation fetches `corner_volume + c` through its timed value path.
5. Keep the `csurf` load, virtual-gather materialization, and FP32 multiply.
   Publish `tile2` to the page of this owner's gradient-value buffer.
6. Preserve the existing gather consumer end/drain.
7. Wait for the predicate and gradient publisher completion tokens.
8. Set local range registers to `0, 16384, 1`.  Submit
   `point_volume, c_to_p_map+c, corner_volume+c, predicate_buffer` and wait for
   its completion token.
9. Submit
   `point_gradient, c_to_p_map+c, gradient_buffer, predicate_buffer` and wait
   for its completion token.
10. Reuse the buffers only after step 9.

Keeping separate response-bearing predicate and gradient publishers replaces
the two removed ordinary loads with two charged stores for each full page.  A
useful predicted instruction signature is therefore:

```text
full pages                        = 61 * 4 = 244
new publisher instructions       = 244 * 2 = 488
new full-window SoA/JIT RMWs      = 61 * 2 = 122
ordinary tail RMWs                = 2
new total RMW instructions        = 124
expected ordinary STRRD           = 244 * 2 + 5-tail = 493
expected response-bearing STRWR   = 244 * 2          = 488
expected ordinary stream total    = 981
```

The `STRRD/STRWR` split assumes each publisher is exposed as one stream-like
instruction per physical page and the token materializer remains accounted as
in the current runner.  If implementation accounting differs, predict and
explain it before examining performance.  Do not retain dead map/volume loads
merely to force these counts.

For `gather_size != 16384`, retain lines 339-341 and the current ordinary page
path, including its two RMWs.  This preserves the legal materializer tail and
avoids weakening the SoA/JIT full-window contract for only 576 lanes.

At `gradzatp.cpp:465-468`, wait for every owner's last volume and gradient
completion tokens, then execute the existing OpenMP barrier.  Only after the
barrier may CPU normalization read `point_volume` or modify
`point_gradient`.

## Why fusion is not the first integration

Fusion is not required for correctness or for a fair first measurement.  The
predicate and gradient product already exist at clean 4K page boundaries;
response-bearing publication plus explicit token waits gives the SoA/JIT
operation coherent immutable arrays without copying a logical payload into
the RMW engine.

A paired/fused volume+gradient operation could share the index and predicate
scan or avoid gradient publication, but it would be a different ABI and a
different hardware treatment.  The current single-operation implementation
rescans shared index/predicate inputs and issues one timed value-line request
per selected occurrence.  Measure those requests and port stalls first.  Add
fusion only as a successor experiment if the unfused result shows that shared
source scans or publication dominate; retain the unfused arm as its control.
Likewise, measure the eight-context/four-value-line-cache proposal from
`90b4fd59cfea` only as a declared successor to the one-context primary arm,
with its 1,250-byte storage ledger and the identical guest/checkpoint.

The following shortcuts are not fair substitutes:

- precompute predicate or gradient arrays before the ROI/checkpoint only for
  the new arm;
- read cacheable SPD from the CPU and fill the arrays functionally;
- use ordinary response-less stream stores and assume acceptance is visible;
- point `predicate[]` at signed `corner_type`;
- omit predicates while leaving inactive `corner_volume` values nonzero; or
- calculate `csurf * zone_field` inside the RMW without declaring a fused
  treatment and charging its resources.

## Matched GZP experiment

Run one source-clean, provenance-frozen matrix with these four primary arms:

| arm | guest path | Row/Offset logical capacity | physical SPD | RMW treatment |
|---|---|---:|---:|---|
| `native16` | existing fixed-input GZP | 16K | 16K | ordinary two 16K RMWs/window |
| `native4` | existing fixed-input GZP | 4K | 4K | ordinary two 4K RMWs/page |
| `current_hybrid` | deferred general-hybrid guest | 16K | 4K | current token materializer; ordinary 4K RMWs |
| `new_hybrid_soa_jit` | **same deferred guest and checkpoint as current hybrid** | 16K | 4K | ACKed publications plus two SoA/JIT RMWs/full window |

Use `n=1,000,000`, four O3 cores, fixed input/seed, two memory channels, four
L3 ports, 3.2 GHz clocks, the same Ramulator/config tree, and the same
non-treatment hybrid knobs as the accepted current matrix.  Select
current/new hybrid mode only after the shared checkpoint is restored.  Freeze
and record source commit, clean status, gem5 and all guest hashes, config-tree
hash, Ramulator library/config hashes, exact commands, checkpoint-tree hashes,
selector hashes, resolved geometry, and output directories.

Use at least three replicas per arm and report every `simTicks` observation,
then the predeclared median and baseline/candidate speedup direction.  Do not
use host time.  Native checkpoints may remain profile-specific, but the two
hybrid arms must share the identical guest binary and checkpoint so the RMW
treatment is their only guest/checkpoint delta.

Require all arms to have:

- `restore.exit=0`, exactly one terminal `m5_exit`, no fatal text, and a
  complete first ROI statistics window;
- `UME_OUTPUT_FP output_hash=11225737641199706160 nonfinite=0`;
- `UME_REFERENCE_PASS point_volume_errors=0 point_gradient_errors=0
  elements=1180000`; and
- immutable checkpoint and frozen-input hashes after every restore.

Require the new arm additionally to show:

- `numInst_INDRMW=124`, comprising 122 guarded full-window RMW completions and
  two ordinary tail RMWs;
- 122 distinct, terminal SoA/JIT generations;
- per generation, `selected + rejected = 16384`;
- predicate issue=response, value issue=response=selected,
  aliases-applied=selected, A read=response, and A write=response;
- 124,928 exact publisher line issues and matching `WriteResp`s, split
  62,464/62,464 between predicate and gradient;
- zero stale/duplicate/unmatched responses, zero premature token readiness,
  zero source-buffer reuse violations, zero forbidden old-value destinations,
  and terminally empty publisher/RMW scoreboards; and
- measured index, predicate, value, A-read/write, publisher, cache-port retry,
  address-permit, context-stall, and high-water counters.

The decisive comparison is current hybrid versus new hybrid.  Native16 is the
performance target and native4 is the small-SPD control.  A correct RMW count
reduction is mechanism proof, not performance proof: report the new arm even
if publication and JIT traffic make it slower.  Do not reuse the analytic
1.2070x ceiling as a prediction or claim.

Useful diagnostic arms after the primary matrix are `volume_only` and
`gradient_only`, both restored from the same hybrid checkpoint.  They can
attribute predicate-publication cost versus gradient-publication/reread cost,
but they must not replace the primary both-RMW treatment or enter its speedup
aggregate.

## Validation and promotion gate

Before the GZP matrix, require focused tests for:

1. exact FP32 duplicate-index order across all four pages, including
   `16777216, 1, -16777216, 1` and cross-page aliases;
2. predicate zero/one/nonzero behavior, signed `-1` rejection as a GZP source,
   false-lane poison values, and null-predicate all-selected behavior;
3. range min/max/stride bounds, partial/empty rejection policy, registered
   half-open spans, and A/source overlap rejection;
4. publisher retry payload ownership, final-`WriteResp` token readiness,
   stale/duplicate/wrong-address responses, and source-tile reuse after packet
   capture but before global publication;
5. RMW A-read/value-read/write retry, same-address exclusion, generation
   rollover, completion-token reuse, and exact terminal drain;
6. four-owner duplicate indices and address-region permit retention through
   final A `WriteResp`;
7. two sequential RMWs sharing immutable index/predicate buffers but using
   different A/value arrays; and
8. the 576-element ordinary tail followed by the normalization barrier and
   full bitwise reference/hash check.

The backed-RMW oracle at `f02c66be17a1`, runner checkpoint fix at
`905619ab514c`, and correctness-marker flush at `b9a74c0f4a9e` are useful
starting coverage for items 1--3 and 5.  They do not waive guarded
SoA/JIT-specific timed-source, final-`WriteResp`, disjointness, publication, or
full-GZP checks.

Promotion requires those focused tests, sanitizer coverage for the bounded
publisher and RMW state, a clean completed multi-replica matrix, a hardware
storage/port/control ledger (including the RMW context and publisher line
credits), and the expected mechanism signature.  No result from an unclean
source tree, incomplete wrapper, response-less publisher, mismatched
checkpoint, wrong exact output, or one-off analytic substitution is
promotable.

## Correctness-first benchmark integration status

Commit base `1a9513db` has now been integrated at the benchmark boundary
without changing `src/mem/MAA`.  Source inspection, rather than the earlier
prose, gives the following exact mapping for the general-hybrid full window:

| sequence | A region | index stream | value stream | predicate | type/op | completion dependency |
|---|---|---|---|---|---|---|
| volume RMW | `point_volume` | `c_to_p_map + c` | `corner_volume + c` | exact `uint32_t(tileCond != 0)`, where `tileCond = corner_type >= 1` | FP32 `ADD_OP` | guarded `tdst2`; wait before the gradient RMW |
| gradient RMW | `point_gradient` | the same immutable `c_to_p_map + c` | exact completed FP32 `csurf * gathered_zone_field` words | the same immutable uint32 buffer | FP32 `ADD_OP` | guarded `tdst2`; wait before source-buffer reuse or normalization |

The existing hybrid gather remains
`maa_indirect_load_virtual_index(zone_field, c_to_z_map, ...)`, followed by
the selected consumer's ACK-gated page load and the existing FP32 vector
multiply.  The treatment does not fuse the gather, change its selector, or
recompute the product with a different arithmetic expression.  The last 576
corners of `n=1,000,000` keep the ordinary physical-4K predicate, map/value
loads, and both ordinary RMWs.

The signed-predicate issue is closed explicitly.  `corner_type` is never
reinterpreted as `uint32_t`.  A separate one-million-word `uint32_t` buffer is
initialized with exactly `corner_type[i] > 0 ? 1 : 0` before the checkpoint,
then registered as immutable external memory.  Its element count, active count,
semantic (`corner_type_gt_0`), phase (`pre_checkpoint`), and 64-bit hash are
printed after restore; the terminal marker repeats the hash.  The correctness
arm also normalizes each completed GTE lane to its CPU-staged predicate.  A
false staged gradient lane contains FP32 qNaN bits `0x7fc00001`, making an
erroneous false-lane value read observable through the existing
nonfinite/reference gate.  Thus signed `-1` remains false, as required.

### Current publication boundary

The repository contains `ResponseBearingSpdPublisher.hh` and its adversarial
unit test, but no guest-visible opcode/controller wiring invokes it.  Ordinary
`STREAM_ST` still retires at transport acceptance and therefore cannot supply
the required visibility edge.  The benchmark does not pretend otherwise.

For this correctness-first integration only, it waits for the FP32 product
tile, whose dependency chain includes the exact predicate, csurf input, and
ACK-gated virtual-gather page.  Only after that completion does the CPU copy
the predicate/product words from cacheable SPD into ordinary coherent
per-owner buffers.  A sequentially consistent fence orders those stores before
the SoA/JIT MMIO record.  Both SoA/JIT completion tokens are waited before the
buffers can be overwritten.  This is safe and fully charged inside the ROI,
but it is not the proposed hardware publisher and is marked
`performance_promotable=0` in both start and terminal markers.

The `volume_only_soa_jit` arm has no CPU staging in the ROI.  For each of the
61 complete 16K windows it issues one logical16 SoA/JIT add directly from the
memory-resident `corner_volume + c` values and direct
`reinterpret_cast<const uint32_t *>(c_to_p_map + c)` index stream, guarded by
the immutable precheckpoint predicate.  It waits for that RMW completion before
continuing.  The gradient path, including its live GTE predicate, virtual gather,
FP32 multiply, and four ordinary 4K RMWs, is unchanged.  The 576-lane tail keeps
both ordinary RMWs.  Therefore the frozen count is 61 logical volume RMWs + 244
full-window gradient RMWs + two tail RMWs = 307.  This arm is performance-capable
because every new logical16 source is already memory-resident and immutable;
the precheckpoint predicate cost and hash are disclosed rather than hidden as a
timed publisher claim.

The eight 16K per-owner staging buffers and the shared immutable predicate
buffer remain ordinary external memory and keep the registration count at 29
of 32.  The two extra completion-only tiles per owner
consume the eight tiles left after the existing six-per-owner GZP allocation;
they are never reused as SPD data.  Full-window volume and gradient RMWs are
issued serially, so their shared immutable index/predicate buffers cannot be
overwritten early.  The ordinary tail retains the existing `wait_ready(tile2)`
and the OpenMP barrier remains after every owner's last operation.

### Selector, markers, runner, and evidence gate

`gradzatp_maa_16K_general_soa_jit_fp` is one guest binary with a restore-only
two-token selector:

```text
token_stream_ld legacy_4k
token_stream_ld volume_soa_jit
token_stream_ld soa_jit
```

The selector is read after the checkpoint, so the two hybrid arms share the
same binary and checkpoint.  The new path prints
`UME_GZP_RMW_TREATMENT` before the ROI and exactly one `UME_GZP_TERMINAL` after
the existing bitwise reference/fingerprint gates.  For `n=1,000,000`, the
terminal contract is 61 full windows, 999,424 staged lanes per buffer, 122
guarded SoA/JIT completions, 124 total RMW instructions, and the unchanged
output hash `11225737641199706160` over 1,180,000 points.

`experiments/scripts/run_gzp_soa_jit_correctness.py` creates exactly five
arms (`native16`, `native4`, `current_hybrid`, `volume_only_soa_jit`, and
`soa_jit_correctness`), freezes source/binary/config/Ramulator/checkpoint and
selector identities, stores exact commands and wrapper exit codes, and records
only simulated `simTicks`.  Global `--extra-gem5-arg` and per-arm
`--restore-arm-gem5-arg ARM=ARG` options accept one whitespace-free gem5
option token at a time and are included in the plan, manifest, and hashed exact
restore command.  This supports optimized context and counter settings without
silently changing checkpoint creation.  Execution fails closed unless the
caller supplies the lead-provided optimized gem5 SHA-256; plan mode does not
launch gem5.

`experiments/analysis/analyze_gzp_soa_jit_correctness.py` requires one terminal
`m5_exit`, a complete first ROI stats window, exact cross-arm output identity,
490 current-hybrid, 307 volume-only, and 124 correctness-treatment RMWs; 61 or
122 terminal SoA/JIT generations as appropriate; per-generation
predicate/request/response/alias closure; aggregate counter closure; and exact
materializer retirement without fallback.  Context high-water must be positive
and internally consistent but is not pinned to one, permitting optimized
context settings.  It reports every `simTicks` observation and the predeclared
median current/volume-only simulated speedup.  It never uses host time and
explicitly forbids a performance comparison with the CPU-staging arm.

The remaining blocker is architectural, not semantic: wire a bounded
response-bearing SPD publisher to a guest opcode, then replace the CPU staging
copy and validate its exact WriteResp, retry, generation, and terminal-drain
counters.  Until that happens, this integration is valid correctness evidence
for the logical16 SoA/JIT RMW mapping, not performance or promotion evidence.

Local source validation compiled the native16, native4, existing general
hybrid, and selector-compatible SoA/JIT targets together with `g++ -O3`; the
contract tests, Python bytecode checks, five-arm optimized-setting plan check,
synthetic 61/122-event trace and aggregate-counter closure at context high-water
eight, gem5 source style, and `git diff --check` also pass.  No live gem5 binary
was present in this worktree, and no O3 simulation was launched.  The frozen
runner remains gated on the optimized gem5 hash supplied by the lead.
