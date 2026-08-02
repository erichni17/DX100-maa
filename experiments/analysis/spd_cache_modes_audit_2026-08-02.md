# SPD cache modes audit: gather paging, logical tiles, and output residency

Date: 2026-08-02
DX100 source and in-tree evidence: `87230d797ce885f1c31cede6ebdc78ef62def917`
Design brief audited: `/data1/nier/dx100-research/docs/plans/virtual_tile_design_2026-07-03.md`
(SHA-256 `416c60921c252996adfe35df81c1956a628d6d35626c8319ae6d0655983b7031`)

## Decision

The design brief combines three mechanisms that are not interchangeable:

| ID | Mechanism | Address/data being made resident | Status at `87230d7` |
|---|---|---|---|
| A | Coherently backed gather result, demand-filled into 4K SPD pages | Dense result `C[i] = A[B[i]]`, indexed by logical iteration | Implemented and exercised for one fused gather -> FP64 multiply -> dense-store chain |
| B | General logical SPD cache | Ordinary logical MAA source/destination tiles used by normal consumers | Standalone bounded control core only; not connected to the MAA, SPD, ports, IF, or API |
| C | LLC-resident scatter/RMW output region | Sparse target lines such as NAS-IS histogram buckets | Design opportunity only; no C experiment is established by A or B |

The safe reuse conclusion is:

- `LogicalSPDCacheController` is the right control-state foundation for B, but
  it is not yet sufficient or integrated.
- C can reuse its bounded-queue, generation, transaction, lease, and
  dirty-writeback disciplines. It cannot reuse the logical-page controller as
  an unmodified drop-in because C needs physical-line coherence and atomic-RMW
  state that the controller does not represent.
- The historical `13.6x` number is only a measured NAS-IS **DRAM-write
  amplification/opportunity** (`786,051 / 57,958`), not a measured or projected
  speedup. No timing or area conclusion is made here.

## A. Current coherent gather-result paging

### Mechanism

```text
indices B ----> 16K logical gather/reorder ----> coherent dense backing C
                                                      |
                     page-ready after every backing WriteResp
                                                      |
                         +----------------------------+
                         v              one ready page at a time
                 [4K physical SPD] -> [FP64 scalar MUL] -> [4K output SPD]
                         ^                                      |
                         +---- demand fill; then dense store ---+
```

This mechanism does not retain the scattered source array `A`, and it does
not retain an indirect scatter target. The indirect producer places each
returned gather word at its unique logical destination `C[i]`; the transparent
consumer later reloads four dense pages. Its preserved 16K reorder opportunity
comes from the retained Row/Offset metadata, not from a 16K physical SPD page.

### Exact source trace

All line references below are to `87230d7`; the corresponding source files are
unchanged between that commit and this audit's parent.

1. `benchmarks/API/MAA_gem5.hpp:372-393` encodes the direct-index virtual
   gather with an ordinary-memory backing destination. Lines `395-422` encode
   the special transparent consumer and explicitly name its completion token,
   physical input tile, output tile, backing, and final destination.
2. `src/mem/MAA/IF.hh:35-53` assigns the producer opcode
   `INDIR_LD_VIRTUAL_INDEX` and the special consumer opcode
   `VIRTUAL_TILE_ALU_SCALAR`. `src/mem/MAA/CpuSidePort.cc:215-380` decodes the
   physical tile fields plus backing/index addresses.
3. `src/mem/MAA/IndirectAccess.cc:2486-2563` divides the logical result by
   `physical_tile_elements` and publishes a page only after all its logical
   words are scanned and all expected retirement words are issued and
   completed.
4. `src/mem/MAA/IndirectAccess.cc:2625-2679` emits the backing writes as
   response-bearing `WriteReq`s, forces cache routing, limits outstanding
   retirement writes, and records page membership. Lines `3237-3250` count a
   word complete only on the returning write response.
5. `src/mem/MAA/Port.cc:48-77` serializes other MAA operations behind an
   exact-address retirement owner. Lines `539-568` send a virtual-retirement
   write through the retirement cache and retain a response-bearing packet;
   lines `698-718` route `WriteResp` back to the indirect producer.
   `src/mem/MAA/CacheSidePort.cc:119-122` selects the retirement-side port, and
   `configs/common/MAAConfig.py:419-422` connects those ports through private
   retirement caches to the L3-side bus. This is the coherent backing path.
6. `src/mem/MAA/MAA.cc:676-815` admits exactly a 16K-logical/4K-physical FP64
   multiply descriptor tied to the producer generation. Lines `818-910`
   synthesize native stream-load, ALU, and stream-store micro-ops for one page.
7. `src/mem/MAA/TransparentSPDController.hh:12-38,141-194` owns one descriptor,
   four page-ready bits, one mapped page, and one native action at a time.
   Lines `238-279` enforce the fixed fill -> compute -> store sequence.
   `src/mem/MAA/MAA.cc:1145-1171` advances and retires that sequence.
8. `src/mem/MAA/SPD.cc:237-270` allocates payload using
   `physical_tile_elements`; `src/mem/MAA/SPD.hh:53-79` rejects any element
   beyond that physical capacity. `SPD::setVirtualSize` changes size metadata,
   not payload capacity (`src/mem/MAA/SPD.cc:230-235`).

### What the evidence establishes

`experiments/analysis/transparent_spd_matched_matrix_2026-08-02.md` records a
six-arm matrix whose raw evidence is under
`/data1/nier/dx100-runs/2026-08-02-transparent-spd-premeeting`. Regenerating its
fail-closed summary from the six named run directories reproduced the archived
JSON and Markdown byte-for-byte. The gate checks matching simulator/workload
hashes, checkpoint and restore exits, one terminal marker, exact output hash
`7228541527853630339`, balanced retirement issue/completion, four page-ready
events, and the twelve-action fill/compute/store trace.

That evidence supports A for this one producer/consumer chain. It does not
establish ordinary logical tile operands, replacement among multiple logical
tiles, arbitrary consumers, C-style scatter residency, or a timing/area claim.
The final stream store also completes when its response-less write packet is
accepted (`src/mem/MAA/Port.cc:573-595`), so A's last destination is not a
general response-acknowledged logical dirty-page contract.

## B. General logical SPD cache for ordinary MAA consumers

### Intended mechanism

```text
ordinary MAA instruction:  logical src Ls, logical dst Ld
                                  |
                    +-------------+-------------+
                    | logical-page lookup/miss  |
                    | generations + finite FIFO |
                    +-------------+-------------+
                                  |
                 fill/evict       v       dirty writeback
coherent backing <---------- [bounded physical SPD slots] ----------> coherent backing
                                  |
                      ordinary native MAA micro-op
```

B makes logical identities architectural and physical SPD slots private to
the controller. Unlike A, software should not name a completion-token tile,
input slot, output slot, or special consumer opcode.

### What exists at `87230d7`

`src/mem/MAA/LogicalSPDCacheController.hh` is a payload-free, compile-time
bounded core:

- lines `35-66`: default capacities are two logical descriptors, four pages
  per descriptor, two physical slots, four FIFO misses, and four leases;
- lines `68-102`: descriptor/page/lease identities include generations and
  bounded indices;
- lines `167-220`: slots are `Empty`, `Filling`, `Clean`, `Dirty`, or
  `Writeback`, and external actions carry a unique transaction serial;
- lines `222-330`: allocation, exact page readiness, access, deduplication, and
  finite miss backpressure;
- lines `341-449`: deterministic fill/victim/writeback action selection and
  exact response matching; a dirty slot is not reused before its matching
  writeback response;
- lines `451-507`: bounded leases protect resident pages and authorize dirtying;
- lines `581-602,726-731`: all controller storage is fixed `std::array` state.

The integration boundary is equally exact: `src/mem/MAA/MAA.hh:494-509`
still owns the old physical-token arrays and one `TransparentSPDController`.
No MAA production file includes or instantiates `LogicalSPDCacheController`;
outside its own header it appears only in its host test and analysis/contracts.

### Reuse verdict for B

Reuse the core, but not unchanged. At this baseline it lacks all of the
following integration-critical pieces:

- a full-overwrite destination reservation that can create a dirty destination
  without fetching old bytes;
- atomic acquisition of distinct source and destination slots, with no pin
  held while waiting for the other slot;
- backing address, datatype, element count, and logical operand ABI metadata;
- hidden SPD slot allocation and rejection of software access to those slots;
- generation-bearing producer publication and ordinary-IF logical hazards;
- response-bearing stream fill/writeback transactions connected to the port
  response path; and
- consumer completion only after every destination writeback response.

Conditional or masked consumers additionally require an old-destination fill
or an exact byte-validity/overwrite proof. Treating an unwritten byte as zero
would be a correctness bug.

## C. NAS-IS scatter/RMW output residency through the LLC

### Mechanism

```text
                  sparse target address = histogram[key]
                                  |
                acquire/read line through coherent LLC
                                  |
               serialize/merge exact per-word RMW updates
                                  |
                 dirty line remains owned in LLC (or a
                 snoop-visible bounded MAA line buffer)
                                  |
             final eviction / phase handoff with acknowledgements
```

C caches the *ordinary-memory targets of an indirect update*. Its identity is a
translated physical cache line and word offset, not `(logical tile, page)`.
It neither needs nor implies a dense gather-result backing array.

### Current indirect path and the inert flag

The July design brief's cacheable/uncacheable probe changed
`--maa_l2_uncacheable` and `--maa_l3_uncacheable`. The null result is explained
directly by current source:

1. `configs/common/MAAConfig.py:207-249` constructs MAA apertures starting at
   `options.mem_size`: cacheable SPD data, noncacheable SPD data, size/ready,
   registers, instruction file, and virtual-ready state.
2. `configs/common/MAAConfig.py:427-435` adds only those MAA apertures to the L2
   or L3 exclusion lists. `configs/common/Simulation.py:742-752` likewise maps
   those apertures as cacheable/noncacheable for the process.
3. The NAS-IS histogram is ordinary application memory below the MAA MMIO
   apertures. Changing the aperture exclusion lists cannot change histogram
   routing.
4. Ordinary indirect RMW separately creates `ReadExReq`
   (`src/mem/MAA/IndirectAccess.cc:1953-1991`), performs the typed word update
   (`src/mem/MAA/IndirectAccess.cc:2192-2384`), and emits `WritebackDirty` using
   `my_force_cache` (`src/mem/MAA/IndirectAccess.cc:2394-2409`).
5. `src/mem/MAA/Port.cc:169-244` chooses cache-side versus direct-memory queues
   from the per-packet force decision or a CPU-cache snoop. The SPD-aperture
   exclusion flags do not set that decision.

There is a separate, relevant control that the old probe did not exercise:
`--maa_force_cache_access` is declared at `configs/common/Options.py:288-292`,
plumbed at `configs/common/MAAConfig.py:65-66`, and described by
`src/mem/MAA/MAA.py:55-58`. With it set, `MAA::sendPacket` leaves `hit_cache`
true and queues ordinary indirect requests on the cache-side path; that path is
connected to the L3-side bus at `configs/common/MAAConfig.py:413-418`.

Thus the old null probe correctly proves that SPD-MMIO cacheability is
irrelevant to C. It does **not** prove that forced coherent routing of the
histogram target is inert.

### Can `LogicalSPDCacheController` implement C?

Not as-is. The following generic pieces are reusable:

- finite FIFO/backpressure;
- generation and non-wrapping transaction identity;
- leases/pins;
- single-owner fill/writeback exclusion; and
- retain-dirty-until-ack discipline.

A C controller in front of the LLC needs additional state:

| State | Required meaning |
|---|---|
| Physical line identity | translated line address, region/address-space identity, word size, and phase/epoch |
| Coherence ownership | `Invalid`, acquire-exclusive/RFO in flight, resident exclusive/modified, recall/invalidate in flight, and writeback in flight |
| Dirty/valid data | per-byte or per-word valid and dirty masks; an operation/datatype tag for any accumulated delta |
| Global owner | MAA/core/instruction/generation owning the line, plus a single serialization point shared by all MAAs and CPU requestors |
| Atomic operation | per-address operation queue or reduction record, exact-once sequence/ticket, original value incorporated once, and response value/winner identity when the ISA observes it |
| Async completion | unique IDs and acknowledgement counts for fills/RFOs, invalidations/recalls, writebacks, and phase handoff |
| Waiters and pressure | finite MSHRs, per-line waiters/update slots, writeback slots, and an explicit stall/flush result when full |

An LLC-only first slice can delegate line data and coherence ownership to the
existing cache hierarchy and add no MAA-resident line store. Any later local
combiner must implement the table above; silently falling back to a direct DRAM
update while a dirty local/LLC owner exists is illegal.

## Correctness boundaries

### NAS-IS

IS histogram increment is a suitable narrow C contract only if all of these
are explicit:

- integer additions are exact-once and use the benchmark's defined overflow
  semantics;
- the initial histogram value is incorporated exactly once;
- no CPU or other MAA observes intermediate buckets during the offloaded phase;
- all requestors for the region share one coherence/serialization domain; and
- the phase-ending wait drains or hands off every dirty line before CPU
  verification.

Under that contract, updates to one bucket may be combined and commutative
updates to different buckets may be reordered. “Commutative” does not permit
dropped updates, duplicate updates, stale initial values, or an early phase
completion.

### BFS and SSSP

BFS discovery commonly uses compare-and-swap or another first-winner update.
The winning old value affects frontier insertion, so a reduction-only cache
cannot reorder or merge it without preserving a per-address linearization
point and returning the correct winner result.

SSSP's final distance may be a monotone `min`, but a successful decrease can
trigger worklist insertion and later reads. Final-value commutativity is not
enough. A resident line is legal only if every competing CPU/MAA access is
routed to the same coherent owner, each atomic request is linearized, and its
success/old-value response becomes visible in program order. Therefore the
IS-only delta-combining contract must fail closed for BFS/SSSP.

## Bounded-state requirements

For B, the checked default envelope is the controller's fixed
`2 descriptors x 4 pages`, `2 slots`, `4 misses`, and `4 leases`; integration
must preserve finite response/deferred/writeback records and stall rather than
allocate an unbounded side table.

For C, choose explicit constants before implementation: `N` resident target
lines, `M` acquire/fill MSHRs, `Q` queued atomic updates across all lines, `W`
writebacks/recalls, and `R` response records. Every accepted operation must own
exactly one of those records until its terminal acknowledgement. When a pool is
full the legal actions are backpressure or a serialized acknowledged eviction;
untracked buffering, generation reuse, and unacknowledged dirty eviction are
reject conditions. Per-line queues must also be bounded so a single hot IS
bucket cannot consume unbounded state.

## Smallest honest experiment for C

Run one no-code NAS-IS pair using the same `87230d7` simulator binary,
workload binary, input/class, checkpoint, restored cache state, Row/Offset
configuration, and all other options:

```text
control:   --maa_force_cache_access absent/false
treatment: --maa_force_cache_access
both:      identical --maa_l2_uncacheable/--maa_l3_uncacheable settings
```

The phase must include the MAA histogram construction and the CPU's normal IS
verification/handoff. Record the resolved `config.ini`, commands, binary and
input hashes, terminal status, exact benchmark-output fingerprint, MAA
cache-side/direct-memory packet counts, L3 hit/miss/writeback counters, and the
same physical target-line `WRITE_ADDR_AUDIT` used by the historical probe.

Predeclare these reject rules:

1. **Reject the run** for any mismatch in binary/input/checkpoint/config,
   missing terminal state/final stats, panic, or output/verification mismatch.
2. **Reject mechanism activation** unless the treatment resolves
   `force_cache_access=true` and all histogram `ReadExReq`/`WritebackDirty`
   traffic moves from the direct-memory queue to the cache-side queue.
3. **Reject LLC residency as the next C slice** if treatment target DRAM writes
   divided by unique target lines remains above `2.0x`, or if target DRAM writes
   are not reduced by at least one half relative to the matched control. These
   are feasibility thresholds, not speedup predictions.
4. If the DRAM-write threshold passes but completion/CPU handoff is not
   response-acknowledged, classify the result only as a routing opportunity;
   do not promote it to a correct general C implementation.

Do not use latency to accept this first mechanism test. A later, separately
qualified pair may evaluate timing only after correctness, routing, target-line
amplification, and phase-handoff gates pass.

## Final boundary

A is a real, narrow, coherently backed gather-result pager. B is a bounded
logical-SPD control design awaiting integration. C is a distinct coherent
atomic-output-residency design whose cheapest unresolved question is already
testable with the separate force-cache routing knob. Results from one category
must not be cited as evidence for another.
