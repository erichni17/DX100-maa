# Fused direct-sink live-drain control audit

## Verdict

The fused direct-sink path does **not** introduce a live-checkpoint drain
regression on commit `43a48f16a73c7046c758bbcb6bf58b43f3c02847`.
The original four-CPU SE treatment is invalid as a drain oracle: it launches a
single guest workload with `-n 4`, and the unused O3 CPUs
`system.switch_cpus1`, `system.switch_cpus2`, and `system.switch_cpus3` never
signal drain completion.  A corrected one-CPU native/fused pair both entered a
genuinely live MAA drain, returned from `m5_checkpoint`, and produced the same
strictly verified result:

```
n=16384 errors=0 hash=12364084552293620495
```

Both corrected treatments used the unmodified base-43a Ramulator2 adapter.
`system.mem_ctrls` returned `Draining` initially and later signaled in both
arms.  The proposed last-write `signalDrainDone()` change is therefore **not
necessary for this treatment**.  It may still be a reasonable defensive fix
for a separately reproduced last-write-only drain, but this checkpoint failure
does not supply that reproduction and does not justify changing production
memory semantics.

## Matched treatment

The native control in `benchmarks/API/test_native_live_checkpoint.cpp` issues
the ordinary MAA chain that is semantically equivalent to the fused operation:

1. stream-load the `uint32_t` index tile;
2. gather FP64 source values;
3. multiply the gathered tile by scalar `3.0`;
4. stream-store FP64 results to the destination.

It polls for the first changed destination word and makes `m5_checkpoint` the
next guest action.  There is no diagnostic I/O between progress observation
and the pseudo-op.  A Drain trace is accepted as live only if it contains
`Failed to drain system.maa`; exact output is checked after the checkpoint
returns.

The final pair used:

- one guest ABI core (`NUM_CORES=1`) and gem5 `-n 1` in both Atomic and O3
  phases;
- `N=16384`, one virtual outstanding-write credit, and one virtual word/cycle;
- X86O3CPU, 2 GiB, the same L1/L2/L3 hierarchy, one Ramulator2 channel, one
  MAA, one indirect unit, 16,384 tile elements, and identical remaining MAA
  knobs;
- the same locally built gem5 binary and production Ramulator artifact.

The two generated `config.ini` files differ only in guest command/executable,
equivalent absolute-versus-relative Ramulator config spelling, and per-output
redirect paths.  All simulated hardware fields are identical.

## Evidence identity

| Item | SHA-256 |
|---|---|
| gem5 `build/X86/gem5.opt` | `3f7ac2ab81d5156dc6a04cce6b413469e3b600c7996b33853e26f9de44c4d754` |
| production `libramulator.so` | `76ea3a9c7467a5fc0dc04f2b5f083909c03e8b7280c1872046fc78edb2a15753` |
| base-43a `src/mem/ramulator2.cc` | `dfb69d8aaf2d8959f32c192c9335a83c0b3c10e6c1bb47abec9683f0c61848ef` |
| native guest binary | `31154876fb8a65fc95fbb683a6407a6211eca4c46e341b16ebba0459ba1017e9` |
| fused guest binary | `22142cc2a1dd666ee10ad582fdca3b90d3e2c3cfa810251d4a8888d1698a7bcb` |

The production library was loaded through
`/data1/nier/DX100/ext/ramulator2/ramulator2/libramulator.so`; the native runner
verifies both its hash and the path resolved by `ldd` before launching gem5.
No source or artifact in another worktree was modified.

Raw artifacts are outside Git:

| Arm | Evidence directory | `restore.log` SHA-256 | `config.ini` SHA-256 | Status |
|---|---|---|---|---|
| native one-core slow | `.../evidence/native-onecore-slow-base43a-v3` | `69e6c3d011d0695ae79b013f1918b319b64b089f87093f5c9984bf606869156d` | `f903d3b1e73ecffff77dc09086bf83cc549382fa6a86a225d454267be4cf2001` | checkpoint `0`, restore `0`, exact |
| fused one-core slow | `.../evidence/fused-onecore-slow-base43a` | `89ddc0e9819aee17aaa92627b1d3c8fb373c4b4e2471150c229d1225ab8a2f42` | `89c63875684a83401dbbc52343b5bd5ab84fc60e058df501a7e7e465c999da06` | checkpoint `0`, restore `0`, exact |

The common evidence prefix is
`/data1/nier/worktrees/codex-coordination/sessions/audit-live-drain-control-20260809-20260809-011612-9f6e67cd`.

## Drain comparison

### Corrected native arm

At tick `3542357468`, 22 of 288 objects needed simulation.  The named
drainables were:

- `system.mem_ctrls`;
- `system.cpu.dcache.mshr_queue` and `system.cpu.dcache.write_queue`;
- `system.cpu.icache.mshr_queue`;
- `system.cpu.l2cache.mshr_queue`;
- four instances printed as `system.l3.mshr_queue`;
- `system.maa`;
- `system.switch_cpus`.

The remaining 11 were the PacketQueue/crossbar drainables named by their own
`not drained` diagnostics.  Their completion messages, all seven cache queue
messages, and the active CPU completion were observed.  At tick `3549288853`,
`All 288 objects drained` proves that the two otherwise-unlogged named
drainables, `system.maa` and `system.mem_ctrls`, also signaled.  One newly
exposed `system.tol3bus.reqLayer2` crossbar drained in the next cycle; the
following poll reported `Drain done`.  The checkpoint returned and validation
reported `errors=0` with the expected hash.

### Corrected fused arm

At tick `3423049380`, 9 of 288 objects needed simulation.  The named
drainables were:

- `system.mem_ctrls`;
- `system.cpu.icache.mshr_queue`;
- `system.cpu.l2cache.mshr_queue`;
- `system.l3.mshr_queue`;
- `system.maa`;
- `system.maa_retirement_caches.mshr_queue`;
- `system.switch_cpus`.

The other two were the dcache and L3 PacketQueues.  Both PacketQueues, all
listed cache queues, and the active CPU signaled.  `All 288 objects drained` at
tick `3476288489` proves that `system.maa` and `system.mem_ctrls` also signaled.
After one tol3-bus crossbar drained, the poll at tick `3476289115` reported
`Drain done`.  The checkpoint returned and exact validation produced the same
hash as native.

Thus the exact persistent drainable set in each corrected arm is empty.

### Why the old four-CPU hang is not a regression result

The original native N=4097 control reached a second drain with 5/900 objects
not ready.  Its active CPU and one PacketQueue later signaled; the exact
persistent set was:

```
system.switch_cpus1
system.switch_cpus2
system.switch_cpus3
```

The same-base fused N=4097 trace began with 19/900 objects not ready.  Its
packet/crossbar queues, seven cache MSHR queues, and active CPU later signaled.
On the unpatched adapter used in this audit, its exact persistent set was the
same three unused CPUs plus `system.mem_ctrls`.  `system.maa` was absent from
the failed set in both old traces, so those probes also allowed the MAA to
quiesce before the drain poll.  Even a Ramulator signal fix cannot make that
four-core topology a valid fused-versus-native oracle because the three idle
O3 CPUs guarantee nontermination.

`configs/deprecated/example/se.py` assigns the one supplied process to every
configured CPU.  The idle restored O3 CPUs report `Fetch not drained` and never
reach the `CPU done draining` path in `src/cpu/o3/cpu.cc`.  This is an existing
SE/O3 topology problem, not fused direct-sink state.

## Ramulator assessment

Base 43a signals Ramulator drain completion only from a successful response
send when the total outstanding count reaches zero.  Its write callback
decrements `nbrOutstandingWrites` and invokes `accessAndRespond` but does not
explicitly signal a draining object.  That code predates the fused direct-sink
commits (the relevant lines blame to `a40792a0f`, 2025-03-26), and DRAMSim3 has
an explicit last-write signal.

That asymmetry is worth a focused adapter regression, but it is not the cause
of the corrected live-checkpoint treatment: the unpatched adapter's
`system.mem_ctrls` signaled in both native and fused arms.  The minimal
defensible decision is to leave production Ramulator code unchanged for this
audit.  Promote the candidate only after a narrow test makes
`system.mem_ctrls` the sole remaining drainable and demonstrates that the
callback is the transition from one outstanding write to zero, with no later
response event available.

## Minimal correction

1. Use a dedicated one-core guest layout and `-n 1`; do not try to repair the
   experiment by ignoring or force-draining CPUs.
2. Make the checkpoint pseudo-op immediately follow observed destination
   progress.  Do not put flushed diagnostics between the observation and the
   pseudo-op.
3. Require the Drain trace to name `system.maa`, then require eventual
   `Drain done`, checkpoint return, and exact post-return output/hash.
4. Use N=16384 with the one-credit/one-word-per-cycle throttle.  The exploratory
   N=4097 native arm returned but left 81 sentinel elements, so it is not a
   valid correctness oracle and was not accepted.
5. Do not weaken correctness, suppress drainables, or add the Ramulator patch
   merely to make the invalid four-core timeout disappear.
