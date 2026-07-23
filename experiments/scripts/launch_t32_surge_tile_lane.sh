#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=/data1/nier/worktrees/DX100-full-tile-sweep-20260720
RUN_ROOT=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep
STATE_ROOT=/data1/nier/.dx-runtime-state
PRIMARY_STATE="$STATE_ROOT/workflows/dx100-full-tile-sweep-recovery2-normal-20260721.json"
UNIT=dx100-full-tile-t32-surge-recovery2-20260722

allowed=()
for unit in \
    dx100-full-tile-normal-recovery2-20260721.service \
    dx100-full-tile-normal-retry-recovery2-20260721.service \
    dx100-is-exit-gate-recovery2-20260721.service \
    dx100-full-tile-surge-recovery2-20260722.service \
    dx100-full-tile-ume-surge-recovery2-20260722.service \
    dx100-full-tile-t8-surge-recovery2-20260722.service \
    dx100-full-tile-xrage64-recovery2-20260722.service; do
  relative=$(systemctl --user show "$unit" --property=ControlGroup --value 2>/dev/null || true)
  cgroup=/sys/fs/cgroup${relative}
  if [[ -n "$relative" && -d "$cgroup" ]]; then
    allowed+=(--allowed-live-cgroup "$cgroup")
  fi
done
if ((${#allowed[@]} == 0)); then
  printf 'refusing 32K launch without any owned live cgroup\n' >&2
  exit 1
fi

exec systemd-run --user --no-block --collect \
  --unit="$UNIT" \
  --description="DX100 OOM-contained 32K tile surge lane" \
  --working-directory="$SOURCE_ROOT" \
  --property=MemoryAccounting=yes \
  --property=MemoryHigh=24G \
  --property=MemoryMax=32G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  --property=KillMode=control-group \
  /usr/bin/python3 "$SOURCE_ROOT/experiments/scripts/run_normal_tile_recovery.py" \
  --state-root "$STATE_ROOT" \
  --workflow "$RUN_ROOT/recovery2-t32-surge-workflow.json" \
  --run-root "$RUN_ROOT" \
  "${allowed[@]}" \
  --aggregate-memory-max-gib 272 \
  --primary-workflow-state "$PRIMARY_STATE" \
  --parallel 3 \
  --available-gib 96 \
  --swap-quiet-seconds 300 \
  --expected-memory-high-gib 24 \
  --expected-memory-max-gib 32 \
  --artifact-stem recovery2-t32-surge-manager
