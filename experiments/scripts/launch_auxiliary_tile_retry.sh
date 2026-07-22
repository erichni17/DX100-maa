#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=/data1/nier/worktrees/DX100-full-tile-sweep-20260720
RUN_ROOT=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep
STATE_ROOT=/data1/nier/.dx-runtime-state
PRIMARY_STATE="$STATE_ROOT/workflows/dx100-full-tile-sweep-recovery2-normal-20260721.json"
UNIT=dx100-full-tile-auxiliary-retry-recovery2-20260721
ORIGINAL_UNIT=dx100-full-tile-auxiliary-recovery2-20260721.service
NORMAL_UNIT=dx100-full-tile-normal-recovery2-20260721.service
GATE_UNIT=dx100-is-exit-gate-recovery2-20260721.service

if systemctl --user is-active --quiet "$ORIGINAL_UNIT"; then
  echo "original auxiliary unit is still active" >&2
  exit 1
fi

NORMAL_CGROUP_REL=$(systemctl --user show "$NORMAL_UNIT" --property=ControlGroup --value)
GATE_CGROUP_REL=$(systemctl --user show "$GATE_UNIT" --property=ControlGroup --value)
[[ -n "$NORMAL_CGROUP_REL" && -n "$GATE_CGROUP_REL" ]]
NORMAL_CGROUP=/sys/fs/cgroup${NORMAL_CGROUP_REL}
GATE_CGROUP=/sys/fs/cgroup${GATE_CGROUP_REL}

exec systemd-run --user --no-block --collect \
  --unit="$UNIT" \
  --description="DX100 one-shot OOM-contained auxiliary tile retry" \
  --working-directory="$SOURCE_ROOT" \
  --property=MemoryAccounting=yes \
  --property=MemoryHigh=24G \
  --property=MemoryMax=32G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  --property=KillMode=control-group \
  /usr/bin/python3 "$SOURCE_ROOT/experiments/scripts/run_normal_tile_recovery.py" \
  --state-root "$STATE_ROOT" \
  --workflow "$RUN_ROOT/recovery2-auxiliary-workflow.json" \
  --run-root "$RUN_ROOT" \
  --allowed-live-cgroup "$NORMAL_CGROUP" \
  --allowed-live-cgroup "$GATE_CGROUP" \
  --aggregate-memory-max-gib 240 \
  --primary-workflow-state "$PRIMARY_STATE" \
  --retry-failed \
  --parallel 1 \
  --available-gib 128 \
  --expected-memory-high-gib 24 \
  --expected-memory-max-gib 32 \
  --artifact-stem recovery2-auxiliary-retry-manager
