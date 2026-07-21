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
316,161,776 KiB RSS (301.5 GiB) by themselves. Immediately before the host
became unreachable, total memory use was 316 of 330 GiB, `MemAvailable` was
about 12 GiB, all 2 GiB of swap was occupied, and `vmstat` showed intermittent
swap traffic. The host rebooted at 13:53:46 EDT. The old workflow JSON still
said 18 tasks were running, but no corresponding processes survived.

The recovery campaign therefore does not resume the stale workflow. It uses a
new systemd-owned manager with a campaign-wide 220 GiB `MemoryHigh`, 240 GiB
`MemoryMax`, and zero `MemorySwapMax`. Normal 2-GiB configurations run at most
eight at a time; full-Class-B IS runs strictly one at a time. Before each phase,
the manager also requires at least 96 GiB host `MemAvailable` and five continuous
minutes without swap-counter movement. It records PID plus kernel start time
and refuses to start when an owned gem5, tile runner, or `dx-runtime` process is
already live. The manager independently reads its cgroup limits and exits before
launching any child if the hard boundary is absent or different.
