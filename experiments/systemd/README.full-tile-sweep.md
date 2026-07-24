# Full tile sweep memory containment

`dx100-full-tile-sweep.slice` is the production aggregate memory boundary for
future full-tile transient services:

- `MemoryHigh=256G` applies reclaim pressure before the emergency boundary.
- `MemoryMax=272G` is the campaign-wide hard cap.
- `MemorySwapMax=0` prevents campaign memory from entering swap.
- child services retain their own `MemoryHigh`, `MemoryMax`, `OOMPolicy=stop`,
  and `KillMode=control-group` settings.

The repository is inert. Merely checking out or merging these files does not
install, enable, start, stop, or reparent anything. The deployment command
copies the reviewed unit into the stable user-systemd configuration rather
than linking it to a disposable worktree.

## Why this is the production boundary

The server has 330.51 binary GiB of RAM. Its exact 90% threshold is about
297.46 GiB; the migration ceiling is intentionally rounded down to 296 GiB.
The slice's 272 GiB hard cap leaves roughly 58.5 GiB outside the campaign for
the kernel, SSH sessions, other users, and services.

Child `MemoryMax` values may sum above 272 GiB after migration. The parent
slice, not a hand-maintained sum of child reservations, enforces the real
aggregate ceiling. This permits more concurrent simulations without exposing
the host to their combined worst case.

During migration, active legacy services remain outside the slice. Their
hard caps plus the proposed 272 GiB slice must remain at or below both 296 GiB
and 90% of physical RAM. Consequently, a full-sized slice may coexist with no
more than 24 GiB of active legacy caps. Prefer zero legacy services at cutover.

## One-time, no-sudo deployment

First merge the reviewed implementation to the repository's canonical
`dx100-improvements` branch. From that stable checkout, inspect the inert
plan:

```sh
python3 experiments/scripts/deploy_full_tile_slice.py plan
```

At a clean task boundary, install the copy and reload the user manager:

```sh
python3 experiments/scripts/deploy_full_tile_slice.py install --apply
python3 experiments/scripts/deploy_full_tile_slice.py verify
```

Installation does **not** start or enable the slice. The first admitted
transient service activates it automatically. A different pre-existing unit
is never overwritten unless an operator separately reviews it and supplies
`--replace`.

System-wide `earlyoom` is a separate, root-managed fallback. It is not needed
for this user-owned containment path.

## Admission and migration check

Run the one-shot check before cutover or an unusual manual launch:

```sh
python3 experiments/scripts/preflight_full_tile_memory.py
```

The checker:

- discovers active `dx100-full-tile-*` and `dx100-is-exit-gate-*` services;
- excludes services already parented by `dx100-full-tile-sweep.slice`;
- verifies every active legacy service has a real binary-GiB `MemoryMax`;
- accounts for those legacy caps during migration;
- requires at least 50 GiB `MemAvailable` and zero current memory PSI;
- samples `pswpin`/`pswpout` over five seconds and rejects active swapping;
- rejects swap or OOM/max events in campaign and legacy cgroups.

`/proc/vmstat` counters are cumulative since boot. Nonzero totals alone do
not mean the host is swapping now. Stable host/user-manager swap occupancy is
reported as a warning, while a positive sampled delta or any campaign-cgroup
swap remains a hard refusal.

The JSON field `safe_slice_cap_gib` is:

```text
min(272 GiB, 296 GiB - active legacy MemoryMax sum)
```

The launcher always proposes the production 272 GiB cap; it therefore refuses
to launch while the active legacy sum exceeds 24 GiB.

## Canonical transient launch

After cutover, every full-tile service must use
`run_full_tile_transient.py`; do not invoke the dated legacy launchers:

```sh
python3 experiments/scripts/run_full_tile_transient.py \
  --unit=dx100-full-tile-example \
  --description="DX100 contained example lane" \
  --working-directory="$PWD" \
  --memory-high-gib=56 \
  --memory-max-gib=64 \
  -- \
  /usr/bin/python3 experiments/scripts/run_normal_tile_recovery.py \
  --help
```

The launcher takes a user-runtime admission lock, verifies the installed
slice has exactly `256G/272G/0`, performs the migration and pressure checks,
and calls `systemd-run --user --slice=dx100-full-tile-sweep.slice`. It waits
only for systemd's startup transaction, not for simulation completion, and
sets no runtime timeout. After startup it verifies both the child and parent
`Slice`/`ControlGroup` assignments and their effective kernel
`memory.high`/`memory.max`/`memory.swap.max` values. It then repeats the
migration preflight while still holding the admission lock. If containment or
the second snapshot fails, it stops only the newly created, authenticated
unit and reports failure.

`--dry-run` performs all pre-launch checks and prints the proposed
`systemd-run` argument vector without starting a service.

After migration, treat any service matching the full-tile naming patterns but
outside the slice as a legacy service. The preflight will reserve its complete
hard cap and fail closed if that makes the transition unsafe. The lock can
serialize only this canonical launcher; all dated/manual full-tile launch
paths must be retired at cutover. The wrapper does not replace workflow task
leases or live-process ownership checks.
