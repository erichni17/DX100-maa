# Safe-combiner pair r2: independent audit

## Disposition — no promotion

The `safe416` execution fixes the previously unsafe *observed* placeholder behavior: it keeps each incomplete line in the combiner and emits 2,048 complete 64-byte coherent writes, with zero partial writes. It is an exact, matched single microbenchmark comparison and has a real simulated improvement. It does **not** yet justify promotion, including a hardware-aware micro-level promotion, because the claimed 4,096-word physical-result bound is not enforced by the implementation and the winning configuration has no modeled tag/CAM, reference-RAM, payload-port, reset, arbitration, or Fmax cost.

This is an independent read-only audit of `/data1/nier/dx100-runs/2026-08-29-hybrid-safe-combiner-pair-r2` and source commit `0987c22aeff8e25c6db1e528420c0c2c7accc55b`. No gem5 job was launched and no production source was changed.

## Findings first

### F1 — blocker: the 4,096-word physical-result limit is an evidence-field, not a source invariant

The selected arm correctly *calculates* its live result-data capacity as 1,664 words: 1,600 combiner words plus eight 64-byte response slots, or 64 FP64 words. Thus `1,664 <= 4,096`, and the strict trace reports `result_words=1664`. The runner writes `physical_result_word_bound: 4096` in `result.json` ([`run_hybrid_safe_combiner_pair.py`](../scripts/run_hybrid_safe_combiner_pair.py)), but neither the runner nor `MAA::beginStrictTwoPhaseReference` rejects a configuration above 4,096. The latter computes `combineCapacity + responseCapacity` and rejects only a `uint32_t` overflow ([`MAA.cc`](../../src/mem/MAA/MAA.cc)).

Consequently, the reported bound is a true statement about this one arm, but not an enforced physical architectural bound. A future command can exceed it while still entering the strict reference machinery. Add a fail-closed source-level `resultCapacity <= 4096` invariant (and a negative test) before using this as a physical-area limit.

### F2 — blocker for performance/area interpretation: the 416-entry combiner is fully associative and cost-free in timing

The selected config uses `virtual_combine_ways=0` and `virtual_combine_banks=0`. `insertVirtualCombineWord` linearly searches every eligible slot, while `reserveVirtualCombineBank` returns immediately when the bank count is zero ([`IndirectAccess.cc`](../../src/mem/MAA/IndirectAccess.cc)). The safe run therefore records zero combiner bank accesses/conflict cycles. `virtual_words_per_cycle=1` limits response-word attempts, but does not model a 416-tag lookup latency, a CAM match, finite tag/data ports, or arbitration between lookup, victim selection, payload allocation, full-line drain, and ACK completion.

The 1,600 FP64-word payload is 12,800 B and the response capacity is 512 B, but that is not the whole combiner. Its 416 line records contain at least 416 tags, 416 valid masks, and 416 x 16 32-bit word references (26,624 B of reference storage alone), plus allocation/free-list/generation state, victim state, page-ready metadata, and the acknowledged-write scoreboard. The C++ uses host `vector`/`map` containers to implement this bounded state. I found no unbounded **payload** admission in this path: response credit limits source reservations to the eight response slots, combiner payload allocation fails at 1,600 words, and the retirement scoreboard is bounded to 32 configured writes. Nonetheless, these host containers are not a hardware implementation or an area/timing model.

No report charges lookup/CAM energy or latency, reference RAM, payload ports, reset/clear time, arbitration, or Fmax. The 16.93% timing result must not be read as a physically realizable speedup until those costs and a finite organization are modeled.

### F3 — the run demonstrates a safe execution, not a universal no-partial-write policy

`drainVirtualCombiner(false)` only emits a slot when its valid mask is full; the partial-write path is reachable only with `flush_partial=true`. `copyLine` zero-fills staging, but the safe execution invokes it for a full valid mask before a full 64-byte write, so no placeholder bytes are published. That closes the unsafe dense-placeholder mechanism for this trace.

However, the generic implementation still has legal `flush_partial=true` calls at final drain/partition boundaries and partial-victim retirement under pressure. This is not a source-level rule that `safe416` can never publish a partial line under another legal schedule; it is a checked property of this execution. Retain `IND_VirtPartialWrites == 0` as a mandatory gate and add a stress/negative test that demonstrates either stall-until-full behavior or fail-closed rejection when the 416/1,600 capacity cannot preserve it.

### F4 — correctness is exact differential evidence, but lacks an independent oracle

Both arms exit by `m5_exit`, return zero, have absent registered PIDs, and produce the same consumer hash `7228541527853630339` with `errors=0`. The strict traces agree on logical work and use real ACK visibility (`virtual_idealized_write_ack=false`). Exact sender-state identity includes address, generation, and a non-recycled transaction; an unmatched/reused ACK panics. The 2,048 safe issues and completions have matching transaction IDs through the final ACK, so I found no observed ACK/reuse or coherence-visibility failure.

This is still differential, not an independent native/reference oracle: both arms share the same benchmark, checkpoint, binary, and consumer checker. It establishes equality to the masked-write control arm, not general functional correctness beyond that oracle.

## Recomputed evidence

All 36 entries in `artifacts.sha256` rehashed successfully. The manifest pins the source commit above and one gem5 SHA-256 `14f9870e5bf337588d50e012a557e26ed51e99ccc9b07476991960d8cf4e1917`. The two commands use the same checkpoint, executable hash, workload, clocks, cache/memory settings, strict mode, and consumer treatment. The only intended functional configuration delta is combiner capacity: control `16/0` versus safe `416/1600`; output-path names necessarily differ.

| Metric | control16 | safe416 | Recomputed delta |
| --- | ---: | ---: | ---: |
| `simTicks` | 56,868,031 | 47,241,090 | -9,626,941 (-16.9286%) |
| `simInsts`; B words; A issues | 32,952; 16,384; 9,523 | same | 0; 0; 0 |
| retirement issues/completions | 8,668 / 8,668 | 2,048 / 2,048 | -6,620 / -6,620 |
| full / partial writes | 0 / 8,668 | 2,048 / 0 | +2,048 / -8,668 |
| semantic / transport bytes | 131,072 / 554,752 | 131,072 / 131,072 | 0 / -423,680 |
| L3 MAA misses; ReadEx misses | 4,097; 2,048 | 2,049; 0 | -2,048; -2,048 |
| L3 MAA miss latency | 433,536,613 | 320,744,559 | -112,792,054 |
| Ramulator reads | 26,874 | 24,828 | -2,046 |
| combiner line / word HWM | 16 / 53 | 409 / 1,592 | +393 / +1,539 |

The safe HWM is within the configured 416 tags and 1,600 payload words. Its 2,048 x 64 B full writes equal 131,072 B and 16,384 FP64 results, matching the semantic work count. Trace records confirm every safe write has `bytes=64`, `valid_words=0` (the full-line convention), `dense_initialize=0`, and acknowledged transaction IDs 1 through 2,048. The strict trace reports `coherent_ack=1`, `order_ok=1`, and `terminal=1`; no partial publication was observed. The ratio `56,868,031 / 47,241,090` is **1.203783x** in favor of safe416. This is a single deterministic-looking observation, not a replicated workload result.

## Required closure before any micro-level promotion

1. Enforce and test the 4,096-word result bound in source, including all combiner and response capacity modes.
2. Replace the cost-free fully associative setting with a finite, timing-visible tag/data organization; charge reference RAM, tag/valid storage, payload ports, reset, victim/ACK arbitration, and a justified Fmax impact.
3. Make no-partial-publication an explicit selected-mode contract across capacity pressure, final drain, partitioning, and delayed/reordered ACKs.
4. Add an independent correctness oracle and repetitions before interpreting the speedup beyond this equal-work micro.
