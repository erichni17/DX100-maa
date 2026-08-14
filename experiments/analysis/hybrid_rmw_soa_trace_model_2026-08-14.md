# Hybrid SoA/JIT RMW trace model (2026-08-14)

## Scope and evidence

This is an analysis-only handoff. It did not change product source and did not
run gem5. All times below are `simTicks`. The inputs are exactly
`/tmp/hybrid-rmw-soa-matrix-20260814-r1`,
`/tmp/hybrid-rmw-soa-matrix-20260814-r2`, and
`benchmarks/API/test_hybrid_rmw_soa.cpp` (SHA-256
`fb6fb40bb09110492b1d6315c432f0fe2ba66330fc523d279b0d15a26cca1bd5`).

Both repetitions have the same four matrix observations, terminal `m5_exit`,
`errors=0`, exact expected/output hash `2761840269561229581`, and identical SoA
trace SHA-256
`a6cc78c9489dc24e4e24f29a0858a6b57e9c3f7db2e112613ac452ec9f25fb46`.
The physical-16 and physical-4 SoA traces are also byte-identical. The measured
ROI observations are 20,497,744 (ordinary/native-16), 28,522,125
(ordinary/native-4), and 807,878,353 for both SoA geometries. Ordinary versus
SoA changes API staging and old-value output, as the matrix itself records, so
these are context observations rather than a speedup pair.

Provenance limitation: each manifest names source commit
`0d507155e70c168b179604b3e9d45d4aac34c8b9`, but that commit does not contain
the guest source file. The guest binaries are bound by the manifest hashes; the
logical reconstruction is separately bound to the source hash above and is
cross-checked against every trace head/alias count and terminal counter.

Independent source-review gate: session
`hybrid-soa-jit-independent-review-20260814-20260814-084119-62e5f45b`
returned **BLOCK proceeding to a multi-context optimizer** on clean
`d7a9f403`. Its blockers are incomplete global span ordering across CPUs/MAAs,
drain/checkpoint reporting Drained with active functional-unit/packet/SoA state,
an FP64 completion token that owns two SPD tiles but guards one, unaligned typed
span overread/misdecode risk, and mid-operation stats-reset accounting. The
review passed the bounded one-context state and exact trace lifecycle evidence,
but these matrices are not promotion evidence and the ROI observation must be
remeasured after the reset-accounting defect is repaired.

## Measured one-context decomposition

The 126 A-line intervals pair each `soa_jit_a_read_issue` with the matching
`soa_jit_a_write_issue` and `soa_jit_a_write_response`. There are 63 unique A
lines in each of two operations, 29,689 selected value reads, 2,048 index-line
reads, and 2,048 predicate-line reads.

| Component | simTicks | ROI share | Meaning |
|---|---:|---:|---|
| Pre-first-A index/predicate build | 302,556,755 | 37.451% | fill stages plus the two 313-tick A-launch transitions |
| Post-first-A A/value service | 504,007,624 | 62.386% | all 126 A read-to-WriteResp intervals |
| Front-end/inter-instruction residual | 1,313,974 | 0.163% | ROI minus both traced instruction summaries |
| **ROI** | **807,878,353** | **100%** | exact closure |

The pre-first-A intervals are 151,265,701 and 151,291,054. Within that phase,
the traced fill stages total 302,556,129 and the A-launch build transitions total
626. From fill completion through final A/value completion is 504,008,250; from
the first A issues onward it is 504,007,624. The latter decomposes into
502,272,352 through A write issue and 1,735,272 waiting for WriteResp.
The 126 complete A-line service min/mean/max are
2,919,038 / 4,000,060.5 / 23,819,613. The critical line is target line zero:
it contains 1,024 ordered aliases and costs 23,819,613 in operation 0 and
23,756,387 in operation 1.

The index events show exactly one outstanding line, 2,048 issue/response pairs,
and 151,180,878 aggregate response simTicks (49.968% of the fill stage). The
configuration sets `virtual_index_buffer_lines=1`; the code bounds its window
by that setting. Predicate ingestion has one `SoaPredicateLine`, and does not
issue the next line while that slot is pending. Because predicate address is a
function of logical iteration, it can be prefetched independently and consumed
in order; the present implementation waits for the index before requesting it.

The predicate responses are not individually timestamped, so the script keeps
the remaining 151,375,251 fill simTicks unclassified. The following **optimistic
feeder projections** are brackets, not measurements. “Index-only” divides only
the measured index-response work by the credit count and holds the remainder
fixed. “Dual-component” also divides the unclassified remainder, but still adds
the two components rather than assuming cross-stream overlap.

| Credits per modeled feeder | Index-only fill | Dual-component fill |
|---:|---:|---:|
| 1 | 302,556,129 | 302,556,129 |
| 2 | 226,965,690 | 151,278,065 |
| 4 | 189,170,471 | 75,639,033 |
| 8 | 170,272,861 | 37,819,517 |

Thus the requested index8 probe is bounded at 170,272,861 fill simTicks when
predicate/control work stays serial. Letting both modeled components scale at
eight credits gives 37,819,517, but is much more optimistic because some of the
unclassified remainder is control/Row/Offset work. With the C8 LPT A-service
projection, those two fill cases give total envelopes of 236,790,056 and
104,336,712 respectively. Neither is a gem5 result.

## Reconstructed order and value locality

Offset entries are allocated in selected guest-iteration order. Before any A
line is claimed, each trace `head` therefore identifies the first selected
ordinal in that A line; the linked Offset chain retains increasing guest order.
All 126 heads and alias counts match the deterministic index and predicate
formula. The complete 29,689-row sequence
`(operation, target_line, logical_itr, value_line)` has SHA-256
`09373d92fa6f1c5c9ca245f961fbe1523360dc04981a207cfe6c2564e4ef6574`.
The script can emit it as TSV.

The table below is an initially empty, fully associative LRU model dedicated to
64-byte value lines. “Physical fills” means one fill per modeled miss; it is a
projection, not an observed cache counter. It excludes A/index/predicate
pollution, tags, hit latency, prefetch, and coherence.

| Ordering | Cache lines | Physical fills | Avoided vs no cache | Fill bytes |
|---|---:|---:|---:|---:|
| Trace Row/Offset | 0 | 29,689 | 0 (0%) | 1,900,096 |
| Trace Row/Offset | 4 | 28,153 | 1,536 (5.174%) | 1,801,792 |
| Trace Row/Offset | 8 | 28,153 | 1,536 (5.174%) | 1,801,792 |
| Trace Row/Offset | 16 | 28,153 | 1,536 (5.174%) | 1,801,792 |
| First-alias A-line order | 4/8/16 | 28,153 | 1,536 (5.174%) | 1,801,792 |
| Source-stream floor | 4/8/16 | 2,048 | 27,641 (93.102%) | 131,072 |

The first-alias permutation preserves each A word's update order and gives the
same result as the trace order for these cache sizes. The source-stream floor
also preserves update order but is not feasible for the current one-context,
one-read/one-write-per-A-line engine: it would require many live A lines or A
revisits. It is only a locality bound. In particular, the critical target-zero
chain touches all 1,024 value lines once per operation, so a 4/8/16-line demand
cache cannot shorten that compulsory chain without value prefetch/lookahead.

## Optimistic A-context scheduling

For each operation independently, the script treats measured A-line service
intervals as independent immutable jobs. The lower column is
`max(sum/C, longest job)` per operation; the LPT column greedily assigns longest
jobs to the least-loaded context. Both retain the entire measured fill,
front-end residual, and build work as fixed serial time.

| A contexts | Ideal total lower bound | LPT total | LPT A-service only |
|---:|---:|---:|---:|
| 1 | 807,878,353 | 807,878,353 | 504,007,624 |
| 2 | 555,874,541 | 558,132,836 | 254,262,107 |
| 4 | 429,872,635 | 430,482,672 | 126,611,943 |
| 8 | 366,871,683 | 369,073,324 | 65,202,595 |
| 16 | 351,446,729 | 351,446,729 | 47,576,000 |

These are optimistic projections, not simulated performance. Even with the
entire 302.6M fill deleted, the idealized LPT totals remain 66,517,195 at C8
and 48,890,600 at C16 because the two 1,024-alias critical chains serialize.
Thus C8 alone cannot make this design competitive with the measured
ordinary/native-16 observation, and no speedup is claimed.

## Limits omitted by the simple model

The schedule assumes interval costs remain unchanged while contexts overlap.
It omits shared MAA request ports, cache/MSHR and write-credit limits, DRAM
channels/banks, response arbitration, A WriteResp backpressure, context lookup,
and scheduling overhead. It also reuses single-context intervals that already
contain the actual cache/memory behavior; concurrency can increase contention
or change hit rates. Each context has a strictly serial value response chain,
so extra A contexts cannot parallelize the longest chain. The fixed serial fill
contains 4,096 64-byte input-line responses (262,144 bytes), and the service
phase logically requests 29,689 value lines plus 126 A reads and 126 A writes;
the cache table must not be added to these counts as measured DRAM traffic.

## Recommendation for the next measured implementation

After the independent BLOCK items and ROI reset accounting are repaired, the
minimum informative configuration is **8 bounded A contexts, a 4-line
value cache, 8 direct-index line credits, and 8 independent predicate-line
credits**. Eight 128-byte-max A contexts cost at most 1,024 payload bytes; the
four value lines cost 256 bytes; and the 8+8 feeder windows cost 1,024 payload
bytes, plus tags and control. Predicate responses must be tagged by logical
line and consumed in order. Credits must remain parameterized and fail closed
at their bound.

Do not begin the multi-context optimizer or treat this as promotion authority
before that gate is cleared. This is a later measurement recommendation, not a
competitiveness prediction. The
4-line cache is the minimum because 8 and 16 give no additional fills avoided
under either feasible A-line ordering. The 8+8 feeder is mandatory in the same
measurement: the existing fill alone is 14.76 times the native-16 ROI, so a
context-only experiment answers the wrong bottleneck. Measure a 1/2/4/8 feeder
credit sweep and retain per-stream issue/response/HWM/stall counters. Also
include a C16 follow-up and bounded value lookahead/prefetch, because the C8
ideal service remains 65.2M and the compulsory target-zero chain sets a 47.6M
two-operation floor in this interval model.

Reproduce without launching gem5:

```bash
python3 experiments/analysis/analyze_hybrid_rmw_soa_trace.py \
  /tmp/hybrid-rmw-soa-matrix-20260814-r1 \
  /tmp/hybrid-rmw-soa-matrix-20260814-r2 \
  --emit-sequence /tmp/hybrid-rmw-soa-value-sequence.tsv
```
