# Fused-p16 NA=1024 forward-progress audit (2026-08-27)

## Classification

**Not expected scaling; unresolved between same-tick event explosion and an
advancing queue/polling forward-progress collapse.** The current live root is
not a performance or liveness observation: its candidate restore has neither a
gem5 terminal nor a nonempty final stats window. It reached the ROI banner at
18:22:13 on 2026-08-26, emitted the fused treatment-selection line, and its
last log write was at 18:23:52. No `CG_LOGICAL16_RMW_TERMINAL`, fingerprint,
producer-complete marker, q16-complete marker, `m5_exit`, or stats dump
appeared. At this audit its gem5 PID was absent.

That rules out accepting ordinary scaling. It does not establish either
remaining fault mode: final stats are dump-on-completion only and the live
restore omitted `MAAVirtualTrace`, so it contains no event-tick sequence or
queue-state records. The absent PID alone does not provide a wrapper exit code
or cause of termination.

## Comparability audit

The accepted NA=256 root is
`cg-fused-p16-q16-successor-na256-r2` recorded in
`fused_p16_product_successor_2026-08-26.json`: exact candidate/control output,
10 fused windows, and 396,154,397 candidate `simTicks` versus 419,398,090
control `simTicks`. The NA=1024 command requested 65 windows and used the same
gem5 SHA-256 (`271836…4dfd2e`), Ramulator SHA-256 (`76ea3…a15753`), four
indirect units, eight 16K tiles/core, 4K physical tiles, 16/4/4/1 combiner,
8 response slots, 32 write credits, and 32 value owners.

The NA=1024 source commit is `ab891853…`, whereas acceptance used
`4a4d91b8…`. A path-limited comparison finds only
`experiments/scripts/run_cg_fused_p16_product_q16.py` changed: it adds the
NA=1024 authorization check and generalizes expected windows from 10 to 65.
The `src`, `configs`, `benchmarks`, `include`, and `util` trees are unchanged;
the live `cg.cpp` hash matches the accepted source. Therefore this audit found
no simulator, guest, or configuration treatment delta that explains the stall.

## Bounded discriminating probe

`run_fused_p16_forward_progress_probe.py` is a deliberately isolated next
step. It runs no CG application and no comparison arm: one 16,384-word fused
producer plus one q16 consumer, with the NA=1024 four-indirect-unit geometry.
It uses the accepted pinned simulator/config source, `MAAVirtualTrace`, an
8,000,000,000 relative simulated-tick ceiling, and a 180-second watchdog that
only terminates its own child process group. It emits periodic JSON snapshots
of trace size, last simulated tick, last event, and multiply completions.

On watchdog expiry it labels only trace-proven signatures:

- at least 64 post-progress `indirect_execute` events at one tick:
  `EVENT_EXPLOSION_SAME_TICK`;
- at least 16 post-progress `indirect_stall` events at multiple advancing
  ticks: `QUEUE_POLLING_FORWARD_PROGRESS_COLLAPSE`;
- otherwise: `INCONCLUSIVE_TIMEOUT` (or `NO_TRACE_PROGRESS`).

It is intentionally not launched by this audit. A terminal one-operation run
is diagnostic mechanism evidence only, not CG performance evidence; an
inconclusive or failing probe must not authorize a larger rerun.
