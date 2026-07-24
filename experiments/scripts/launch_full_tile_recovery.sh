#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=/data1/nier/worktrees/DX100-full-tile-sweep-20260720
RUN_ROOT=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep
STATE_ROOT=/data1/nier/.dx-runtime-state
UNIT=dx100-full-tile-recovery2-20260721

allowed=()
for unit in \
    dx100-full-tile-normal-recovery2-20260721.service \
    dx100-full-tile-normal-retry-recovery2-20260721.service \
    dx100-full-tile-auxiliary-recovery2-20260721.service \
    dx100-full-tile-auxiliary-retry-recovery2-20260721.service \
    dx100-full-tile-surge-recovery2-20260722.service \
    dx100-full-tile-ume-surge-recovery2-20260722.service \
    dx100-full-tile-t32-surge-recovery2-20260722.service \
    dx100-full-tile-t8-surge-recovery2-20260722.service \
    dx100-full-tile-xrage64-recovery2-20260722.service; do
  relative=$(systemctl --user show "$unit" --property=ControlGroup --value 2>/dev/null || true)
  cgroup=/sys/fs/cgroup${relative}
  if [[ -n "$relative" && -d "$cgroup" ]]; then
    allowed+=(--allowed-live-cgroup "$cgroup")
  fi
done
if ((${#allowed[@]} == 0)); then
  printf 'refusing IS launch without any owned live cgroup\n' >&2
  exit 1
fi

exec systemd-run --user --no-block --collect \
  --unit="$UNIT" \
  --description="DX100 OOM-contained serial IS tile lane" \
  --working-directory="$SOURCE_ROOT" \
  --property=MemoryAccounting=yes \
  --property=MemoryHigh=72G \
  --property=MemoryMax=80G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  --property=KillMode=control-group \
  /usr/bin/python3 "$SOURCE_ROOT/experiments/scripts/run_normal_tile_recovery.py" \
  --state-root "$STATE_ROOT" \
  --workflow "$RUN_ROOT/recovery2-is-workflow.json" \
  --run-root "$RUN_ROOT" \
  "${allowed[@]}" \
  --aggregate-memory-max-gib 272 \
  --parallel 1 \
  --available-gib 96 \
  --swap-quiet-seconds 300 \
  --expected-memory-high-gib 72 \
  --expected-memory-max-gib 80 \
  --artifact-stem recovery2-is-manager
