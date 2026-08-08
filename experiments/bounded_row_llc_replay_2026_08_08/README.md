# Bounded-row coherent-LLC replay model

This directory is a deterministic work/traffic screen for one proposed true
metadata-virtualization mechanism. It does not implement gem5 and does not
claim timing performance.

Run the synthetic invariant tests:

```sh
python3 -m unittest \
  experiments/bounded_row_llc_replay_2026_08_08/test_llc_replay_model.py -v
```

Regenerate the frozen result from the raw physical-admission trace:

```sh
python3 experiments/bounded_row_llc_replay_2026_08_08/llc_replay_model.py \
  --trace /data1/nier/dx100-runs/2026-08-08-virtualization-sprint/hybrid-control-explicit-0108d9b/native_direct_16k/physical_admission_records.jsonl \
  --trace-sha256 1c68340c0e87a53240905389c1c0e5bf451a0645b8ceaf5f92d4e34edaba5424 \
  --output /tmp/bounded-row-llc-replay-results.json
cmp /tmp/bounded-row-llc-replay-results.json \
  experiments/bounded_row_llc_replay_2026_08_08/results.json
```

`input_manifest.json` binds the source commit, binaries, checkpoint, raw trace,
physical validation, Ramulator configuration, and the prior matched-control
tables. The 12 MiB raw JSONL remains in the frozen run root; it is consumed and
SHA-256 checked rather than copied into Git.
