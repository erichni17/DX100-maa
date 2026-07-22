#!/usr/bin/env bash
set -euo pipefail

SOURCE_ROOT=/data1/nier/worktrees/DX100-full-tile-sweep-20260720
RUN_ROOT=/data1/nier/dx100-runs/2026-07-20-full-tile-sweep
STATE_ROOT=/data1/nier/.dx-runtime-state
UNIT=dx100-full-tile-normal-retry-recovery2-20260721
WORKFLOW=$RUN_ROOT/recovery2-normal-retry-workflow-v2.json
PATCHED_GEM5=$SOURCE_ROOT/build/X86/gem5.opt
PATCH_MANIFEST=$RUN_ROOT/recovery2-prefetch-fix-manifest.json
GATE_UNIT=dx100-is-exit-gate-recovery2-20260721.service
GATE_CGROUP_REL=$(systemctl --user show "$GATE_UNIT" --property=ControlGroup --value 2>/dev/null || true)
GATE_CGROUP=/sys/fs/cgroup${GATE_CGROUP_REL:-/gate-not-active}

[[ -x "$PATCHED_GEM5" && -s "$WORKFLOW" && -s "$PATCH_MANIFEST" ]]

exec systemd-run --user --no-block --collect \
  --unit="$UNIT" \
  --description="DX100 OOM-contained non-IS tile retry" \
  --working-directory="$SOURCE_ROOT" \
  --property=MemoryAccounting=yes \
  --property=MemoryHigh=128G \
  --property=MemoryMax=144G \
  --property=MemorySwapMax=0 \
  --property=OOMPolicy=stop \
  --property=KillMode=control-group \
  /usr/bin/python3 "$SOURCE_ROOT/experiments/scripts/run_normal_tile_recovery.py" \
  --state-root "$STATE_ROOT" \
  --workflow "$WORKFLOW" \
  --run-root "$RUN_ROOT" \
  --allowed-live-cgroup "$GATE_CGROUP" \
  --retry-failed
