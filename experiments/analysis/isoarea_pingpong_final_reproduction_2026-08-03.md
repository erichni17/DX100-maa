# Exact-c26a082 iso-area ping-pong reproduction

All three sequential frozen-binary arms completed checkpoint and restore with
exit 0 and the exact output hash `7228541527853630339`.

| Arm | simTicks |
|---|---:|
| serial 4K | 46,504,601 |
| serial 2K | 46,900,233 |
| ping-pong 2K | 46,627,923 |

Ping-pong is 0.580615% faster than serial 2K. This is the treatment-only
chunking-recovery comparison. It is 0.265182% slower than serial 4K, the fair
fixed-area overall result because that comparison also changes chunk size.

The trace analyzer validates legal issue-to-completion interval envelopes and
exact output, but does not establish simultaneous useful STREAM/ALU progress;
no such claim is made here. Raw frozen evidence, failed attempts, inputs,
source snapshots, configs, hashes, logs, stats, and traces are in
`/data1/nier/dx100-runs/2026-08-03-isoarea-pingpong-final-c26a082`.
