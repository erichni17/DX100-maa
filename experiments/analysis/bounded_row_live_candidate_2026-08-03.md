# Bounded-row live candidate handoff — 2026-08-03

## Decision boundary

This is a **functional but poorly calibrated candidate mechanism**. It is not a
final architecture, a promotion recommendation, a performance result, or
Scott's decision. The live smoke proves bounded execution and exact semantic
accounting for one 16K logical gather. It does not prove that the four fixed
global grow ranges provide useful four-way reordering.

The decisive observation is the pass distribution `16384/0/0/0`. All useful A
requests fell in pass 0; passes 1–3 performed empty B rescans. A next range
policy should anchor bounds to the source region or to an explicitly bounded
observed min/max or histogram before any performance claim.

## Audit basis

Work started at the requested merge commit
`5d0215da84864b423cb50f2f3fc2734f5c8be06f`.

The grounded model in
`experiments/bounded_row_study_2026_08_03/bounded_row_model.py` represents the
finite mechanism with fixed arrays: 4,096 Offset slots, 16 RowTable slices,
32 rows per slice, eight line slots per row, 96 response slots, and a
480-word response pool. It validates indices and decoded geometry before
mutating state, drains finite occupancy, and charges each field independently
at byte granularity. Its earlier modulo policy can assign every request to a
deterministic bucket but cannot make a fixed global bucket well calibrated to
the workload's occupied address region.

The native implementation in `src/mem/MAA/Tables.hh` and `Tables.cc` has these
semantics:

- `OffsetTable` is finite. Allocation seeds a free-entry stack; insertion
  stores `(logical iteration, word id, next)` and appends to the cache-line's
  linked Offset chain. Entries return to the free stack only when the chain is
  consumed or explicitly freed.
- A `RowTableEntry` groups cache-line addresses under a decoded grow. Each line
  stores the head and tail of its Offset chain. A row or line capacity miss
  returns failure and requires a drain; it does not create overflow storage.
- Virtual claims can release their RowTable line slot when the request is
  committed, while the Offset chain remains the response-placement authority
  until consumption. Native-order claims retain explicit claimed state and are
  released against the same row, line, address, and chain head.

The live candidate therefore uses the native tables as the active finite
working set rather than placing a hidden 16K row/offset structure beside them.

## Exact candidate mechanism

`--maa_virtual_index_range_passes` is opt-in and defaults off. When enabled for
a 16K logical gather, the configuration fails closed unless:

- Offset capacity and Offset epoch are each at most 4,096;
- RowTable line capacity (`slices * rows * entries`) is at most 4,096;
- at least `ceil(logical / Offset capacity)` passes are configured;
- direct-index accesses are forced through the cache hierarchy;
- range filtering has nonzero throughput, grow ordering is enabled, the
  combiner is retained across passes, and native issue order is disabled.

For this slice, four deterministic half-open ranges divide the entire 65,536
grow-code space: `[0,0x4000)`, `[0x4000,0x8000)`, `[0x8000,0xc000)`, and
`[0xc000,0x10000)`. Every pass rescans all 16K B indices through the LLC-visible
direct-index path and admits only A addresses whose decoded grow falls in that
range. This preserves a logical-gather-wide address-range ordering opportunity
without retaining the 16K request payload or 16K Row/Offset metadata on chip.
The 4K combiner remains live across pass barriers so values retire by their
architectural logical iteration and are written to the ordinary paged output
backing.

`BoundedRangePassTracker` separately records exact admission and retirement
for every logical iteration. It rejects invalid geometry, wrong-pass
admission, duplicate admission, retirement before admission, duplicate
retirement, premature pass completion, and incomplete final completion. The
simulator panics on any rejected transition. Structured begin, per-pass, and
final events expose the active capacities, ranges, counts, backing choice,
and checker charge.

No logical index, A address, RowTable entry, cache-line descriptor, or result
payload is stored in the tracker. The B array is the explicit coherent backing
for rescans; output values use the existing bounded combiner and paged
destination backing.

## Review fixes preserved

- The consumer runner builds `offset_args=()` and passes each Offset override
  only when its environment value is nonzero. Sentinel zero therefore preserves
  the SimObject defaults (16,384 in the ordinary runner path) instead of
  overriding them with zero. A contract test covers the default path.
- The stricter no-refill gate is scoped only to range-pass mode. With range
  passes off, `refill_allowed` uses the pre-existing expression exactly:
  `!virtual_native_issue_order || (!virtual_build_incomplete &&
  boundedSourceResponsesComplete())`. Contract tests cover both branches.
- Size-valued trace fields use gem5-compatible formatting; the physical trace
  validator rejects malformed fields rather than accepting them.

## Storage accounting

These are model charges, not synthesized area or C++ heap-size claims.

| Item | Bound | Charged bytes |
|---|---:|---:|
| Offset entries | 4,096 | 24,576 |
| Row descriptors | 512 | 2,048 |
| RowTable line descriptors | 4,096 | 65,536 |
| Response slots and 480-word pool | 96 slots | 6,720 |
| Logical invalidator bits | 32,768 byte-rounded bits | 32,768 |
| Partition/cursor/scalar control | fixed | 588 |
| Candidate model ledger | | **132,236** |
| Verifier-only simulation state | two 16K bitmaps, two 64-entry counters, 64 pass flags | **4,672** |

The **132,236 B** figure is the candidate model ledger. The exact-once checker
is separate fail-closed simulation validation state, not required production
hardware unless a later design explicitly chooses to build it. Its 4,672 B
consist of 4,096 B for two 16,384-bit maps, 512 B for two arrays of 64
`uint32_t` counters, and 64 B for pass-finished flags. Scalar C++ object and
allocator overhead are excluded. The grounded ledger intentionally charges
each field array element independently in bytes and includes the prior model's
byte-rounded invalidator line maps across 32 default 4-byte SPD lane tiles; it
is conservative relative to cross-field bit packing. Architectural destination payload, coherent B
backing, cache capacity, and simulator object overhead are not metadata
claimed by the model ledger.

## Live smoke evidence

The accepted evidence is rooted at:

`/data1/nier/worktrees/codex-coordination/sessions/bounded-row-live-candidate-20260803-182833-c48e5161/evidence/paged_4k_range_8a3a1e60`

Identity:

- source commit: `8a3a1e6050315a745f4857251c7528dc604b043c`
- `gem5.opt` SHA-256:
  `e4ad9750862f4198ca5a8a3aac7880aa374b4a5a054750a708a879920303ed9b`
- workload SHA-256:
  `2abec459dc16041ce5d65cc98d76e5bdf9359afea7249c50982ed65ac9323925`
- runner SHA-256:
  `f4ffed0751049eb492341fb2c145a869e28b6f022fe7e6b8a0e853af1fd5719b`
- tracker SHA-256:
  `e1a0ce3136414c894b3db676bc614809a085d975ee892f9b2b5cf507a6c01783`
- `IndirectAccess.cc` SHA-256:
  `1a1f9a6c709c1109ae77fb7ee5688bc087190cd59e5c7e1a8dd309da9c83e63f`

The checkpoint and restore exit codes are zero, the runner pass marker exists,
and the terminal cause is `m5_exit` at tick 3,872,115,488. The measured ROI has
`simTicks=77016154` and `simInsts=2859`; these values are recorded for
provenance only, with no baseline or speedup claim.

Correctness evidence:

- exact output:
  `VIRTUAL_TILE_CONSUMER_RESULT mode=paged page_elements=4096 hash=7228541527853630339 errors=0`
- 16,384 physical records, ascending logical-iteration normalization;
- record digest:
  `b756ee96179d3ae0d497a65f43f329cde02df07059b3967aa47b5f9be6957e15`;
- raw JSONL SHA-256:
  `9fc07a140db7815c938f393fd215e2ec2aa1f853fad0b5540bcaf7f2a6c080a7`;
- 16,384 admissions, 16,384 retirements, zero duplicate admissions,
  zero duplicate retirements, and zero missing iterations;
- generation was unavailable for all 16,384 records and is explicitly marked
  unavailable rather than synthesized.

The exact pass events are:

| Pass | Grow range | Admissions | Retirements |
|---:|---|---:|---:|
| 0 | `[0x0000,0x4000)` | 16,384 | 16,384 |
| 1 | `[0x4000,0x8000)` | 0 | 0 |
| 2 | `[0x8000,0xc000)` | 0 | 0 |
| 3 | `[0xc000,0x10000)` | 0 | 0 |

This is exact but badly skewed. The ranges did not partition this workload's A
addresses. The smoke is evidence of safety and liveness under skew, including
one RowTable-full event and 242 build rounds, not evidence of useful four-way
reordering.

## Traffic accounting

- Four B scans inspected 65,536 index words = 262,144 semantic bytes.
- The unaligned B span occupied 1,025 cache lines per pass, so the live run made
  4,100 LLC-visible line requests = 262,400 line-request bytes.
- Relative to one scan, the three empty useful passes added 49,152 word
  inspections = 196,608 semantic bytes and 3,075 line requests = 196,800 bytes.
- The filter charged 65,537 words and 4,101 cycles; the one extra word relative
  to feeder delivery is the RowTable-full retry. It recorded five wait events
  and five wait cycles.
- A-side/source counts were 16,384 source reads, 16,384 RowTable cache-line
  insertions, 2,929 inserted rows, 9,523 unique lines, and 129 unique rows.
- The direct-index feeder high-water mark was 64 words (four cache lines).
  Response high-water marks were 96 slots and 96 words, with zero response-pool
  stalls.
- Aggregate MAA cache/memory counters were 5,107 L3 read hits, 1,026 L3 read
  misses, and 1,245,376 memory bytes read. These counters include MAA traffic
  beyond B rescans and must not be presented as B-only traffic.

## Validation performed

- `experiments/scripts/run_bounded_range_pass_unit.sh` — PASS. Covers uneven
  range coverage, exact reverse-order retirement of all 16K iterations,
  all-16K-in-one-range skew, and fail-closed invalid/duplicate/missing cases.
- Focused Python contract suite — 30 tests PASS. It covers range-mode wiring,
  trace schema, both refill branches, runner default Offset sentinels, and the
  existing transparent/DX contracts.
- Grounded bounded-row model suite — 26 tests PASS.
- Shell syntax checks for both touched runners — PASS.
- `git diff --check` — PASS.
- `scons --ignore-style build/X86/gem5.opt -j4` — PASS; no build used more than
  four jobs.
- One lightweight `paged_4k` gem5 smoke — PASS as detailed above. No full
  workload was launched.

Two earlier smoke attempts remain negative evidence:

- Commit `415ad845` exposed a same-tick capacity-drain refill spin near logical
  iteration 7,660. The fix gates refills only in range-pass mode, preserving the
  legacy branch.
- Commit `e2e0e6dc` reached exact simulator completion, but `%zu` produced a
  malformed trace token and the external validator failed closed. Commit
  `8a3a1e60` emits parseable sizes and produced the accepted run.

## Ramulator provenance

The three ignored paths used by the final build are real local copies, not
symlinks:

- `ext/ramulator2/ramulator2/ext/spdlog/include`
- `ext/ramulator2/ramulator2/ext/yaml-cpp/include`
- `ext/ramulator2/ramulator2/libramulator.so`

They were copied exactly from
`/data1/nier/worktrees/DX100-integrate-hybrid-pingpong-20260803/ext/ramulator2/ramulator2`.
The two include trees and library were byte-identical to the previously linked
inputs, so the replacement alone did not require restarting the full build.
The library SHA-256 is the required
`76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753`.
All three remain ignored/untracked. An initial interactive SCons invocation
briefly generated standard hooks in the shared `/data1/nier/DX100/.git`; the
exact generated hook files were immediately removed and verified absent. No
source or persistent artifact under `/data1/nier/DX100` was changed.

## Limitations and integration conflicts

- Fixed global grow ranges are the central calibration failure: `16384/0/0/0`
  makes three scans pure overhead. Source-relative bounds or a bounded observed
  min/max/histogram policy is required before a performance experiment.
- The exactness tracker contains full-logical bitmaps. Its 4,672 B are reported
  separately as verifier-only simulation state, not architectural cost and not
  an unreported 16K payload store. A hardware proposal would have to explicitly
  choose to build, compress, or omit this checker.
- LLC-visible rescans are coherent cache requests, not guaranteed LLC hits.
- The retained 4K combiner and destination-page backing are required parts of
  the mechanism. They do not preserve hidden 16K Row/Offset metadata.
- Only one synthetic paged consumer was run. There is no paired baseline, no
  balanced address-range workload, no full benchmark, and no performance or
  promotion evidence.
- Changes overlap integration hotspots in `MAA.py`, `MAA.hh`, `MAA.cc`,
  `IndirectAccess.hh`, `IndirectAccess.cc`, `Options.py`, `MAAConfig.py`, and
  `run_virtual_tile_consumer_case.sh`. In particular, reconcile against the
  architecture-design lead's MAA work, live SPD cache checkpoint `14feab56f6aa`,
  and ping-pong runner work rather than applying these files wholesale.
- Hybrid-tail instrumentation checkpoint `d307b2317fc5` and its subsequent
  report checkpoint `b34d1f859217` are adjacent evidence only and were not
  merged into this candidate.
