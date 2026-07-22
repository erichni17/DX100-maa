# DX100 agent process-control rules

- Run every simulation, watcher, formatter, hook, or helper that may last more
  than ten seconds in a uniquely named `tmux` session or through `dx-runtime`.
  Do not leave cancellable work owned only by an interactive tool process.
- Never request or suggest a persistent rule for raw `kill`, `pkill`, or
  `killall`. Exact PID approvals are not reusable, while broad signal rules are
  unsafe.
- Stop only sessions whose name and process identity were verified. Prefer a
  cooperative runtime stop; otherwise send `C-c` to the owned tmux session and
  use `tmux kill-session` only after the wrapper has had time to exit.
- Never signal a gem5 PID directly. Preserve its wrapper return code, final
  stats, and correctness log before classifying it.
- Keep escalated host commands to one simple command per tool call. Do not mix
  `tmux`, process inspection, and output filtering in one compound shell line;
  compound commands produce misleading saved-prefix suggestions.
- On this 56-CPU host, Black's multiprocessing CLI can hang inside the command
  sandbox. Invoke Black sequentially through its Python API, or run the hook in
  a named tmux session. Never start another bare parallel Black process.
- A sweep repair may start only after checking `MemAvailable` and recent
  `vmstat` swap-in/swap-out. Do not treat idle CPU as permission to oversubscribe
  memory.
- Full-Class-B NAS IS uses about 60 GiB RSS per gem5 process on this host. Run
  at most one IS task at a time; never place IS in a generic fixed-parallelism
  batch.
- Launch the recovery manager only through its systemd user unit with
  `MemoryHigh=220G`, `MemoryMax=240G`, and `MemorySwapMax=0`. The manager must
  verify these cgroup files itself and refuse uncapped execution.
- The dedicated non-IS recovery may overlap the isolated IS gate only through
  `dx100-full-tile-normal-recovery2-20260721.service`, with
  `MemoryHigh=128G`, `MemoryMax=144G`, and `MemorySwapMax=0`. Together with the
  gate's 96 GiB hard limit, the aggregate campaign hard limit remains 240 GiB.
  The combined 220/240 GiB manager may start only after both overlapping units
  are terminal.
- A disjoint auxiliary lane may overlap the normal recovery and IS gate only
  after the live normal unit is reduced to `MemoryHigh=96G` and
  `MemoryMax=112G`. Launch it only as
  `dx100-full-tile-auxiliary-recovery2-20260721.service`, with
  `MemoryHigh=24G`, `MemoryMax=32G`, `MemorySwapMax=0`, and at most three
  non-IS tasks. Its manager must verify the two existing cgroups and the
  aggregate `112G + 96G + 32G = 240G` hard limit before admission. Select only
  wrapper-only repairs and far-end pending tasks whose exact completed
  artifacts the main workflow will independently revalidate and fast-reuse;
  never overlap an output directory with a live task.
  A failed auxiliary task receives at most one retry in the same 24/32 GiB
  envelope after the first auxiliary unit is terminal; keep the normal unit at
  96/112 GiB until that retry is also terminal.
- A speculative XRAGE surge lane may additionally overlap these owned units
  only as `dx100-full-tile-surge-recovery2-20260722.service`, with
  `MemoryHigh=24G`, `MemoryMax=32G`, `MemorySwapMax=0`, and at most three
  non-IS tasks. Its manager must verify the normal, gate, and auxiliary
  cgroups and the aggregate `112G + 96G + 32G + 32G = 272G` hard limit before
  admission, require at least 96 GiB available after five quiet swap minutes,
  and select only far-end pending XRAGE tasks. XRAGE runners must hold an
  output-specific `flock` across reuse validation and simulation so a later
  normal-lane claimant waits and fast-reuses instead of launching a duplicate.
  The remaining serial IS recovery must wait until the surge workflow is
  terminal and its unit is inactive. Record the surge cgroup independently and
  require that telemetry in final validation.
- After the XRAGE surge is live, a UME surge may use the gate's conservatively
  released reservation only after its recorded peak remains below 64 GiB and
  the live gate is reduced to `MemoryHigh=64G`, `MemoryMax=72G`, with swap
  still disabled. Launch the UME lane only as
  `dx100-full-tile-ume-surge-recovery2-20260722.service`, with
  `MemoryHigh=16G`, `MemoryMax=24G`, `MemorySwapMax=0`, and at most three
  tasks. Its manager must verify `112G + 72G + 32G + 32G + 24G = 272G`, the
  same 96 GiB/five-minute admission gate, and pending primary ownership. UME
  runners must use the same output-specific lock/revalidate contract. The
  serial IS recovery and finalizer must also wait for and record this lane.
- When the auxiliary lane and its one-shot retry are both terminal, keep the
  normal lane at 96/112 GiB and reuse the released 32 GiB reservation as
  `dx100-full-tile-t32-surge-recovery2-20260722.service`, with
  `MemoryHigh=24G`, `MemoryMax=32G`, `MemorySwapMax=0`, and at most three
  tasks. Select only the far-end 32K GAPBS/CG tasks, whose runners must hold
  output-specific locks and independently revalidate reuse. The manager must
  verify the remaining live cgroups and the unchanged 272 GiB aggregate cap.
  Restore the normal lane to 128/144 GiB only after this lane is terminal and
  inactive; the serial IS recovery and finalizer must wait for it and require
  its cgroup telemetry.
- After the UME surge is terminal, immediately reuse its released 24 GiB cap as
  `dx100-full-tile-t8-surge-recovery2-20260722.service`, with
  `MemoryHigh=16G`, `MemoryMax=24G`, `MemorySwapMax=0`, and at most two 8K
  GAPBS/CG tasks. Its manager must verify the remaining live cgroups and the
  unchanged 272 GiB aggregate ceiling. The T32 launcher must treat this lane as
  an allowed owned cgroup, and the serial IS recovery/finalizer must wait for
  and record it.
- XRAGE 64K is a required sweep point, not an unsupported exception. Build the
  `spatter_maa_64K` target with `TILE_SIZE=65536`, then reuse the original XRAGE
  surge lane's released reservation as
  `dx100-full-tile-xrage64-recovery2-20260722.service`, with
  `MemoryHigh=24G`, `MemoryMax=32G`, `MemorySwapMax=0`, and one task. Launch it
  only after the original XRAGE surge workflow is terminal and its unit is
  inactive. The T8/T32 launchers may treat it as an allowed owned cgroup, and
  the serial IS recovery/finalizer must wait for and record it.
- Durable units inherit systemd's base PATH, not Codex's injected tool PATH.
  Tile runners must use base-system utilities (`grep`, `sed`, `awk`) or an
  explicit executable path; do not make correctness classification depend on
  `rg` being available. Retry paths must reuse an existing run only after
  independently rechecking its exact oracle, stats, clean exit, and absence of
  panic/fatal markers.
- A terminal normal workflow with failed/skipped tasks receives at most one
  automatic `dx-runtime workflow resume --retry-failed` pass through
  `dx100-full-tile-normal-retry-recovery2-20260721.service`, using the same
  128/144 GiB and zero-swap limits. Do not loop retries; inspect any failures
  left after that pass.
- If the IS gate's immutable runner snapshot fails only in post-processing,
  retry it at most once through
  `dx100-is-exit-gate-retry-recovery2-20260721.service`, retaining the gate's
  80/96 GiB and zero-swap limits. The source runner must revalidate the exact IS
  oracle, final stats, clean `m5_exit`, and absence of panic/fatal markers before
  reusing artifacts.
