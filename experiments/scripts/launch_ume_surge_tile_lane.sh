#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=/data1/nier/worktrees/DX100-full-tile-sweep-20260720
RUN_ROOT=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep
STATE_ROOT=/data1/nier/.dx-runtime-state
PRIMARY_STATE="$STATE_ROOT/workflows/dx100-full-tile-sweep-recovery2-normal-20260721.json"
UNIT=dx100-full-tile-ume-surge-recovery2-20260722
NORMAL_UNIT=dx100-full-tile-normal-recovery2-20260721.service
GATE_UNIT=dx100-is-exit-gate-recovery2-20260721.service
AUXILIARY_UNIT=dx100-full-tile-auxiliary-recovery2-20260721.service
XRAGE_SURGE_UNIT=dx100-full-tile-surge-recovery2-20260722.service

NORMAL_CGROUP_REL=$(systemctl --user show "$NORMAL_UNIT" --property=ControlGroup --value)
GATE_CGROUP_REL=$(systemctl --user show "$GATE_UNIT" --property=ControlGroup --value)
AUXILIARY_CGROUP_REL=$(systemctl --user show "$AUXILIARY_UNIT" --property=ControlGroup --value)
XRAGE_SURGE_CGROUP_REL=$(systemctl --user show "$XRAGE_SURGE_UNIT" --property=ControlGroup --value)
[[ -n "$NORMAL_CGROUP_REL" && -n "$GATE_CGROUP_REL" && -n "$AUXILIARY_CGROUP_REL" && -n "$XRAGE_SURGE_CGROUP_REL" ]]
NORMAL_CGROUP=/sys/fs/cgroup${NORMAL_CGROUP_REL}
GATE_CGROUP=/sys/fs/cgroup${GATE_CGROUP_REL}
AUXILIARY_CGROUP=/sys/fs/cgroup${AUXILIARY_CGROUP_REL}
XRAGE_SURGE_CGROUP=/sys/fs/cgroup${XRAGE_SURGE_CGROUP_REL}

exec systemd-run --user --no-block --collect \
  --unit="$UNIT" \
  --description="DX100 OOM-contained UME surge tile lane" \
  --working-directory="$SOURCE_ROOT" \
  --property=MemoryAccounting=yes \
  --property=MemoryHigh=16G \
  --property=MemoryMax=24G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  --property=KillMode=control-group \
  /usr/bin/python3 "$SOURCE_ROOT/experiments/scripts/run_normal_tile_recovery.py" \
  --state-root "$STATE_ROOT" \
  --workflow "$RUN_ROOT/recovery2-ume-surge-workflow.json" \
  --run-root "$RUN_ROOT" \
  --allowed-live-cgroup "$NORMAL_CGROUP" \
  --allowed-live-cgroup "$GATE_CGROUP" \
  --allowed-live-cgroup "$AUXILIARY_CGROUP" \
  --allowed-live-cgroup "$XRAGE_SURGE_CGROUP" \
  --aggregate-memory-max-gib 272 \
  --primary-workflow-state "$PRIMARY_STATE" \
  --parallel 3 \
  --available-gib 96 \
  --swap-quiet-seconds 300 \
  --expected-memory-high-gib 16 \
  --expected-memory-max-gib 24 \
  --artifact-stem recovery2-ume-surge-manager
