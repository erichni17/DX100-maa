# Long-running process control on mbit1

## Incident and recurrence audit

The 2026-07-20 tile-sweep session paused for 35,871.7 seconds (9:57:52)
waiting for approval of an exact `kill PID...` command used to retire hung
Black workers. The saved prefix contained the transient PIDs and therefore had
no future value. A second Black invocation reproduced the same hang and another
exact-PID approval prompt.

A read-only audit of `~/.codex/sessions/2026/07` found:

- 151 command calls, 22 escalated calls, 18 tmux-related calls, and three raw
  kill call sites in the current session;
- 32 escalated calls, four raw kill call sites, and 28 tmux `kill-session` call
  sites across the July transcripts searched.

These are transcript call-site counts, not a claim that every call executed.
They establish that exact-PID escalation is a recurring operational trap and
that named tmux ownership is already the dominant working control path.

## Required operating pattern

1. Start long or potentially hanging helpers in a uniquely named tmux session.
2. Record the session name, wrapper identity, workflow state, and output path.
3. Use `dx-runtime` for workflow ownership and terminal state.
4. For cancellation, verify the named session, request cooperative shutdown,
   and retire the session through tmux. Do not signal gem5 directly.
5. Put memory admission in a durable supervisor. It should wait rather than
   start a batch when `MemAvailable` is below its reserved headroom.
6. Keep each escalated tool call to one command so Codex's argument-prefix rule
   matches the intended controller instead of a trailing pipeline segment.

The Codex rules engine matches argument prefixes exactly. Saving approval for
`kill 123 456` can only match those same transient arguments; allowing bare
`kill` would be dangerously broad. Official Codex guidance recommends narrow
prefix rules and documents `approvals_reviewer = "auto_review"` as a middle
ground for interactive approval policies. The only generally prompt-free
configuration is `--ask-for-approval never`; pairing it with
`danger-full-access` also removes the sandbox and should be reserved for an
explicitly trusted, isolated environment.

Source: <https://learn.chatgpt.com/docs/agent-configuration/rules> and
<https://learn.chatgpt.com/docs/agent-approvals-security>.

## Tile-sweep implementation

`wait_and_run_full_tile_repair.py` waits for enough `MemAvailable`, selects a
parallelism limit that keeps a 32 GiB reserve under a conservative 16 GiB per
new simulation allowance, requires five continuous minutes without swap-counter
movement, and then `exec`s the repair workflow. The supervisor itself is
launched in tmux, so it can be inspected or retired without a raw PID signal. It
never kills or pauses an existing simulation.

## 2026-07-21 OOM reboot

The original fixed-parallelism workflow admitted seven corrected full-Class-B
NAS IS jobs concurrently. Two were killed with rc=137; the five survivors held
316,161,776 KiB RSS (301.5 GiB) by themselves. An early pre-crash snapshot had
316 of 330 GiB in use and about 12 GiB `MemAvailable`. The timestamped
`vmstat.log` later reached its worst recorded point at 13:40:53 EDT: only
420,616 KiB of 346,566,728 KiB RAM was free, buffers plus cache were just
810,332 KiB, and all 2,097,148 KiB of swap was occupied. That corresponds to
345,335,780 KiB (329.338 GiB) of non-cache RAM in use and only 1.174 GiB left
as free, buffers, and cache. The account cannot read the prior boot's kernel
journal, so this proves severe memory exhaustion and thrashing but not a saved
kernel OOM-killer event. The host rebooted at 13:53:46 EDT. The old workflow
JSON still said 18 tasks were running, but no corresponding processes survived.

The recovery campaign therefore does not resume the stale workflow. It uses a
new systemd-owned manager with a campaign-wide 220 GiB `MemoryHigh`, 240 GiB
`MemoryMax`, and zero `MemorySwapMax`. The initial emergency recovery serialized
full-Class-B IS. That fixed task-count rule was superseded on 2026-07-27 by
memory-token admission: each live IS task reserves 64 GiB, and the number of
new tasks comes from projected memory headroom rather than a hard-coded process
count. Before every launch, the controller subtracts the exact 10% host reserve
and every active full-tile service's unconsumed `MemoryMax - MemoryCurrent`
reservation from `MemAvailable`. Admission is serialized by a runtime lock and
recomputed after every launch and completion. Each IS task retains independent
`MemoryHigh=60G`, `MemoryMax=64G`, and `MemorySwapMax=0` containment. Active
swap, memory PSI, max/OOM events, or insufficient projected headroom blocks new
admission without terminating healthy running tasks.

The first contained IS gate exposed an independent logging failure and was
stopped through its named systemd unit. gem5's `--prog-interval` is a
`Param.Frequency`, despite its name. Setting it to `1000000000000` requested a
1 THz progress frequency and produced `progress_interval=1` in `config.ini`, so
four CPUs printed a line every simulated tick. The run emitted 188,840,011
lines in its first 22 GiB and made unusably slow progress. IS recovery commands
now pass the runner's zero sentinel, which omits `--prog-interval` and preserves
BaseCPU's `0Hz` default. The stopped attempt is retained under
`failed-attempts/2026-07-21-is-gate-every-tick-progress`.

The IS exit/correctness gate blocks only the remaining six IS points. It does
not block the 46 non-IS recovery points. Those points may run concurrently in
the dedicated `dx100-full-tile-normal-recovery2-20260721.service` with
`MemoryHigh=128G`, `MemoryMax=144G`, and `MemorySwapMax=0`. Its manager admits
only the already-owned IS gate cgroup as a live campaign conflict and rejects
all other owned gem5/runtime processes. The two concurrent services therefore
have an aggregate hard maximum of 240 GiB. After both are terminal, the normal
workflow state is reused rather than launched again. The original serial IS
workflow remains an authoritative revalidation chain, while
`run_memory_admitted_is_recovery.py` may run far-end pending IS outputs early
when memory tokens are available. Output-specific locks prevent duplicate
execution, and the authoritative wrapper independently fast-reuses each
completed artifact.

The first two UME tasks in the overlapping normal unit exposed a service-PATH
failure: gem5 completed with the exact output hash, exact reference PASS, final
stats, and clean `m5_exit`, but the wrapper returned rc=90 because `rg` exists
only in Codex's injected PATH and is absent from systemd's base PATH. All tile
runners now use base-system `grep` for terminal classification. They also have
a retry-only fast path that accepts an existing output directory only after
rechecking the workload oracle, nonempty stats, clean `m5_exit`, and absence of
panic/fatal markers, then appends a successor rc=0 result row. This lets
`dx-runtime workflow resume --retry-failed` repair wrapper-only failures
without repeating a completed simulation. The immutable snapshots of tasks
already in flight remain untouched; their artifacts are repaired only through
the explicit retry path after the workflow becomes terminal.

The normal workflow receives exactly one automatic retry pass after its first
terminal state. The retry runs through
`dx100-full-tile-normal-retry-recovery2-20260721.service` with the same
128/144 GiB zero-swap cgroup and five-minute admission gate. Correctness-complete
wrapper failures take the fast reuse path; incomplete or genuinely failing
runs are attempted normally. Any task still failed after that one pass requires
inspection rather than an unbounded retry loop. The post-overlap callback
requires all 46 normal tasks to be completed, so the remaining IS phase cannot
silently advance past unresolved normal failures.

The isolated 16K IS gate has the same one-pass repair rule. Its retry runs in
`dx100-is-exit-gate-retry-recovery2-20260721.service` with the original 80/96
GiB zero-swap cgroup. The repair may overlap the normal 128/144 GiB group, but
only after the original gate service is inactive and after the same five-minute
host admission check. An artifact-reuse success still requires the exact IS
verification marker, final stats, clean `m5_exit`, and no panic/fatal marker.
