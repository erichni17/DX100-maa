# SSSP S22 host admission prediction

Recorded 2026-08-31 22:47 EDT. This is a host-only screening result. No gem5
or native SSSP execution was launched.

The ledger was regenerated with predictor schema 2 after the exact reason
coverage closure. Every unsafe eligible window is now counted once in
`reason_covered_unsafe_windows`, independently of the overlapping reason
columns.

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
| Reason-covered unsafe windows | 7,232 |
| Bounds-rejected windows | 0 |
| Active-source-rejected windows | 7,232 |
| Cross-owner-rejected windows | 7,232 |

Reason counts overlap: each unsafe window can carry more than one reason and
must not be added across reason columns. Both accounting invariants close:
`routed_windows + unsafe_eligible_windows == eligible_windows` and
`reason_covered_unsafe_windows == unsafe_eligible_windows`.

The largest eligible iteration is iteration 114 (bin 135): frontier 152,407,
38 positional 4K chunks, 127,379 active sources, 4,121,664 active edge words,
and 231 eligible/unsafe windows. Its 231 windows carry both data-hazard
reasons and no bounds reason.

The complete machine-readable per-iteration ledger, including all zero-window
base and tail iterations, is
`experiments/analysis/sssp_chunk_admission_predictor_s22_2026-09-01.json`
(schema 2, SHA-256
`2ae007490d74ff91768f6864e9eb7db97d7452d0c76ddb4b6bff59ffb33554a4`).

## Directed validation gates

The test constructs the same directed two-level 4,096-source fanout used by
`run_sssp_old_result_hybrid_small.sh` and checks the exact mixed-admission
outcomes before S22 is accepted:

| Fixture | Eligible | Routed | Unsafe | Reason-covered | Active-source rejected | Cross-owner rejected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `all_safe` | 4 | 4 | 0 | 0 | 0 | 0 |
| `active_source` | 4 | 3 | 1 | 1 | 1 | 0 |
| `cross_owner` | 4 | 2 | 2 | 2 | 0 | 2 |
| `overlap` | 4 | 2 | 2 | 2 | 2 | 2 |

Command:

```text
python3 -m unittest -v experiments.tests.test_predict_sssp_chunk_admission
```

Result: 6/6 PASS, including an active-source/cross-owner overlap fixture,
deterministic replay, and truncated-input rejection. The validation constructs
inputs only; it does not run GAPBS.

## Method and provenance

The tool memory-maps GAPBS `.wsg` CSR data and mirrors these current
`DeltaStepMAA` rules:

- the default source picker and delta-bin frontier lifecycle;
- active-source marking from the complete current frontier;
- 1K/2K/4K positional frontier chunks selected by the current four-core
  thresholds;
- per-destination epoch/first-owner tracking and propagation of
  `ActiveSource`, `CrossOwner`, and global `Bounds` reasons;
- once-per-window coverage whenever any reason bit is present, without summing
  the overlapping reason-specific counts;
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
| Predictor source | `experiments/tools/predict_sssp_chunk_admission.cc`, SHA-256 `16cabf7cebc5d4786331e279c6cf822907614eed03d1fc55089cb558e74e620e` |
| Predictor test | `experiments/tests/test_predict_sssp_chunk_admission.py`, SHA-256 `089ee8b1a1b72c5cb97e50dfdfccf221d4d3e4fbc7dd34015503374d3ab2a605` |
| Host binary | `/tmp/predict_sssp_chunk_admission_exact`, SHA-256 `b302356ff3b421d7ffff6b64f9daca39d729d5f3c6b31ca86e3d744a29a20153`, 55,456 bytes |
| Compiler | `g++ (Ubuntu 9.5.0-1ubuntu1~22.04.1) 9.5.0` |
| Production SSSP source | `benchmarks/gapbs/src/sssp.cc`, SHA-256 `2e3e8b2c85c43520f2938be2b04a209fc53a8241316cba613f42fa1521352c4e` |
| Tracker source | `benchmarks/gapbs/src/sssp_chunk_admission.hh`, SHA-256 `8add7b1c3ffa07aa990dd0c3d75da901ff382fbe5571c80321affdfa7d6096e5` |
| Frozen full runner | `experiments/scripts/run_sssp_old_result_hybrid_full.sh`, SHA-256 `22e3bb5336e80ca9cb2549d53845d0cf8d26868fcb627ed3f7fbb7249dd36dbf` |
| Frozen S22 input | `/data1/nier/worktrees/DX100-full-tile-sweep-20260720/benchmarks/gapbs/serialized_graph_22.wsg`, SHA-256 `23eb25e34343334976554071a8184f7b03358fe1892ba44cd2f5a38369f4eebc`, 1,090,514,493 bytes |

Build and prediction command:

```text
g++ -std=c++17 -O3 -Wall -Wextra -Werror \
  experiments/tools/predict_sssp_chunk_admission.cc \
  -o /tmp/predict_sssp_chunk_admission_exact
/tmp/predict_sssp_chunk_admission_exact \
  --input /data1/nier/worktrees/DX100-full-tile-sweep-20260720/benchmarks/gapbs/serialized_graph_22.wsg \
  --delta 1 --threads 4 \
  --output experiments/analysis/sssp_chunk_admission_predictor_s22_2026-09-01.json
```

The schema-2 regeneration reports 16.673816 seconds internally. The original
schema-1 run reported 17.00 seconds wall and 1,193,024 KiB peak RSS. These are
reproducibility/resource observations only.

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
