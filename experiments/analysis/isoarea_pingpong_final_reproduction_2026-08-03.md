# Accepted exact-c26a082 ping-pong reproduction

This supersedes the rejected/stale `5bb0a74` report/evidence attempt. The
accepted raw root is `/data1/nier/dx100-runs/2026-08-03-isoarea-pingpong-repair-c26a082`, produced by source commit `68edb9e` with one treatment-neutral shared checkpoint.

| Arm | simTicks |
|---|---:|
| serial 4K | 45,364,029 |
| serial 2K | 45,844,797 |
| ping-pong 2K | 45,468,571 |

Ping-pong has 0.820651469% fewer ticks than serial2K (1.008274419x); this is the treatment-only comparison. It is 0.230451312% slower than serial4K, which is fixed-area overall-design context because chunk size also changes.

All outputs match exactly. The analyzer records only interval-envelope overlap data; it does not establish useful simultaneous work. The inner matrix is terminal (`matrix.exit=0`, `matrix.complete`), while the outer `matrix.launch.exit` is missing; this preserved wrapper caveat does not fabricate outer-trap success.
