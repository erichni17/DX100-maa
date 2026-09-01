# SSSP S22 host admission prediction

Recorded 2026-08-31 22:47 EDT. This is a host-only screening result. No gem5
or native SSSP execution was launched.

## Recommendation

**NO LAUNCH.** The deterministic model predicts 7,232 size-eligible logical
windows and zero routed windows on the frozen S22 input. All 7,232 eligible
windows are attached to chunks rejected for both active-source and cross-owner
hazards. Another multi-day gem5 run is not justified by this prediction.

This recommendation is an admission-coverage decision, not an architecture
performance result. The host runtime below must not be interpreted as
simulated performance.

## Result

The predictor selected GAPBS source 2,796,003 from the default
`std::mt19937(27491095)` source picker, matching the frozen S22 record. It
executed 365 delta iterations: 95 base iterations and 270 MAA-sized
iterations. Eighty-one iterations (75 through 156 inclusive) contained at
least one complete 16K window. None contained a routed window.

| Count | Total |
| --- | ---: |
| Frontier words | 12,660,615 |
| Active outgoing edge words | 134,217,158 |
| Eligible 16K windows | 7,232 |
| Routed windows | 0 |
| Unsafe eligible windows | 7,232 |
| Bounds-rejected windows | 0 |
| Active-source-rejected windows | 7,232 |
| Cross-owner-rejected windows | 7,232 |

Reason counts overlap: each unsafe window can carry more than one reason and
must not be added across reason columns. The accounting invariant closes:
`routed_windows + unsafe_eligible_windows == eligible_windows`.

The largest eligible iteration is iteration 114 (bin 135): frontier 152,407,
38 positional 4K chunks, 127,379 active sources, 4,121,664 active edge words,
and 231 eligible/unsafe windows. Its 231 windows carry both data-hazard
reasons and no bounds reason.

The complete machine-readable per-iteration ledger, including all zero-window
base and tail iterations, is
`experiments/analysis/sssp_chunk_admission_predictor_s22_2026-09-01.json`
(SHA-256 `eb43461cd867b2f72f6b826ce28eba5a1bf5ab9835d88fb0590fafa4814979a9`).

## Directed validation gates

The test constructs the same directed two-level 4,096-source fanout used by
`run_sssp_old_result_hybrid_small.sh` and checks the exact mixed-admission
outcomes before S22 is accepted:

| Fixture | Eligible | Routed | Unsafe | Active-source rejected | Cross-owner rejected |
| --- | ---: | ---: | ---: | ---: | ---: |
| `all_safe` | 4 | 4 | 0 | 0 | 0 |
| `active_source` | 4 | 3 | 1 | 1 | 0 |
| `cross_owner` | 4 | 2 | 2 | 0 | 2 |

Command:

```text
python3 -m unittest -v experiments.tests.test_predict_sssp_chunk_admission
```

Result: 5/5 PASS, including deterministic replay and truncated-input
rejection. The validation constructs inputs only; it does not run GAPBS.

## Method and provenance

The tool memory-maps GAPBS `.wsg` CSR data and mirrors these current
`DeltaStepMAA` rules:

- the default source picker and delta-bin frontier lifecycle;
- active-source marking from the complete current frontier;
- 1K/2K/4K positional frontier chunks selected by the current four-core
  thresholds;
- per-destination epoch/first-owner tracking and propagation of
  `ActiveSource`, `CrossOwner`, and global `Bounds` reasons;
- eligible windows as each chunk's active outgoing words divided by 16,384;
  and
- the production distinction between base and MAA-sized iterations, so base
  work contributes no admission counters.

Parallel relaxation is made deterministic by assigning OpenMP-style static
contiguous work to four logical threads, then applying and merging it in
thread-major order. The tool uses ordinary sequential integer MIN updates; it
does not invoke MAA or simulate timing.

| Artifact | Identity |
| --- | --- |
| Baseline source commit | `5fbaa33e38d3c63bee905c78f6459ad295737870` |
| Predictor source | `experiments/tools/predict_sssp_chunk_admission.cc`, SHA-256 `1aa554add1dbcd31fb9e8f1d01370a17b8db1302a0041b0356fcbbea2ce6696a` |
| Predictor test | `experiments/tests/test_predict_sssp_chunk_admission.py`, SHA-256 `8e69c88699b88802938697c3228f022f400461dd3205e6ed4b9d6a389c047c62` |
| Host binary | `/tmp/predict_sssp_chunk_admission`, SHA-256 `6c9e3af5c2a53d4ae43969483e65444941cbf75eb77df4844ada61e639e0c203`, 51,016 bytes |
| Compiler | `g++ (Ubuntu 9.5.0-1ubuntu1~22.04.1) 9.5.0` |
| Production SSSP source | `benchmarks/gapbs/src/sssp.cc`, SHA-256 `567a6ad9dd37441e69018e264e7c380be7805a9f9911c0441c015ae0cb12738d` |
| Tracker source | `benchmarks/gapbs/src/sssp_chunk_admission.hh`, SHA-256 `e391f23eaa2a25c20c4f9354461351197858fd92a98f3a359e5ecd3f9cf39151` |
| Frozen full runner | `experiments/scripts/run_sssp_old_result_hybrid_full.sh`, SHA-256 `8067edd11a15b94e8856fe7b32e1b8980551fb9f17ec7dc2f80a8dc06d770623` |
| Frozen S22 input | `/data1/nier/worktrees/DX100-full-tile-sweep-20260720/benchmarks/gapbs/serialized_graph_22.wsg`, SHA-256 `23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc`, 1,090,514,493 bytes |

Build and prediction command:

```text
g++ -std=c++17 -O3 -Wall -Wextra -Werror \
  experiments/tools/predict_sssp_chunk_admission.cc \
  -o /tmp/predict_sssp_chunk_admission
/usr/bin/time -v /tmp/predict_sssp_chunk_admission \
  --input /data1/nier/worktrees/DX100-full-tile-sweep-20260720/benchmarks/gapbs/serialized_graph_22.wsg \
  --delta 1 --threads 4 \
  --output /tmp/sssp_chunk_admission_s22.json
```

The tool reports 16.971486 seconds internally; `/usr/bin/time` reports 17.00
seconds wall, 16.73 seconds user, 0.25 seconds system, and 1,193,024 KiB peak
RSS. These are reproducibility/resource observations only.

## Limitations

- Production uses concurrent CAS relaxation and `fetch_and_add` frontier
  assembly. Their inter-thread order is not specified. The predictor's stable
  thread-major order is one deterministic model, not a bit-for-bit replay of
  every OpenMP schedule.
- The deterministic model finds 7,232 eligible windows, six more than the
  7,226 recorded by the earlier completed global-admission S22 gem5 run. That
  0.083% difference is expected from frontier ordering/relaxation sensitivity.
  Both results classify routing as zero, but the old run did not preserve
  per-chunk reason counters and therefore is not an exact oracle for this tool.
- A single ordering cannot prove that every possible concurrent ordering has
  zero safe chunks. The result is a fail-closed launch screen. If a future
  launch depends on small nonzero coverage, evaluate additional deterministic
  schedules or make frontier assembly deterministic in the guest first.
- The predictor models admission and SSSP work construction only. It does not
  model coherent fallback publication, MAA response timing, caches, memory,
  simulator completion, correctness fingerprints, or speedup.
- The current input has positive in-range weights and vertices, so no bounds
  rejection is observed. Malformed serialized-file structure is checked, but
  the tool is not a general graph repair utility.
