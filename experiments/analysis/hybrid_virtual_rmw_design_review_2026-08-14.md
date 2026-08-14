# Backed virtual-RMW minimum-state design review — 2026-08-14

## Decision

Continue the backed virtual-RMW direction, but do **not** promote the proposed
4,096-entry `BackedRmwValueSlot` or `BackedRmwWriteSlot` arrays as minimal
hardware.  Keep the existing hybrid: one logical-16K ordering scope, the
current 4K Offset/RowTable epoch and `BoundedDescriptorSpool`, physical 4K SPD
producer/result pages, and normal timed cache/DRAM ports.  Fetch the operand
identified by the retained logical iteration only after an A-line RowTable
entry is claimed.  A fixed one-context implementation is sufficient for
correctness; eight contexts are a reasonable first optimized point.

This is not a new fully bounded architecture.  It is a small consumer added to
the current resident-first four-pass hybrid.  It does not change the 16K
ordering contract or add a dedicated memory port.  Its price is extra
timing-visible operand-record traffic and holding a selected 64-byte A line
while its record chain is consumed.

The target is material.  The matched GZP attribution in
`hybrid_gzp_remaining_gap_2026-08-14.md` finds 4,028,146 of the 4,870,516-cycle
hybrid/native16 gap (82.7047%) aligned with `INDRMW`, because the hybrid still
executes 490 page RMWs rather than 124 logical RMWs.  The native16-latency
substitution is only a 1.2070x analytic ceiling, not evidence for this design.

## Source contract at `47f4260c`

The ordinary `INDIR_RMW_VECTOR` contract is the authority, not an opcode name
reused with weaker behavior:

- `benchmarks/API/MAA_gem5.hpp:644-662` supplies equal-position index, value,
  optional predicate, and optional old-value destination tiles.
- `src/mem/MAA/IndirectAccess.cc:2803-3559` translates `A[B[i]]`, derives the
  A line and word, and inserts logical `i` and `wid` into the Row/Offset state.
  `src/mem/MAA/Tables.cc:147-167,348-368` appends equal-A-line occurrences in
  insertion order while preserving logical `i`.
- `src/mem/MAA/IndirectAccess.cc:5770-6004` starts from the returned old A
  line, optionally publishes each old word to destination SPD, and applies
  typed ADD/MIN/MAX in Offset-chain order.  Lines 6009-6025 emit the updated A
  line.  A correctness-first backed form must replace that non-response-bearing
  `WritebackDirty` retirement with a response-bearing `WriteReq` and must not
  complete until its exact `WriteResp`.
- `src/mem/MAA/BoundedDescriptorSpool.hh:14-49` already defines the needed
  backed identity: a 48-bit record containing 14-bit logical `i` and 32-bit
  index.  It permits at most 4,096 active descriptors, three external
  populations, four read-line credits by default, and sixteen write credits
  (`:31-43`).  The external footprint for three full populations is 73,728 B;
  one classification append plus one replay is 2,304 64-byte line transfers,
  or 147,456 B per logical RMW.

Because the spool returns logical `i`, the value need not be copied into the
RowTable.  Keep `OffsetTableEntry::itr == logical i`; use it to address the
published operand record when the A line is selected.  This also preserves the
ordinary optional-destination position.  Replacing `itr` with a value-slot
number, as the reviewed slice does, couples architectural identity to a large
temporary bank and makes ordinary result semantics harder to retain.

## Review of the in-progress slice

The reviewed uncommitted slice was identified by SHA-256
`71e7c459...055a17` for `IndirectAccess.hh`, `eafe58f0...03a88f` for
`IndirectAccess.cc`, and `610b7173...2a91` for `MAA_gem5.hpp`.  It declares
4,096 value slots and 4,096 write slots (`IndirectAccess.hh:506-528`), fetches
one 32-byte record again during descriptor replay (`IndirectAccess.cc:1236-1283`),
allocates a value slot before RowTable insertion (`:3449-3489`), releases it
when the A response is transformed, and separately retains a write slot until
`WriteResp` (`:6056-6325,7603-7629`).

That is explicit added hardware, despite the comment that no operation-sized
payload is retained:

| Array | Logical minimum per entry | LP64 C++ object | 4,096 entries |
|---|---:|---:|---:|
| value: valid + value + generation + logical `i` | 111 bits | 24 B | 98,304 B (96 KiB) |
| write: valid + A-line address + generation | 97 bits | 24 B | 98,304 B (96 KiB) |
| total | 208 bits | 48 B | **196,608 B (192 KiB) per indirect unit** |

Even an ideal bit-packed implementation is 106,496 B (104 KiB).  The value
array is live for a whole 4K RowTable epoch rather than for memory concurrency,
and the write array is sized to 4K even though the existing bounded transports
use 16 default/32 maximum write credits.  Neither bound follows from the
number of ports or outstanding transactions.

Two semantic issues must also be resolved before this can be called ordinary
RMW-compatible.  The slice turns the optional destination into a zero-sized
completion tile (`IndirectAccess.cc:3092-3101`) and suppresses old-A result
writes.  That is sufficient only for a new explicitly no-result ABI; GZP uses
no destination, but the ordinary opcode allows one.  Also, a generation field
inside a record detects stale data but does not model when page publication
became visible.  Publication needs real timed writes and an authenticated
final `WriteResp` boundary.

## Smaller just-in-time scoreboard

Yes: claim one A-line entry, retain its Offset-chain head, and walk that chain
in its existing order.  For each retained logical `i`, issue the corresponding
record-line read through the normal MAA cache path, validate the operation
generation and index, apply the value to the retained A line, and advance the
chain.  False predicates never enter the RowTable and therefore need no value
fetch.  When the chain ends, issue one response-bearing A-line `WriteReq` and
hold the context until its matching `WriteResp`.  Backpressure RowTable claims
when no context is free.

This handles the worst case in which all 4,096 occurrences hit one A line:
the engine retains one 64-byte A line and streams 4,096 operands through one
slot.  It does not need 4,096 simultaneously live values.  The chain order is
the ordinary insertion order.  All occurrences of one A line have the same
translated grow and therefore land in the same counted pass; four-pass replay
does not split or permute that alias chain.

For a generic 64-bit value, one packed context needs the following raw state:

| State | Bits |
|---|---:|
| retained A-line data | 512 |
| A-line address + head + cursor + remaining count + state | 106 |
| current operand: valid + 64-bit value + 32-bit generation + 14-bit `i` | 111 |
| exact write owner: valid + 64-bit A-line address + 32-bit generation | 97 |
| **total/context** | **826 bits = 103.25 B** |

The head/cursor/count fields use 13 bits each so 0..4095 plus an invalid/end
state are representable.  The operation generation can instead be global,
reducing the raw total by 64 bits per context.  Physical SRAM/register rounding
must be charged; a conservative 128 B/context gives 128 B for the serialized
correctness implementation or 1 KiB for eight contexts.  This is two orders of
magnitude below the slice's 192 KiB arrays.

No new external ports are required.  A correctness implementation uses the
existing cache/DRAM request path for record reads, A reads, and A writes, with
one scoreboard fill and one ALU consume per cycle.  An eight-context version
may allow at most eight record lines, eight A lines, and eight A writes in
flight; request sender state carries the context ID, so a large associative
lookup is unnecessary.  It needs one 64-byte response fill path, a 64-bit
operand read, an A-word read/modify/write path, and the existing packet
arbiter.  More ports or contexts are optimizations and must be justified by
measured high-water/stall counters.

### Traffic with the reviewed 32-byte record

For one full 16K RMW, records occupy 524,288 B or 8,192 cache lines.  The
counted-quantile summary and classification scans read them twice.  A JIT
replay reads between `ceil(selected/2)` lines (perfect two-record coalescing)
and `selected` lines (one reordered record per line request).  With all 16,384
records selected:

| Record traffic per logical RMW | Lines | Bytes |
|---|---:|---:|
| modeled publication writes | 8,192 | 524,288 |
| summary + classification reads | 16,384 | 1,048,576 |
| JIT value reads, best / worst | 8,192 / 16,384 | 524,288 / 1,048,576 |
| **total, best / worst** | **32,768 / 40,960** | **2.0 / 2.5 MiB** |

RowTable ordering intentionally destroys logical-record locality, so the
upper JIT bound is the prudent GZP expectation until measured.  These are
MAA/cache-line transfers; cache hits may avoid DRAM but do not make the port
traffic free.  Add the existing 2,304 descriptor-spool transfers (144 KiB),
and workload-dependent A traffic: one 64-byte read, one 64-byte write, and one
`WriteResp` per selected distinct A line in each pass.  GZP has two RMWs, so
the record bound is 4.0--5.0 MiB plus 288 KiB of descriptor traffic per full
logical window.  This cost must be measured against the 1.2070x zero-staging
ceiling.

The optimized layout should not scan 64-bit values while discovering grow
quantiles.  Publish index, predicate mask, and value as a structure of arrays;
the six-byte spool remains unchanged and JIT uses its `i` to read only the
value.  For GZP FP32, the two RMWs share 64 KiB of indices and a 2 KiB mask and
publish two 64 KiB value arrays: 198,656 B (194 KiB), exactly 3,104 aligned
lines per full window.  Each RMW must still derive its own grow partition
because `point_volume` and `point_gradient` have different bases.  A small
record-line cache/coalescer can reduce JIT reads, but no reduction should be
claimed without the observed line-request count.

## Legal four-page GZP publication and ordering

`benchmarks/UME/gradzatp.cpp:343-404` already supplies the legal cut points.
For each physical page `p` and lane `j`, logical `i = 4096*p + j`:

1. After `tile4`, `tile0`, and `tileCond` are ready at lines 355-365, publish
   volume record `(i, c_to_p_map, condition, corner_volume)` before `tile0` is
   overwritten by the zone-field materialization.
2. After the multiply produces `tile2` at lines 398-402, publish gradient
   record `(i, same c_to_p_map, same condition, csurf*zone_field)` before the
   physical tiles are reused for page `p+1`.
3. Publication is a modeled producer: it copies accepted line payloads out of
   the SPD, sends ordinary timed cache `WriteReq`s, owns retries and payloads,
   and marks a page published only after every line's authenticated
   `WriteResp`.  Acceptance may release the physical SPD source because the
   packet owns a copy; visibility may not be announced until `WriteResp`.
4. Four page records use one nonzero operation generation and fixed page
   ordinals.  Seal only after pages 0..3 are published.  The consumer may scan
   a line early only if that exact line's generation/WriteResp readiness is
   known; the simple implementation waits for the four page seals.

The record address supplies logical order, and the spool preserves `i`.
Within an aliasing A line, the Offset chain therefore applies page 0 lanes,
then page 1, page 2, and page 3 in logical order, matching one ordinary 16K
RMW.  Different A lines may retain RowTable/DRAM order because they do not
alias.  Execute volume and gradient as separate logical RMW generations; they
target different arrays and may share immutable index/mask publication, but
each owns its own value backing, descriptor lifecycle, address-range permit,
and exact A `WriteResp` drain.  The 576-element final GZP tail remains on the
ordinary path.  The normalization barrier at `gradzatp.cpp:465-468` waits for
both logical RMW completion tokens.

The existing 4K SPD remains the producer/result storage.  GZP requests no
old-A destination.  For the general opcode, correctness-first code should
reject a backed RMW with `dst != -1` until old values are written by logical
`i` into four page-backed result regions and exposed through the existing 4K
materializer; silently returning a zero-sized tile is not compatible.

## Implementation split and gate

**Correctness first:** one context, one logical RMW at a time, four fully
published pages, unchanged six-byte descriptor spool, one JIT record at a
time, one retained A line, no optional destination, exact address/generation
checks, and completion only after all descriptor publication ACKs, A
`WriteResp`s, and scoreboard entries drain.  Preserve normal port contention,
retry, address serialization, and the current 4K SPD hazards.  Fail closed on
missing/duplicate page, stale generation, duplicate `WriteResp`, record/index
mismatch, partial tail, and context reuse.

**Optimized later:** eight contexts, per-line publication readiness, shared
GZP index/mask backing, SoA records, record-line coalescing/cache, overlap of
publication with summary/replay, and optional backed old-result pages.  Every
optimization retains the same logical alias order and WriteResp boundary.

Do not promote on API output alone.  First require focused duplicate-index and
FP32-order tests spanning all four pages, false predicates, cache retries,
same-address write serialization, stale generations, and final-tail fallback;
then report value/context/write high-water, record publication/scan/JIT lines,
descriptor lines and ACKs, A reads/writes/ACKs, port stalls, exact output, and
terminal emptiness.  Only after that should a fresh matched
native16/native4/hybrid gem5 matrix test whether the mechanism reduces GZP RMW
count from 490 to 124 without exceeding the measured staging cost.
