# 4K Selected-Subset Reorder Versus Full 16K Metadata

## Decision

For the present direct-index gather, prefer a 4K physical SPD with a full 16K
Row/Offset metadata lifetime if recovering the 16K A-side reorder window is the
goal.  A selected-subset scheme can be made correct, but it replaces 189,344 B
of on-chip lower-bound storage with at least four B scans, a selector, and LLC
capacity/traffic.  It is not the same mechanism as the existing grow-partition
experiments, and current measurements give no reason to enable it by default.

This is a design analysis, not a gem5 result for a new treatment.  No gem5 run
was launched.

## Exact proposed mechanism

For one logical gather `C[i] = A[B[i]]`, let `N=16,384` and let the active
Row/Offset capacity `K=4,096`.

1. **Pass selection.** Define a deterministic, total function `p(i, grow) ->
   {0,...,P-1}` before the first result is issued.  In pass `p`, stream all B
   cache lines sequentially in 4K-sized feeder chunks; calculate `grow` for
   every live B word; retain only entries whose selector equals `p`; discard the
   remaining temporary descriptors.  The B words are not silently retained in
   SPD.  To revisit them they must remain resident in LLC or be fetched again.
2. **Grouping and issue.** Build no more than `K` Offset entries and `K`
   Row-table entries for the selected records, group by A cache line/DRAM row,
   issue A reads in the selected Row-table order, and preserve each result's
   logical index `i` until retirement.
3. **Retirement and next pass.** Merge results into the C-line combiner but do
   not flush partial C lines at a pass boundary.  After all selected responses
   and all required writes complete, advance to the next selector value.  Flush
   only at final completion.

The information that must survive a pass boundary is: selector epoch; B scan
position and request tags; active Row/Offset descriptors; the bounded B feeder;
A response `(i, word)` records; C-combiner data/dirty masks and outstanding
write tags; the completion count; and an exactly-once bitmap over `i`.  A
selector label needs `ceil(log2 P)` bits per item if it is materialized; an N=16K,
P=4 label array is 4 KiB and the completion bitmap is 2 KiB.  A discovered
row-group-to-pass map needs keys, counts, and assignments as well; it is not
represented by the 4K active Row/Offset store and must be bounded or external.

The small checked model is
`experiments/analysis/hybrid_reorder_cost_model.py`; it intentionally reports
traffic and minimum state, not a performance prediction.

## Passes, bandwidth, and selection policy

Element capacity alone gives `P >= ceil(N/K) = 4`.  Four passes therefore read
the 64 KiB B vector four times: 65,536 B words, 4,096 64-B LLC line reads, and
196,608 additional B bytes relative to a one-pass full-metadata design.  The
first scan may obtain 64 KiB from memory; the remaining three cost LLC bandwidth
only if all 1,024 B lines stay resident and are not invalidated.  If they do not,
the worst case is four 64 KiB memory fetches.  The present partition machinery
counts every examined word: at P=4 a static finite selector must process 65,536
words.  At 16 words/cycle that alone has a 4,096-cycle serialization lower
bound; the balanced prepass raises it to 81,920 words.  An unlimited filter must
not be reported as free hardware.

There are only two honest policy families.

* A static policy such as `grow % P` needs no discovery pass, but is not
  capacity safe: all useful descriptors can map to one pass.  That pass must
  dynamically drain, split the group, or overflow; dynamic drains lose the
  asserted one-16K reorder window.
* A balanced policy first counts row-group weights, assigns groups (or pieces
  of an oversized group) to capacity-bounded passes, then rescans B.  It needs
  at least one extra full scan (five total here), a group-to-pass map, and a
  deterministic tie/split rule.  Spilling a 16-B descriptor for every
  non-retained item would add 192 KiB of LLC writes and 192 KiB of LLC reads in
  this 16K/4K example; it is an external metadata sorter, not free retention.

An adversarial single grow group has 16K selected records.  A four-way row hash
puts all of them in one partition; a balanced policy must split that group into
four subpasses.  This remains correct, but it does not provide a monolithic
16K descriptor lifetime.  It also proves that `P=4` is only an element-count
lower bound, not a guarantee for an arbitrary row-only selector.

## Correctness and ordering contract

Each live input position is selected exactly once:
`sum_p [p(i,grow_i)=p] = 1`.  The selector must be stable across retries and
must include any predicate rule: false predicates are recorded once with their
architecturally defined no-write or initialized result, not re-retired in every
pass.  Each returned A value carries `i`; the C combiner writes `C[i]` and the
exactly-once bitmap rejects a duplicate or missing retirement.  Completion
requires every logical `i` to be accounted for, no outstanding A/read/write
tag, and a final C-combiner drain.  This makes A issue order deliberately
different from C architectural order.

Keeping the combiner across passes is mandatory for comparable C traffic.  The
existing experiment demonstrates why: flushing it at each barrier alters dense
C write geometry.  This state is additional live state, not an implementation
detail that can be discarded while claiming equivalent bandwidth.

## Distinction from existing experiments

The existing `virtual_index_partitions` implementation does **not** retain a
selected subset across B chunks.  In `IndirectAccess.cc`, each partition resets
`my_i` to zero and rescans the full B stream; the selected predicate is
`grow_addr % partitions == partition`.  Its Row/Offset state is drained as it
fills, and its optional `virtual_partition_keep_combiner` only preserves C-line
combiner state across the barrier.  It has no balanced selector map, no
pre-classification pass, and no mechanism that guarantees a four-pass 16K
reorder reconstruction.

That distinction matches the measured FLAG00 partition-count campaign
(`/data1/nier/dx100-runs/2026-07-29-flag00-partition-count-05f390c`): P=1/P=2/P=3
had 31,923/63,846/95,769 direct-index B words and 37,918,072/38,516,528/
38,495,870 ROI ticks, respectively.  P=2 and P=3 produced the same exact output
hash as P=1, but were +1.578% and +1.524% versus P=1.  Those runs had zero
charged filter cycles because their filter throughput was unlimited, so they do
not pay the finite-selector lower bound above.  The separately summarized
four-partition result was +7.260% versus full descriptors on FLAG00.  These are
correctness-checked measurements of grow-modulo rescans, not measurements of
the balanced retain/spill algorithm specified here.

## Comparison with 4K SPD plus full 16K metadata

The current 4K-SPD/full-metadata ledger is 842,482 B configured comparable
lower-bound storage: 512 KiB physical SPD, bounded 8 KiB B feeder, 3.75 KiB A
response pool, 24 KiB C combiner, 10.47 KiB virtual control, and 254,464 B of
retained Row/Offset/invalidator metadata.  The fully bounded 4K epoch ledger is
653,138 B with 66,688 B retained metadata.  Retaining full metadata therefore
costs 189,344 B (185 KiB) in this ledger; it avoids selector labels/maps and
allows one B pass and one 16K A reorder lifetime.  Both are lower bounds, not
synthesized area/power estimates.

Measured evidence favors keeping that metadata when A-side locality is the
target.  On the representative FLAG gather, the full-descriptor direct4 point
took 36,662,629 ROI ticks and the fully bounded 4K point took 37,737,471
(full metadata is 2.93% lower).  Across 14 FLAG gathers, shrinking only Offset
storage at a fixed 4K schedule changed ticks, writes, DRAM commands, and MAA
issue traces by exactly 0.000%; changing the epoch, not capacity, changed the
schedule.  This means the 185 KiB comparison is a real capability/cost choice,
not evidence that small metadata is intrinsically faster.

XRAGE shows the countervailing, mechanism-specific effect.  The full 16K
Row/Offset direct4 arm had 1,160,968,019 ROI ticks and 342,732 C retirement
writes.  A separate controlled scheduling comparison held Row capacity at 4K
and changed the Offset epoch from 16K to 4K: the 16K-epoch arm had 302,676
inserted A cache-line descriptors, 39,830 rows, and 327,924 writes; bounded4
had 322,414 lines, 43,452 rows, and 262,903 writes at 1,083,316,475 ticks.
Thus the bounded schedule won XRAGE by changing C coalescing despite worse
A-side locality; it did **not** reconstruct a 16K A reorder window.  A
multi-pass hybrid must be compared to both signatures: it should reduce
repeated A-row work relative to the 4K epoch *and* charge at least its B
scan/filter/LLC work.  No current run provides that evidence.

Sources: the checked campaign notes `mechanism_audit.md`,
`descriptor_capacity.md`, `offset_capacity_epoch.md`, `bounded_window_followups.md`,
`xrage_full_attribution.md`, and the storage reports under
`/data1/nier/dx100-runs/2026-07-29-xrage-full-direct-control-digest-b9e0ca2/`
and `/data1/nier/dx100-runs/2026-07-29-flag-matched-offset-epoch-broad-3b50cdb/`.
