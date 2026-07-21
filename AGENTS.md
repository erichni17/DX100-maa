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
