# SSSP conflict-tolerant old-result routing audit (2026-09-01)

## Findings and decision

1. **GO for a default-off functional prototype, but not for unconditional
   rejection removal.** Cross-window/cross-owner destination aliases do not
   need rejection for *final-distance correctness* when every destination
   operation is a coherent, linearizable MIN and the old-result/reconstruction
   conditions below hold. An explicit iteration-wide source snapshot makes
   active-source aliases safe by turning the routed wave into a legal stale
   relaxation wave. Repeated pushes repair any propagation deferred by that
   snapshot.

2. **Coherent MIN serialization alone is insufficient for the current
   reconstruction.** Within each physical page, duplicate aliases must apply in
   original lane/offset order. With original candidates `[5, 7]`, initial
   distance 10, and application order `[7, 5]`, the current “last original
   alias” formula reconstructs 7 and pushes 7 although memory ends at 5. The
   executable model contains this counterexample. The current SoA/JIT mechanism
   is intended to meet the stronger condition: aliases apply in Offset-chain
   order and old values are captured immediately before the ordered RMW
   (`IndirectAccess.cc:5380-5434,5456-5485`), while result placement uses the
   original logical ordinal (`SoaJitOldResultBuffer.hh:13-18,92-139`). A future
   conflict-tolerant path must preserve and test that contract; “cache coherent”
   is not a substitute for it.

3. **The proposed source snapshot does not exist in the current path.** The
   admission scan reads live `dist[u]` (`sssp.cc:564-588`), the MAA condition
   reads it again (`sssp.cc:740-748`), candidate generation reads it again
   (`sssp.cc:796-802`), and the coherent tail also uses live source distances.
   Therefore simply deleting `ActiveSource`/`CrossOwner` reason bits is a
   **NO-GO** change. All active decisions, bounds checks, routed candidates,
   fallback pages, and coherent tails must consume one frozen source image.

4. **Exact frontier order/work is not preserved and must not be an acceptance
   criterion.** Even the base OpenMP CAS loop has schedule-dependent success and
   push order (`sssp.cc:1123-1143`). Page reconstruction coalesces successful
   decreases, cross-window interference can leave a legal stale push, and
   local-bin block merge order is nondeterministic. The required criteria are
   exact final distances, no missed final decrease, termination, bounds, and
   response closure. Work counts/order remain diagnostic performance data.

No gem5 run or production edit was made for this audit.

## Source semantics audited

### Base `DeltaStep`

For each frontier occurrence, the base loop captures `dist_u = dist[u]` once,
then walks its edges. Each edge retries `compare_and_swap` until its candidate
is no longer smaller or it wins; every winning strict decrease is pushed to a
thread-local destination bin (`sssp.cc:1123-1143`). Threads select the smallest
nonempty local bin at or above the current bin, reduce it into
`next_bin_index`, concatenate that bin's per-thread blocks using
`fetch_and_add`, and retain higher bins (`sssp.cc:1151-1174`). Thus:

- successful MINs serialize, but which candidate wins first is not fixed;
- a captured source distance may become stale before its later edge uses;
- a stale candidate is still an upper bound derived from a discovered path;
- a later smaller winner pushes the source/destination again, allowing a
  subsequent iteration to propagate the improvement; and
- stale entries in a later bin are harmless: at use time the lower-bound test
  can deactivate them, while the strict decrease that made them stale supplies
  the necessary lower-bin work.

The current code's `kBinSizeThreshold` assertion is an existing operational
restriction, not part of this semantic proof. A prototype must retain a valid
same-bin drain path (or test cases above that existing threshold); it cannot
use that assertion to discard a required repeat.

### Current hybrid old-result path

`RunSsspHybridWindow` validates the 16K backing, issues FP32-tagged MIN with
old-result capture, waits for completion, and then reconstructs each of four
physical 4K pages independently (`sssp.cc:287-347`). For a destination in one
page, original-order execution makes

`min(old value of the page's last alias, last alias candidate)`

the value after that page. The reverse pass propagates it to earlier aliases;
the forward pass pushes lanes satisfying `candidate == page_final &&
old > page_final`. Independent page reconstruction intentionally preserves the
legacy physical-page boundary rather than treating all 16K aliases as one
frontier batch.

Today `Tracker::observeDestination` rejects both an active destination source
and every owner sharing a destination (`sssp_chunk_admission.hh:46-65`). The
actual routed issue/candidate/window sequence is also inside an OpenMP critical
region (`sssp.cc:797-831`), so routed windows are currently host-serialized.
Keeping that critical region is the least ambiguous first prototype. The more
general argument below permits concurrent windows, but only under the explicit
linearizability and completion contract.

## Why cross-window aliases preserve final distances

Assume every selected lane performs an atomic `D[v] := min(D[v], c)` and
receives the exact value immediately preceding its own linearization point.
All possible cross-window schedules then produce

`D_final[v] = min(D_initial[v], every candidate to v)`.

Interference after a window's last alias but before its reconstruction can make
that window push an obsolete larger value. It cannot increase `D`, and the
stale push is legal DeltaStep work. Conversely, consider the globally last
strict decrease to the eventual minimum. With original lane order inside its
page, either that lane passes the page winner test or an equal lane that
performed the strict decrease does. A later lower value would contradict
“globally last”; a later larger/equal alias leaves the page final at the same
minimum. Therefore at least one push represents the final decrease. Local-bin
merging may reorder or temporarily retain other pushes but cannot discard this
minimum-bin work.

This establishes final-distance/progress safety, not identical CAS successes,
frontier multiplicity, order, iteration count, or work.

## Why an all-source snapshot handles active-source aliases

At the iteration boundary, freeze every frontier source distance before any
destination write. Every routed candidate is then `snapshot[u] + w`, including
fallback and coherent-tail candidates. Since `snapshot[u]` is an already known
source-to-`u` path length and weights are positive, the candidate is a legal
path upper bound and its bin is not below the current bin. If another lane
lowers active source `u` during the wave, candidates already formed from its
older snapshot can only be too large. The lowering lane pushes `u`; a repeated
iteration snapshots the smaller value and propagates it. This is a Jacobi-style
legal DeltaStep schedule and corresponds to the base schedule in which all
workers capture `dist_u` before any CAS wins.

Without a phase boundary, the one-wave result is timing dependent. The model's
three-event example reads source 8 before or after it is lowered to 3, producing
destination 10 or 5 through a weight-2 edge. Both eventually converge after the
source push, but only the explicit snapshot gives one auditable operand and
bounds identity for the whole routed wave. This audit treats the snapshot as a
minimal *sufficient* prototype condition; it does not claim that every possible
live-read implementation is incorrect.

## Minimal prototype contract

All conditions are required unless the reconstruction is redesigned:

1. **Snapshot phase and barrier.** After the prior iteration's writes are
   complete, copy the distance of every valid frontier source into external
   coherent storage. Derive active status and candidate bounds from that copy,
   then execute a global barrier before the first destination MIN.

2. **Single operand image.** MAA full pages, non-routed fallback pages, and
   coherent tails must use the same snapshot. No candidate or active predicate
   may reread live `dist[u]` until the wave is closed.

3. **External storage.** The simplest implementation is one `WeightT` per
   frontier occurrence (`4 * curr_frontier_tail` bytes) plus an optional active
   byte/bit. A `WeightT[num_nodes]` array (`4 * num_nodes` bytes) reuses values
   for duplicate frontier nodes and works with the current node-indexed cursor,
   but requires writing every referenced node before the barrier. Epoch tags
   are needed only if entries can be consumed without being overwritten in the
   current phase. This storage is ordinary coherent memory; hidden SPD or
   simulator-only payload is not acceptable.

4. **Atomic destination contract.** All owners target the same coherent `dist`
   backing. Every MIN is linearizable, cannot lose/torn-write, and captures the
   exact pre-MIN old value associated with its original logical ordinal.

5. **Intra-page alias order.** Aliases to one destination apply in original
   offset/lane order within each physical page. If concurrency work weakens
   that rule, replace the last-alias formula with an order-independent per-page
   minimum over all `(old, candidate)` pairs or a coherent post-wave reload,
   and re-prove the push condition.

6. **Completion/lifetime.** RMW completion means all destination MINs have
   linearized and every selected old result is coherently visible. Index,
   candidate, predicate, snapshot, and old-result backing remain immutable and
   unaliased until reconstruction and response closure finish.

7. **Existing numeric/domain gates remain.** Preserve positive weights,
   `[0, kDistInf]` source/destination/candidate bounds, overflow checks, FP32
   bit-order equivalence, valid destinations, and stable graph/frontier data.
   Only the two data-hazard reasons are candidates for relaxation; bounds stay
   fail-closed.

8. **Progress-preserving bins.** Never deduplicate or discard the only push for
   a strict final decrease. Preserve higher local bins, select the global
   minimum nonempty next bin, allow same-bin repeats, and permit stale entries
   to fail the later active test.

## Executable counterexample search

`experiments/tests/test_sssp_conflict_tolerant_model.py` is standard-library
Python and is source-grounded against the current base loop, hybrid window, and
admission reasons. It covers:

- 256 candidate assignments over two owners, two pages per owner, duplicate
  within-page and cross-page/cross-owner destinations, and equal/decreasing
  candidates;
- all 252 legal RMW/reconstruction interleavings for every assignment (64,512
  schedules total);
- exact atomic-CAS push traces versus reconstructed hybrid pushes, including
  stale-push witnesses and multiple frontier signatures;
- the explicit internal-lane reorder counterexample;
- active-source lowering before/after source use and snapshot repair; and
- an exhaustive repeated-iteration search with cross-owner aliases, an active
  source, both owner block-merge orders, retained bins, and same-bin repeat.

The repeated search visits 9 unique states and 17 transitions. Every terminal
state is `(0, 4, 5, 6)`, equal to Dijkstra. These bounded searches found no
final-distance counterexample under the stated contract. They are executable
evidence plus a regression oracle, not a replacement for the general invariant
argument or future implementation tests.

Run with:

```sh
python3 -m unittest experiments.tests.test_sssp_conflict_tolerant_model -v
```

## Prototype gates

Proceed in two bounded stages:

1. Retain the current OpenMP critical region and ordered old-result mechanism;
   add the external snapshot/feed; relax `CrossOwner` and `ActiveSource` only
   for windows whose entire operand stream is snapshot-backed. Compare final
   distances/fingerprint with the native oracle, while reporting frontier work
   and iteration differences rather than requiring equality.
2. Only after stage 1 passes adversarial host/model tests, consider overlapping
   owners. Add a focused test that forces same-line cross-unit aliases and
   proves global MIN linearizability, exact old-result identity, original
   intra-page order, completion visibility, and response closure.

Production promotion is **NO-GO** until both implementation-specific gates pass.
The current audit supports the semantic prototype, not a gem5 or performance
claim.
