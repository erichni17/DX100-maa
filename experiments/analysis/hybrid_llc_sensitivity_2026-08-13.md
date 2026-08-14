# General-hybrid LLC-port sensitivity handoff — 2026-08-13

## Existing evidence

The completed API matrix at
`/data1/nier/dx100-runs/2026-08-13-general-hybrid-api-dedicated-reg-75612108-r1`
is a single, matched **4-port** point.  All seven arms passed the exact API
hash (`7228541527853630339`), completed normally, and used the same frozen
inputs/checkpoint group within each profile.  Its relevant simulated times
are:

| control | simTicks | materializer forwarded/cache-read lines |
|---|---:|---:|
| native16 | 17,472,912 | 0 / 0 |
| token materializer (`hybrid_token_stream_ld`) | 23,773,602 | 346 / 1,702 |
| token materializer ping-pong | 24,085,350 | 371 / 1,677 |

These are neither a native16-vs-token-materializer speedup claim nor proof
that LLC acceptance bandwidth is the bottleneck: the controls change physical
tile capacity and instruction/materialization behavior.  The low forwarding
fraction motivates the sensitivity only.  Host elapsed time is not evidence.

## Matched sensitivity contract

Run three independent API matrices with identical binaries, input, checkpoint
boundary, MAA knobs, memory channels, and one replica policy.  Vary only
`--l3-ports` as `2`, `4`, and `8`; retain `native16` and
`hybrid_token_stream_ld` as the required reported controls.  The runner keeps
its other required native4/ordinary controls, which protects the existing
exact-comparison contract; do not compare a port point until its whole matrix
passes the analyzer.

The runner now records `l3_ports` in the plan and executed manifest, and emits
the exact gem5 option.  Example (replace absolute artifact paths, run once per
port value):

```sh
python3 experiments/scripts/run_general_hybrid_benchmark_matrix.py \
  --workload api --out /data1/nier/dx100-runs/2026-08-13-general-hybrid-api-l3p4 \
  --gem5 /ABS/gem5.opt --ramulator-library /ABS/libramulator.so \
  --native16 /ABS/test_virtual_tile_consumer_T16384 \
  --native4 /ABS/test_virtual_tile_consumer_T4096 \
  --hybrid /ABS/test_virtual_tile_consumer_T16384 \
  --native16-options 'native 16384' --native4-options 'native 4096' \
  --hybrid-options 'deferred {selector}' --l3-ports 4 --execute
```

Accept only points with campaign/restore exit zero, exactly one `m5_exit` per
run, final nonempty ROI statistics, identical API hash across the full matrix,
and the token-materializer lifecycle closure/fallback checks already enforced
by the analyzer.  Report individual `simTicks` and forwarding/cache-read
counts; do not infer an architectural speedup from host time or compare
different controls as if they were matched.
