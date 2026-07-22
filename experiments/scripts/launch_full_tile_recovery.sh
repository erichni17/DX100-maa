#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=/data1/nier/worktrees/DX100-full-tile-sweep-20260720
RUN_ROOT=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep
STATE_ROOT=/data1/nier/.dx-runtime-state
UNIT=dx100-full-tile-recovery2-20260721

exec systemd-run --user --no-block --collect \
  --unit="$UNIT" \
  --description="DX100 OOM-contained full tile recovery" \
  --working-directory="$SOURCE_ROOT" \
  --property=MemoryAccounting=yes \
  --property=MemoryHigh=220G \
  --property=MemoryMax=240G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  --property=KillMode=control-group \
  /usr/bin/python3 "$SOURCE_ROOT/experiments/scripts/run_full_tile_recovery.py" \
  --state-root "$STATE_ROOT" \
  --normal-workflow "$RUN_ROOT/recovery2-normal-workflow.json" \
  --is-workflow "$RUN_ROOT/recovery2-is-workflow.json" \
  --run-root "$RUN_ROOT" \
  --is-parallel 3
