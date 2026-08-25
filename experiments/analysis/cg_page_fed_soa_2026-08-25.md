# Bounded page-fed CG SoA/JIT vertical slice (2026-08-25)

## Decision and scope

The selected architecture is implemented as a default-disabled, exact
four-page admission form of the existing no-result FP32 SoA/JIT RMW.  It
opens one useful 16K operation, consumes four completed physical 4K index SPD
pages, closes once, and then executes the existing RowTable/OffsetTable
schedule.  Existing ordinary, one-pass SoA/JIT, logical-page, and
physical-page-product modes keep their original wire forms.

This vertical slice does not run full CG or make a performance claim.  Its
promotion gate is the focused four-page cross-page-collision microprobe.

Base source commit: `c48ebef0115530da3b847fe00224216beb2b500e`.

Bounded ABI/state milestone: `787624226d1f8e190793945fcd7f4c76decd96f3`.

## Architecture

The page-fed descriptor reuses ordinary `INDIR_RMW_VECTOR` with the guarded
`0xfd` mode tag.  It names mutable A, coherent product backing, one completion
tile, operation, and a 40-bit nonzero generation.  It has no range-register
dependency because its only legal geometry is exactly 16K.  Instruction word
four must be `UINT64_MAX`, the no-index-backing sentinel; predicate word five
must be zero.  The descriptor's memory-hazard set therefore contains A write
and product read only.

The mode bit and generation reuse the existing 64-byte instruction-file slot
(word zero's guarded tag and word six).  The C++ `Instruction` fields that
mirror those decoded words are simulator representation, not incremental
modeled SRAM or an additional descriptor.

Instruction-aperture word seven is a non-queued 64-bit command doorbell:

- admit: magic, generation, page 0..3, and one completed physical tile ID;
- close: magic and generation, with page/tile bits zero.

An admit command requires the expected page, exact active generation, a
finished 4096-element physical tile, and no live IF reference to that tile.
For lane `i`, hardware reads the SPD index and inserts it into the existing
RowTable/OffsetTable with `itr = page*4096+i`.  The existing Offset entry's
`itr` is the product-source ordinal later consumed by the JIT value path.
Neither the index word nor its page identity survives admission.  There is no
index cache request, response, WriteReq, WriteResp, registered range, payload
buffer, descriptor spool, ordinal bitmap, or 16K tile.

Close is accepted only at four pages and 16,384 monotonically admitted
ordinals.  The Fill stage cannot authorize Build before close.  The normal
RowTable claim path then reads products by retained ordinal and preserves
same-destination source order.

## Fail-closed contract

The state machine poisons invalid admission and gem5 terminates on:

- disabled mode or malformed tag/command;
- missing active context or more than one context for a core;
- zero, reused-current, or mismatched generation;
- out-of-order/repeated page;
- skipped, repeated, or out-of-range ordinal;
- incomplete page or close before all four pages;
- physical tile not Finished or not exactly 4096 elements;
- Offset capacity below 16K, epoch capacity below 16K, Offset full, or any
  RowTable insertion failure that would require a drain;
- execution before exact close, duplicate execution, or stale completion.

Capacity does not fall back to coherent backing and does not drain a partial
page-fed epoch.  Such a configuration is not an emulated implementation of
this architecture.

## Timing and port charge

Each page admission charges exactly 4,096 32-bit SPD reads through the
existing SPD read-port model and 4,096 Row/Offset writes through the existing
RowTable parallelism and `rowtable_latency`.  Concretely, the SPD side calls
the normal `getDataLatency(ceil(4096/16))` path, while the Row side charges
`ceil(4096/total_RT_subslices) * rowtable_latency`.  The doorbell WriteResp is
held until the maximum of those modeled finish times.  Close has one control
cycle and schedules Fill only after closure is latched.  There is no command
queue or retry buffer.

CG launches a response-bearing product publication before the page-admission
doorbell.  The publisher's stream/cache traffic can overlap the disjoint SPD
index-read and Row/Offset admission ports; the next physical page waits only
for the product completion tile that it must reuse.

## Hardware-byte ledger

| Item | Per indirect unit | Four-unit probe | Notes |
|---|---:|---:|---|
| Generation | 8 B | 32 B | Current/last generation |
| Admitted count / next ordinal | 4 B | 16 B | Exact monotonic closure |
| Page cursor | 1 B | 4 B | Values 0..4 |
| Active/closed/failed/executing flags | 1 B | 4 B | Packed flag byte |
| Explicit reserved/alignment charge | 2 B | 8 B | Billed, not hidden padding |
| **Persistent new control** | **16 B** | **64 B** | `static_assert` enforced |
| Command doorbell wire | 8 B transient | 8 B shared aperture | No queue/latch array |
| Index payload/buffer | 0 B | 0 B | Discarded during admission |
| Hidden descriptor/ordinal bitmap | 0 B | 0 B | Not present |
| Incremental Row/Offset state | 0 B | 0 B | Existing entries reused |

The exact admission/response/traffic counters are simulator instrumentation,
like the pre-existing SoA/JIT counters; they are not synthesized control or
payload storage and are excluded from the hardware-byte total.  The
`IND_SoaJitPageFedStateByteOperations` stat is explicitly cumulative
byte-operation instrumentation; the persistent capacity is the 16-byte
`static_assert` and terminal ledger, not that cumulative stat.

For four CG cores, candidate coherent backing is 524,288 B: 262,144 B of
existing virtual-gather backing plus 262,144 B of product backing.  The prior
physical-page-product treatment used another 262,144 B of registered logical
index backing, for 786,432 B total.  Physical SPD payload capacity is unchanged
at 524,288 B and is not hidden candidate state.

## Line traffic

One 16K window contains 1,024 64-byte index lines and 1,024 product lines.
Relative to the prior physical-page-product treatment, page-fed admission
eliminates exactly:

- 1,024 coherent index publication WriteReq/WriteResp lines per window;
- 1,024 coherent index reread/fill lines per window.

Applying the prior profile's 10,960-window population gives 11,223,040 index
publication lines plus 11,223,040 index reread lines, or 22,446,080
issue-level coherent index line operations.  These are calculations from the
prior population, not candidate full-CG measurements.  Product traffic
remains useful and explicit: 1,024
response-bearing product publication lines and the JIT product reads.  With
the default value cache disabled, the collision probe measures 16,384 product
line read issues/responses: one line fetch per retained ordinal, or 16 reads
per unique product line.  If that exact mechanism count held for the profiled
full-CG window population, it would be 179,568,640 product read issues; this is
an extrapolation, not full-CG evidence.  Product publication would calculate
to 11,223,040 lines under that same prior window count.  The probe's
4,096-destination target
contributes 256 A read lines and 256 response-bearing A write lines; full-CG
target traffic remains data dependent and is not projected from the probe.

## Focused evidence

Accepted evidence root:
`/tmp/2026-08-25-cg-page-fed-soa-f67cb2da-r4`.

- source commit: `f67cb2da1c5b54b05fb37b83feec3e5991664d6c`;
- source-built gem5 SHA-256:
  `c82c3c4adb40d8a3826fb0770c8ea0b887c902538a6ac61e234b7a3c0b5ff219`;
- guest SHA-256:
  `ed5b34f9eea7e47e18e6fcea3347128069a5988b12dc4e321780c11c682eb007`;
- frozen Ramulator SHA-256:
  `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`;
- restore return code 0, exact `m5_exit`, nonempty final stats, checkpoint
  immutability, and artifact immutability all pass;
- `simTicks = 402,865,430` is recorded for provenance only, not compared as
  a performance result.

The exact output hashes for ordinary four-page RMW, existing one-pass SoA,
and page-fed SoA are all `17263589712773219203`; all 16,384 product words
match hash `2849837644626199427`, with `errors=0`.  The mechanism ledger is:

- opens/admissions/closes = 1/4/1;
- open/command/total ABI responses = 1/5/6;
- admitted/SPD-read/Row-written index words = 16,384/16,384/16,384;
- index publication pages and coherent index read/write lines = 0/0/0;
- product publication pages/lines = 4/1,024;
- product read issues/responses = 16,384/16,384;
- target A read/write lines = 256/256;
- admission port charge = 1,028 cycles;
- capacity drains, missing/duplicate ordinals, stale generations, and early
  execution = zero.

Result/trace/stats SHA-256 values are recorded in
`result_sha256.txt` under the accepted evidence root.  Earlier r1/r2 runs were
interrupted after exposing the product-publication hazard; r3 was terminal and
correct but intentionally rejected by the then-wrong 1,024-read expectation.
They are not accepted evidence.

No full CG or native run was performed.
