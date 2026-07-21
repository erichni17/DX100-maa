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
`MemoryMax`, and zero `MemorySwapMax`. Normal 2-GiB configurations run at most
eight at a time; full-Class-B IS runs strictly one at a time. Before each phase,
the normal manager and isolated IS gate both require at least 96 GiB host
`MemAvailable` and five continuous minutes without swap-counter movement. They
record PID plus kernel start time and refuse to start when an owned gem5, tile
runner, or `dx-runtime` process is already live. Each manager independently
reads its cgroup limits and exits before launching any child if the hard
boundary is absent or different.

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
workflow state is reused rather than launched again; the original 220/240 GiB
manager runs only the remaining serial IS workflow and final validation.
