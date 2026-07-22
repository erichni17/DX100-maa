#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=/data1/nier/worktrees/DX100-full-tile-sweep-20260720
RUN_ROOT=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep
STATE_ROOT=/data1/nier/.dx-runtime-state
UNIT=dx100-is-exit-gate-retry-recovery2-20260721
NORMAL_UNIT=dx100-full-tile-normal-recovery2-20260721.service
NORMAL_CGROUP_REL=$(systemctl --user show "$NORMAL_UNIT" --property=ControlGroup --value 2>/dev/null || true)
NORMAL_CGROUP=/sys/fs/cgroup${NORMAL_CGROUP_REL:-/normal-not-active}

exec systemd-run --user --no-block --collect \
  --unit="$UNIT" \
  --description="DX100 OOM-contained IS gate retry" \
  --working-directory="$SOURCE_ROOT" \
  --property=MemoryAccounting=yes \
  --property=MemoryHigh=80G \
  --property=MemoryMax=96G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  --property=KillMode=control-group \
  /usr/bin/python3 "$SOURCE_ROOT/experiments/scripts/run_normal_tile_recovery.py" \
  --state-root "$STATE_ROOT" \
  --workflow "$RUN_ROOT/recovery2-is-gate-workflow.json" \
  --run-root "$RUN_ROOT" \
  --allowed-live-cgroup "$NORMAL_CGROUP" \
  --retry-failed \
  --parallel 1 \
  --available-gib 96 \
  --expected-memory-high-gib 80 \
  --expected-memory-max-gib 96 \
  --artifact-stem recovery2-is-gate-retry-manager
