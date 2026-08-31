# Independent review of shared response/combiner pressure spilling

Review date: 2026-08-31

Branch reviewed: `f889a8c5d19b085c964f55bcbd46863f4b81bdbd`, with emphasis on
`49984804` through `8ac798e4`.

Evidence reviewed read-only:
`/data1/nier/dx100-runs/2026-08-31-ume-gzz-matched-consumer-r6` and the
candidate-only bitmap diagnostics `2026-08-31-ume-gzz-bitmap-diag-r1/r2`.
No full application or new gem5 simulation was launched, and production code
was not changed.

## Recommendation

**Do not promote the shared-payload mechanism as a general bounded hardware
result yet.** The current GZZ path is functionally credible and its sealed r6
matrix is valid for the simulator at `f331383f`, but two promotion-blocking
problems remain:

1. duplicate-source fanout and shared-pool ownership rely on uncharged,
   zero-time host state/work, so the exact 38.49% storage claim and any
   duplicate-heavy performance attribution are not established; and
2. the separately allowed bounded-global source issuer can deadlock under the
   same fragmented shared-pool pressure that the new spill path was added to
   solve.

The 1.168x GZZ result may be retained as a scoped simulator observation for
the exact `f331383f` r6 model. It should not be presented as performance of the
current fixed-bitmap implementation or of a fully costed shared-pool hardware
implementation until the gates at the end of this review are closed.

## Findings, ordered by severity

### High — duplicate fanout is functionally counted but neither storage- nor timing-bounded

`virtualSourcePayloadWords()` obtains the number of unique source words by
walking the complete Offset chain (`IndirectAccess.cc:2797-2820`). This walk is
performed in the credit predicate, again at issue, and on every stalled retry.
On response arrival, a second whole-chain walk populates sixteen 32-bit
`remaining_word_uses` counters (`IndirectAccess.cc:9930-9943`;
`IndirectAccess.hh:104-110`). None of these walks advances simulated time or
consumes a modeled Row/Offset read port. A line with large fanout can therefore
perform thousands of linked-entry inspections in one simulated call.

The storage ledger does not charge the chosen persistent fanout state. Its
response-slot formula has a retained-word count and one pool pointer, but no
per-source-word fanout counters or equivalent last-use metadata
(`report_maa_storage.py:495-507`). For the r6 geometry, a direct compact
encoding of the implemented counters needs 16 counters x 15 bits x 128
response slots = 30,720 bits (3,840 bytes) per indirect unit. An alternative
encoding may be smaller, but it must be specified, charged, and timed; zero is
not a valid lower bound for the implemented last-use decision.

The same report sizes destination allocator state from the configured
3,072-word combiner (`report_maa_storage.py:508-516`), while shared mode resets
the combiner payload store to the full 3,072 + 1,024 = 4,096-word shared limit
(`IndirectAccess.cc:6260-6275`). The report therefore describes partitioned
allocator metadata around a dynamically shared capacity. It does not identify
the shared free-list/reference ownership needed to move an arbitrary useful
source word into the combiner without allocating a second cell.

The sealed GZZ run does not cover the missing case. Its terminal trace reports
16,384 descriptors, 16,384 shared transfers, and zero rollbacks. Thus every
logical descriptor caused a distinct final source-word transfer; duplicate
fanout and ownership rollback were not exercised. The only focused test for
this change searches source text for symbol names
(`test_ume_two_pass_matrix.py:155-164`); there is no executable adversarial
fanout test.

Impact: the unique-word capacity invariant is plausible as functional C++, but
duplicate-heavy timing can be arbitrarily optimistic and the published
storage lower bound omits required state. Do not use this commit range as
duplicate-fanout performance or area evidence.

Required fix: define one bounded hardware representation for unique-word
membership, fanout last-use, shared allocation, and response-to-combiner
references; add its bits and ports to `report_maa_storage.py`; advance modeled
time for its accesses; and add executable tests with repeated word IDs,
capacity stalls, a failed insertion/rollback, and successful final transfer.

### High — bounded-global shared-pool issue can deadlock without invoking the spill escape

The RowTable build retry path calls
`spillVirtualCombinePartialForSourceCredit()` when a pending source line lacks
shared credit (`IndirectAccess.cc:7348-7373`). The independently enabled
bounded-global merge path uses `issueBoundedGlobalSourceLine()`. On the same
credit failure it records a stall and immediately returns false
(`IndirectAccess.cc:2899-2924`); it neither spills a partial destination line
nor schedules a retry.

This is a reachable terminal wait, not a throughput preference. The
constructor permits `virtual_bounded_global_merge` with
`virtual_shared_result_payload`; only strict two-phase forbids bounded-global
merge. Consider the legal state already demonstrated by GZZ pressure:

- all earlier responses have drained, so there is no future source response to
  wake the unit;
- the shared pool is full of incomplete destination lines;
- there are no full lines and no outstanding writes; and
- the staged bounded-global A line needs at least one source word.

`serviceBoundedGlobalMerge()` drains no legal full line, then the issue helper
returns false. With no response, write ACK, or scheduled one-cycle event, the
operation cannot make progress. GZZ avoided this only because it used the
RowTable pending-source path, where 13 partial spills were issued.

Required fix: route every shared-credit source issuer through one progress
helper. On word-capacity pressure it must perform the same legal partial spill;
on slot-only pressure it must rely on a provably live outstanding response; and
every non-event-backed stall must schedule a retry. Add a bounded-global test
that fills the shared pool with fragments and proves eventual source issue,
write ACK closure, and terminal completion.

### Medium — `report_maa_storage.py` contradicts the shared-mode C++ allocation

Shared mode deliberately configures `VirtualResponsePayloadStore` as unpacked:
the `packedResponse` argument is false whenever
`virtual_shared_result_payload` is true (`IndirectAccess.cc:196-200`). That
store consequently allocates one 64-byte line per response slot. With r6's 128
slots, this is 8,192 host bytes per indirect unit, in addition to the
4,096-word combiner payload store.

The report instead labels the response as a `shared-packed-word-pool`
(`report_maa_storage.py:597-612`), fixes inactive C++ response-line bytes to
zero (`:637-642`), and emits the categorical statement that no fixed response
line is allocated (`:907-910`). The hardware caveat in the GZZ analysis admits
that C++ response arrays are host-side simulation storage, but the report's
"conservative C++ static view" is still factually false. For r6 it omits
32,768 response-line bytes across four indirect units.

This is separate from the hardware lower-bound issue above: it is acceptable
to exclude a simulator shadow from an RTL payload estimate, but it must be
reported as excluded host storage rather than asserted absent. The exact
1,953,744-byte comparable total also has no committed result JSON or invocation
beside the prose. Re-running the current reporter with the three sealed
`config.ini` files reproduces 3,176,448 / 1,391,616 / 1,953,744 bytes, but those
numbers inherit the omissions in this finding and the preceding one.

Required fix: separate `modeled_hardware_payload`, `host_functional_shadow`,
and `conservative_cpp_allocation` fields; make shared allocator capacity use
the total pool; charge the chosen fanout/reference metadata; and freeze the
three generated reports plus commands/hashes with any storage comparison.

### Medium — the fixed-bitmap successor is not the sealed same-binary performance matrix

The sealed r6 manifest records source `f331383f` and simulator SHA-256
`d3885ab0...`, before the dynamic spilled-line `std::set` was replaced by a
fixed bitmap. Its same-binary comparison is internally valid, but its hybrid
arm alone executes host-dynamic spill identity with unmodeled set operations.

The bitmap diagnostic r1 is a useful negative artifact: it panics on an
undersized line bitmap. R2, built after `8ac798e4` with simulator SHA-256
`cd36ea5a...`, exits zero and reproduces the exact output hash, 25,470,375
ticks, 1,037/1,037 write closure, 1,011 full writes, 26 masked writes, four
pages, and all strict timing counters. This supports functional equivalence of
the corrected candidate. However r2 is a loose candidate-only directory: it
has no artifact ledger, source manifest, immutable checkpoint identity, or
fresh native arms using the new binary. It therefore is not a successor
same-binary performance matrix under the repository's evidence rules.

Required fix: seal the bitmap replay with source/tree/binary/checkpoint
identities and either rerun the short matched three-arm matrix with the fixed
binary or explicitly narrow the retained speedup to the old r6 simulator
model. This does not require a long full application.

### Low — a new trace field is malformed in the sealed evidence

The credit-stall event formats `virtual_response_slots.size()` with `%zu`
(`IndirectAccess.cc:7357-7367`). In the sealed trace this renders as
`response_slots=0/<bad format>u` on all 13 stalls. It did not affect execution
or the current classifier, but it makes the schema field unusable by a strict
evidence parser. Use a supported integer format with an explicit cast and add
a trace parse test.

## Audited behavior without an additional finding

- **Response-to-combiner ownership:** on the final use of a source word,
  `begin_shared_transfer` removes one response credit before combiner
  insertion; a failed insertion restores both the fanout count and credit; a
  successful insertion leaves total shared occupancy unchanged. Earlier
  fanout copies increase destination occupancy while retaining the source
  credit. The accounting order is internally consistent. The rollback path is
  untested by r6.
- **Masked spill and fragmented completion:** the escape selects a non-full
  resident line, issues a masked coherent write, releases exactly its
  popcount, and retains a line-identity bit. A later fragment is legal only for
  a marked line. The retirement scoreboard rejects a second transaction with
  the same physical line key until the first ACK, so the two masks cannot race
  in memory.
- **ACK/page readiness:** page counters add only enabled words, WriteResp
  completion advances completed words, and normal page readiness requires
  scanned = logical, issued = expected, and completed = expected
  (`IndirectAccess.cc:10373-10412,10452-10575`). R6 has
  `virtual_idealized_write_ack=false`, 1,037 issues = 1,037 ACKs, and each page
  event has 4,096 completed words. No pre-ACK exposure was found.
- **Fixed bitmap bounds:** current code sizes the bitmap from the configured
  logical tile bound rather than the instruction's smaller dynamic maximum,
  bounds-checks every aligned backing-line index, and requires all bits clear
  at terminal. For 16K FP32 this is 1,024 bits/128 bytes per indirect unit. The
  corresponding new bitmap charge in `report_maa_storage.py` is correct.
- **Fragment closure:** r6 contains exactly 13 pressure-spill events, each
  spilling 15 words, and 26 total masked transactions. Together with 1,011
  full lines, semantic bytes are exactly 65,536 while transport bytes are
  66,368. The terminal output fingerprint and reference both pass.
- **No data race found:** the relevant gem5 callbacks execute through the
  simulator event queue, and source/response/retirement ownership is guarded
  by exact slot, generation, transaction, address, and scoreboard identities.

## Sealed r6 evidence classification

The sealed directory passes the repository evidence checklist for the exact
`f331383f` model:

- every path in `artifacts.sha256` verifies;
- all three restore process records bind boot ID, PID start identity, command
  hash, return code zero, observed end, and absent terminal PID;
- all restore logs end at an `m5_exit`; final stats are nonempty;
- one frozen gem5 binary and Ramulator configuration are used by all arms;
- checkpoint trees remained immutable;
- all arms report output hash `7602200327591349891`, zero volume errors, zero
  gradient errors, and 196,384 checked elements; and
- intended `simTicks` are native16 20,546,885, strict hybrid 25,470,375, and
  native4 29,755,345, yielding 0.806697x versus native16 and 1.168233x versus
  native4.

The correct conclusion is scoped: r6 proves exact functional output and the
reported simulated performance for its matched-consumer, same-binary
configuration. It does not validate duplicate-source fanout, fixed-bitmap
hardware, or a completely costed/timed shared payload pool.

## Focused validation performed

- `sha256sum -c artifacts.sha256` in sealed r6: PASS for every recorded file.
- Current matched-matrix validator inputs/result were independently read and
  recomputed; sealed counters and speedups agree.
- `python3 -m unittest experiments.tests.test_ume_two_pass_matrix experiments.tests.test_report_maa_storage experiments.tests.test_ume_gzz_matched_consumer_matrix`: 30/30 PASS.
- `run_virtual_response_payload_store_unit.sh`: optimized and sanitizer PASS.
- `run_virtual_combine_payload_store_unit.sh`: optimized and sanitizer PASS.
- `run_virtual_combine_lookup_pipeline_unit.sh`: optimized and sanitizer PASS.
- `run_virtual_retirement_scoreboard_unit.sh`: optimized and sanitizer PASS.
- `run_virtual_combiner_page_order_unit.sh`: optimized and sanitizer PASS.
- `run_complete_line_payload_staging_unit.sh`: optimized and sanitizer PASS.
- Current storage reporter rerun against all three sealed configs: PASS and
  reproduces the prose totals, subject to the accounting findings above.

## Gate before promotion

1. Close the bounded-global source-credit deadlock and add an executable
   fragmented-pool liveness test.
2. Add an adversarial duplicate-word fanout/rollback test through actual
   `IndirectAccessUnit` behavior.
3. Specify and charge one realizable shared allocator, fanout/last-use, and
   response-reference design, including access latency and ports.
4. Correct the host-shadow and shared-capacity fields in
   `report_maa_storage.py`, then freeze generated storage artifacts.
5. Seal a fixed-bitmap successor matrix or narrow the performance statement to
   r6 at `f331383f`.
