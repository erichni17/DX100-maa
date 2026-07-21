#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=/data1/nier/worktrees/DX100-full-tile-sweep-20260720
RUN_ROOT=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep
STATE_ROOT=/data1/nier/.dx-runtime-state
UNIT=dx100-full-tile-normal-recovery2-20260721
GATE_UNIT=dx100-is-exit-gate-recovery2-20260721.service
GATE_CGROUP_REL=$(systemctl --user show "$GATE_UNIT" --property=ControlGroup --value)
if [[ -z "$GATE_CGROUP_REL" || ! -d "/sys/fs/cgroup$GATE_CGROUP_REL" ]]; then
  echo "active IS gate cgroup is unavailable: $GATE_UNIT" >&2
  exit 1
fi
GATE_CGROUP=/sys/fs/cgroup$GATE_CGROUP_REL

exec systemd-run --user --no-block --collect \
  --unit="$UNIT" \
  --description="DX100 OOM-contained non-IS tile recovery" \
  --working-directory="$SOURCE_ROOT" \
  --property=MemoryAccounting=yes \
  --property=MemoryHigh=128G \
  --property=MemoryMax=144G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  --property=KillMode=control-group \
  /usr/bin/python3 "$SOURCE_ROOT/experiments/scripts/run_normal_tile_recovery.py" \
  --state-root "$STATE_ROOT" \
  --workflow "$RUN_ROOT/recovery2-normal-workflow.json" \
  --run-root "$RUN_ROOT" \
  --allowed-live-cgroup "$GATE_CGROUP"
