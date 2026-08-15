# Hybrid 16K-metadata / 4K-physical SoA JIT RMW handoff

## Result

The guarded `INDIR_RMW_VECTOR` SoA/JIT path is implemented and validated for
one full 16,384-entry Row/Offset epoch with a 4,096-element physical SPD. It
uses direct coherent `indices`, `values`, and optional `uint32_t predicates`
backing arrays, returns no old-value vector, and marks a completion-only tile
only after every response-bearing A `WriteReq` has received its exact
`WriteResp`.

The implementation does not publish 32-byte records, allocate a value or write
array proportional to 4K/16K, spool descriptors, add a port, dereference a host
pointer at zero simulated time, or expose a free publication stage. Test MAA
setup, memory-region registration, and array publication all occur after ROI
entry and the stats reset.

## Guarded ABI and bounded state

The existing 64-byte instruction record remains unchanged. Words 3, 4, and 5
carry the values, indices, and optional predicate addresses. Existing shapes
still dispatch after word 4; only the exact SoA/JIT shape waits for word 5.
The guard requires absent SPD sources and condition, absent `dst1` old-value
output, three range registers, and a present completion-only `dst2` token.

Indices and predicates are read through ordinary timed cache-line requests to
build exactly one 16K Row/Offset epoch. Once a reordered A line is claimed, its
Offset chain retains logical `i`; values are fetched just in time through timed
cache-line reads and applied in chain insertion order. The sole context owns a
64-byte A line and metadata and is statically checked at no more than 128 bytes.

The one-context limit is the correctness-first form. The current indirect
response router identifies an outstanding read by physical cache-line address.
Two contexts can request values on the same cache line, but there is no bounded
fan-out owner list or request context/generation tag. Raising the context count
to eight before adding such identity would make a coalesced value response
ambiguous. This is the exact blocker to an eight-context scoreboard; it is not
a Row/Offset or physical-SPD capacity limitation.

## Exact matrix evidence

Validation used the freshly built `build/X86/gem5.opt` and:

```text
experiments/scripts/run_hybrid_rmw_soa_matrix.sh \
  build/X86/gem5.opt \
  /tmp/hybrid-rmw-soa-jit-matrix-20260814-stalls
```

Every arm emitted one exact result line, one `ROI Ended`, one exact `m5_exit`,
no fatal marker, the requested resolved geometry, and the same bit-exact hash:

| arm | logical metadata | physical SPD | simTicks | output hash |
| --- | ---: | ---: | ---: | ---: |
| ordinary native16 | 16,384 | 16,384 | 740,085,683 | 17,795,497,279,832,657,243 |
| ordinary native4 | 4,096 | 4,096 | 739,149,500 | 17,795,497,279,832,657,243 |
| SoA/JIT physical16 | 16,384 | 16,384 | 926,750,119 | 17,795,497,279,832,657,243 |
| SoA/JIT physical4 | 16,384 | 4,096 | 926,750,119 | 17,795,497,279,832,657,243 |

The input contains duplicate destinations, 1,539 false predicates with poison
values, and the order-sensitive FP32 sequence `16777216, 1, -16777216, 1`.
The second generation passes a null predicate pointer, exercising the optional
ABI. Both SoA geometries reported the same per-generation terminal records:

| generation | selected/rejected | index lines | predicate lines | A reads | value reads | aliases | A writes | context HWM/stalls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 14,845 / 1,539 | 1,024 / 1,024 | 1,024 / 1,024 | 63 / 63 | 14,845 / 14,845 | 14,845 | 63 / 63 | 1 / 63 |
| 2 | 16,384 / 0 | 1,024 / 1,024 | 0 / 0 | 63 / 63 | 16,384 / 16,384 | 16,384 | 63 / 63 | 1 / 63 |

Aggregate counters were exact in both SoA arms: 2,048/2,048 index lines,
1,024/1,024 predicate lines, 31,229/31,229 value reads, 126/126 A reads,
126/126 A writes/acks, 31,229 aliases, and two terminal completions. The
aggregate context-high-water statistic is two because it sums a high water of
one across the two instructions. The 126 aggregate stalls count the one
blocked claim check for each of the 126 response-owned A-line contexts.

## Comparability boundary

The two SoA arms use the identical binary and the same `soa16` checkpoint; only
`physical_tile_elements` changes. Their 1.000000000 simTicks ratio is therefore
a geometry-independence and hidden-SPD-dependency check, not a speedup claim.

The native16 and native4 arms require different `TILE_SIZE` binaries, and the
ordinary API stages index/value/predicate tiles and produces an old-value tile.
They consequently use separate checkpoints and are correctness references, not
a matched mechanism-only performance comparison with SoA/JIT. No native-vs-SoA
speedup should be inferred from this matrix.
