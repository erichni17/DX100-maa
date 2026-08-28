# Fixed-16-line combiner reuse analysis (2026-08-27)

## Decision

**Reject tree-PLRU and operation-sized lookahead hardware. Live-test the
already implemented most-filled victim policy, but promote it only if measured
`simTicks` improve.**

The accepted NA=1024 strict line-combined arm uses 16 cache-line slots arranged
as four 4-way sets. It writes 1,064,960 semantic P words through 358,114 masked
64-byte transactions. A fresh trace-only simulator build added one debug event
after each successful combiner insertion; it did not alter timing state or the
architectural result. The rerun reproduced the accepted result exactly at
`2,213,855,573 simTicks` and reproduced all 358,114 round-robin writes in the
offline model.

## Evidence

- run root:
  `/data1/nier/dx100-runs/2026-08-27-lead-combiner-insertion-trace-na1024-r1`;
- simulator source commit: `46dda087` plus experiment-only commits;
- result SHA-256:
  `d6425bf2c3965c7e0832224b83336449c41731c612f4a76ddedc767169faa9ed`;
- reuse report SHA-256:
  `31cf33dc3711d4a145f96125c8c143a43829efc24b56cdd3084954fdb254f189`;
- operations: 65;
- insertion events: `65 * 16,384 = 1,064,960`;
- fixed payload geometry: 16 lines, four ways, four sets.

Every operation contains each logical result word exactly once. The analyzer
fails on duplicate words, malformed operation lengths, incomplete word
closure, or a round-robin count different from the observed 358,114.

## Replacement ceiling

| Fixed-16-line policy | Writes | Reduction vs. RR |
|---|---:|---:|
| Round-robin | 358,114 | 0% |
| LRU | 358,114 | 0% |
| Tree-PLRU | 358,114 | 0% |
| Fewest-filled victim | 354,750 | 0.939% |
| Most-filled victim | 349,673 | 2.357% |
| Exact 32-entry next-use window | 347,146 | 3.063% |
| Exact 128-entry next-use window | 338,072 | 5.596% |
| Exact 512-entry next-use window | 314,074 | 12.296% |
| Set-constrained offline Belady | 313,895 | 12.345% |
| Infinite line capacity | 66,560 | 81.414% |

The 512-entry window is effectively Belady-optimal for this trace, but it is
not a free replacement policy. It requires hundreds of future destination-line
tags plus a search/prioritization mechanism. A 128-entry queue alone needs at
least 1,280 tag bits (160 bytes) for 1,024 possible output lines, before valid,
ordering, and search state. A 512-entry queue needs at least 640 tag bytes.
Neither cost includes the comparator/search network or cycles needed to scan
the window.

Tree-PLRU would require only three bits per 4-way set (12 bits total), but the
exact replay shows no transaction reduction. It is therefore rejected before
implementation.

### Set-index hashing

Simple XOR-folded set indices were replayed over all 65 windows to test a
zero-storage conflict reduction. With the most-filled victim policy, the
current low-bit mapping needs 349,673 writes; XOR shifts 6 and 8 need 349,471
and 349,241 respectively. The best mapping removes only 432 transactions
(0.124%). Belady changes by just 45 transactions across the same mappings.
Address hashing is therefore also rejected before implementation. The sealed
set-hash report is `combiner_set_hash.json` in the run root, SHA-256
`37b85c284f7680120a1cb288f884e2d3796e6fa2841e638740fb579d49a0d006`.

## End-to-end priority

Changing 1,064,960 word writes into 358,114 line writes saved 172,311,821
ticks. Linear interpolation is not a performance claim, but it provides a
useful upper-priority estimate for further transaction reductions:

| Policy | Additional writes removed | Linearized share of current runtime |
|---|---:|---:|
| Most-filled | 8,441 | 0.093% |
| 128-entry lookahead | 20,042 | 0.221% |
| Offline Belady | 44,219 | 0.487% |

Thus even perfect replacement within the current four-way geometry is unlikely
to recover another percentage point end-to-end. The existing most-filled
policy adds no state and remains worth one live same-checkpoint test. New
lookahead hardware is not justified unless a live experiment or another
workload demonstrates substantially larger end-to-end sensitivity.

## Scope

This is a transaction-replacement analysis for CG's virtual P-result backing.
The cross-application audit rejects this edge for IS and HashJoin and separates
it from SSSP's semantically required old-result publisher. No native baseline,
full CG workload, or additional application was launched for this analysis.
