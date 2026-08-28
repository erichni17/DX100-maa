# Independent hardware/accounting review: strict line-combined P retirement

## Decision

**Do not promote the `--maa_virtual_masked_writes` result as iso-area,
hardware-timed, or native-comparable.**  It is a useful, exact, bounded
retirement-attribution observation: the accepted NA1024 pair preserves the
same CG fingerprint and reductions while reducing P write *transactions* from
1,064,960 4-byte writes to 358,114 64-byte writes.  It does not establish
that the masked arm has no incremental control/port cost, nor that the two
implementations have equal area or cycle time.

The strict handoff correctly labels it non-promotable and reports 2,386,167,394
versus 2,213,855,573 simTicks (1.077833x).  This review independently agrees
with that limited attribution, not with an area or product-performance claim.

Primary handoff: [strict_two_phase_cg_reference_2026-08-27.md](../analysis/strict_two_phase_cg_reference_2026-08-27.md).

## What the option changes

`virtual_masked_writes` is a default-false runtime bit
([MAA.py](../../src/mem/MAA/MAA.py#L116-L118),
[Options.py](../../configs/common/Options.py#L389-L392)).  Both matched arms
use the same accepted finite configuration: one MAA, four indirect units,
16 combiner slots, 4 ways, 4 banks, eight unpacked response slots, one
direct-index line, and 32 write credits
([fused runner](../scripts/run_cg_fused_p16_product_q16.py#L66-L76),
[resolved-config gate](../scripts/run_cg_fused_p16_product_q16.py#L360-L386)).
The line-combined wrapper adds only this option to the already-strict restore
command ([wrapper](../scripts/strict_two_phase/run_cg_strict_line_combined.py#L91-L101)).

The same combiner is present in both arms.  On eviction/final flush, the
disabled arm emits each valid word as a word-sized write; the enabled arm
copies the extant word references into a 64-byte temporary line and emits one
64-byte WriteReq with a word-derived byte-enable mask
([drain paths](../../src/mem/MAA/IndirectAccess.cc#L10690-L10735),
[final flush](../../src/mem/MAA/IndirectAccess.cc#L10915-L10943)).  Thus the
option **does not add a second payload store in the simulator model**.  It
does require an implementation of mask generation/expansion, full-line
staging muxing, a masked-write issue path, and a bounded outstanding-write
scoreboard if it is a separate hardware design.  The C++ `set`, `map`, and
`vector` instances are shared source fields, but are not a bounded RTL
implementation ([state inventory](../../src/mem/MAA/IndirectAccess.hh#L145-L174)).

Consequently there are two defensible statements, and they must not be
confused:

- As two modes of one pre-provisioned generic combiner, the fixed payload
  capacities are unchanged and the incremental architectural mode state is a
  control bit plus selection/mask logic.
- As separate baseline and candidate designs, the baseline can omit that
  logic and byte-enable/partial-line tracking.  Equal gem5 knobs therefore do
  not prove equal area, ports, power, or Fmax.

## Storage and search accounting

The accepted configuration really has **16 logical combiner lines**, not 16
private 64-byte line arrays.  A slot is tag/mask/references; its payload is a
shared pool.  Current source allocates a 16-slot vector and resets a pool of
`slots * words_per_line = 16 * 16 = 256` entries
([allocation](../../src/mem/MAA/IndirectAccess.cc#L139-L183),
[per-operation reset](../../src/mem/MAA/IndirectAccess.cc#L6080-L6103)).
Each pool entry is maximum-width 8 bytes, so the fixed payload charge is
2,048 B per indirect unit.  The eight unpacked response slots allocate eight
64-byte lines, or 512 B per unit
([response store](../../src/mem/MAA/VirtualResponsePayloadStore.hh#L20-L39)).
The intended one-line direct-index capacity is another 64 B.  Therefore the
explicit fixed data capacity is **2,624 B/unit, 10,496 B for the four selected
indirect units** (2,048 B combiner + 512 B response + 64 B index).  That is
present on both arms; it excludes Row/Offset, packet buffers, and C++ container
overhead.

A useful packed-control lower bound for the combiner alone is 16 times
`valid(1)+line-address(64)+word-mask(16)+16 pool references(8)` = 3,344 bits,
plus a 2,313-bit 256-entry free-stack/allocation estimate: 5,657 bits
(708 B ceiling) per unit.  This is only an illustrative organization, not a
synthesis result.  Source actually stores 32-bit generation-bearing references
and vectors ([payload store](../../src/mem/MAA/VirtualCombinePayloadStore.hh#L18-L25),
[allocation metadata](../../src/mem/MAA/VirtualCombinePayloadStore.hh#L69-L100));
those host widths must not be reported as an RTL area number.

The lookup is a four-way set probe, not a CAM-free assertion: a request scans
up to four tags in its indexed set, and bank arbitration permits only one
access per selected bank per cycle ([insert path](../../src/mem/MAA/IndirectAccess.cc#L10611-L10684)).
The source gives neither a comparator/arbitration/pool-RAM latency nor a
pipeline stage for lookup, `copyLine`, mask expansion, or the write scoreboard.
The 32-credit limit bounds intent, but the actual address exclusion and
completion metadata are an STL `set`/`map`, not a 32-entry scoreboard
([issue path](../../src/mem/MAA/IndirectAccess.cc#L10076-L10124)).

The optional page-ordered drain is *not* enabled by this accepted config.  If
it is enabled later, its own documented organization adds two 9-bit links and
a 4-bit page id per 384 slots plus a 16-head encoder; it is neither part of
this 16-slot result nor a 384-slot priority scan
([design note](../../docs/architecture/virtual_combiner_page_ordered_drain.md)).

## 16K logical / 4K physical accounting

The selected runner requires `num_tile_elements=16384` and
`physical_tile_elements=4096`; strict mode independently panics on any other
geometry and requires full 16K Offset/epoch capacity
([config gate](../scripts/run_cg_fused_p16_product_q16.py#L366-L374),
[strict guard](../../src/mem/MAA/MAA.cc#L384-L406)).  SPD allocation uses the
physical count, not the logical aperture: its visible payload is
`visible_tiles * 4096 * sizeof(uint32_t)`
([SPD allocation](../../src/mem/MAA/SPD.cc#L255-L278)).  With the accepted
four cores × eight tiles/core, that is 32 × 4096 × 4 = **524,288 B (512 KiB)**
visible SPD, versus 2,097,152 B for visible 16K tiles.

This is payload accounting only.  The 16K Row/Offset descriptors remain and
their source representation has unresolved `Addr`/`int`/container widths.
Also, the older SPD cost note's 256-KiB private-tail total assumes **four
MAAs**, whereas this accepted runner requires `num_maas=1`; it must not be
silently added to, or used to compare, this selected configuration.  Its
supporting `spd_hardware_accounting.py` currently fails its own source-marker
check for `LogicalSPDCacheRuntime.hh`, so it is not a current validated ledger.

## Why the 64-byte arm can transport more bytes

For every masked packet, the request size is a cache line, and the model
accounts `transport_bytes += size`; semantic bytes are only
`popcount(valid_words) * word_size`
([write accounting](../../src/mem/MAA/IndirectAccess.cc#L10125-L10149)).
The disabled byte positions are protected by byte enables
([mask construction](../../src/mem/MAA/IndirectAccess.cc#L10097-L10117)),
but the packet is still 64 B.  A one-word FP32 fragment therefore transports
64 B for 4 semantic B (16x).  Across the accepted P arm, the common semantic
work is 1,064,960 × 4 = 4,259,840 B; 358,114 line packets transport
22,919,296 B, or 5.38x that semantic volume.  This is consistent with fewer
transactions and a lower modeled transaction/ACK bottleneck; it is not a
payload-bandwidth reduction.

## Coherence, acknowledgement, and partial-line correctness

The implementation has meaningful fail-closed checks:

- An aligned line key prevents a second masked/full write to the same line
  while one is outstanding.  A metadata record retains page counts,
  generation, line, and word mask until the WriteResp
  ([issue/metadata](../../src/mem/MAA/IndirectAccess.cc#L9982-L10041)).
- The port routes a WriteResp only through the outstanding-packet key; the
  indirect unit then erases that line from the outstanding set, increments
  completed words, and fences strict backing completion on that response
  ([port](../../src/mem/MAA/Port.cc#L773-L795),
  [completion](../../src/mem/MAA/IndirectAccess.cc#L11415-L11453)).
- Strict mode forbids the idealized-at-issue acknowledgement, and its ledger
  requires matching issue/ACK counts and exactly the logical semantic byte
  total before producer completion
  ([strict prohibition](../../src/mem/MAA/MAA.cc#L384-L418),
  [terminal checks](../../src/mem/MAA/StrictTwoPhaseReference.hh#L254-L295)).

This supports functional exactness of the accepted pair, but leaves two
hardware obligations open.  First, correctness relies on the cache/coherence
path honoring the Request byte-enable semantics; the CG equality is an
end-to-end check, not an adversarial partial-line coherence proof against
intervening writers, eviction, retries, or line ownership transitions.
Second, WriteResp identity is matched by an outstanding physical line key and
host maps rather than an explicit fixed tagged transaction table.  A real
design needs generation/line/mask transaction entries, explicit reuse rules,
and assertions/tests for stale/duplicate ACK, two partial writes to one line,
and interaction with competing coherent agents.

## Promotion checklist

Do all of the following before any hardware, iso-area, or broader-performance
promotion:

1. Write an RTL/microarchitecture specification for the 16-slot, 4-way,
   4-bank combiner and **32-entry** masked-write scoreboard: tags, valid masks,
   free list, byte enables, transaction/generation IDs, retry behavior, and
   exact read/write port counts.
2. Synthesize both a word-write baseline and the masked-line design in the
   same technology/library and constraints.  Report SRAM macros, flop/control
   area, comparator/mux/byte-mask logic, routing, dynamic/leakage power, and
   post-synthesis/post-layout Fmax; either hold area equal by explicit
   reinvestment or stop calling the comparison iso-area.
3. Add timing for tag lookup, pool read/copy, mask generation, bank conflict,
   scoreboard allocation/retirement, request injection, and byte-enabled
   coherence handling.  Calibrate the gem5 model with that timing and include
   backpressure/port contention rather than treating host loops as zero time.
4. Add directed coherence tests with sparse masks, repeated fragments of the
   same line, overlapping/rejected masks, delayed/reordered/stale ACKs,
   competing CPU/MAA writers, cache eviction/retry, and checkpoint/drain
   boundaries; require semantic-byte, word-mask, issue, and ACK reconciliation.
5. Revalidate the cost ledger against current source and freeze a config-bound
   ledger for this one-MAA/4-unit geometry.  Keep visible SPD, retained
   16K Row/Offset state, combiner/response state, and any private logical
   payload separately accounted.
6. For performance promotion, run a provenance-matched equal-work native4
   comparator and the authorized full-CG gate.  Retain the current NA1024
   result only as exact bounded transaction-combination attribution.
