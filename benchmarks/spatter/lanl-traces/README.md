# LANL Spatter Traces

This integration adds checksum-pinned application traces from LANL's ATS-5
Spatter data repository without committing large generated JSON files. The
first supported application is FLAG `static_2d`: 20 configurations containing
14 gathers and 6 scatters.

The importer deliberately writes one configuration per JSON file. DX100's
Spatter driver places every configuration in one gem5 ROI, so splitting the
source makes each simulated result attributable to one access pattern. It also
sets `nruns=1`, which the DX100 driver requires, while preserving the original
pattern, kernel, and `count=1` semantics.

## Install

Use an existing checkout of the three Git LFS archives:

```bash
benchmarks/spatter/setup_lanl_traces.py \
  --archive-root /path/to/flag/static_2d
```

Or download the exact objects from the pinned repository revision:

```bash
benchmarks/spatter/setup_lanl_traces.py --download
```

Generated data is written under `tests/test-data/lanl`, which is ignored by
Git. `manifest.json` in that directory records every derived input hash and
pattern summary.

The validation scripts set `SPATTER_DATA_SEED=1` so output hashes are stable
across binaries. Normal Spatter behavior remains time-seeded when that
environment variable is absent.

## Validation

Build a normal serial Spatter executable, then run every imported configuration:

```bash
cmake -S benchmarks/spatter -B benchmarks/spatter/build_lanl_host \
  -DFETCHCONTENT_SOURCE_DIR_NLOHMANN_JSON=/path/to/nlohmann_json-src
cmake --build benchmarks/spatter/build_lanl_host -j4 --target spatter_base
benchmarks/spatter/run_lanl_trace_host_smoke.sh
```

This host smoke checks trace hashes and parser/execution compatibility. It is
not architectural evidence. The functional MAA model provides a stronger
gather/scatter output check without running gem5:

```bash
cmake -S benchmarks/spatter -B benchmarks/spatter/build_lanl_func \
  -DBUILD_MAA=ON -DBUILD_FUNC=ON \
  -DFETCHCONTENT_SOURCE_DIR_NLOHMANN_JSON=/path/to/nlohmann_json-src
cmake --build benchmarks/spatter/build_lanl_func -j4 \
  --target spatter_maa_flag_verify_16K
benchmarks/spatter/run_lanl_trace_functional_smoke.sh
```

A gem5 result additionally requires a matching
baseline, native DX100, fused DX100, and virtual DX100 configuration, terminal
completion, output correctness, and simulated-time comparison.

## Scope

FLAG is immediately useful because its patterns contain 31,923 or 63,846
accesses and already use `count=1`. The AMG, LULESH, Nekbone, and PENNANT files
under `standard-suite/app-traces` are also application-derived traces, but they
encode short patterns with very large repetition counts. The current DX100 MAA
path does not implement those repeated-pattern semantics and must not silently
flatten or relabel them.

Full ATS-5 applications such as Branson, AMG2023, and SPARTA require a frozen
input, a deterministic correctness oracle, and a serial region-of-interest
kernel extraction before they are suitable for gem5.
