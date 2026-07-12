#!/usr/bin/env bash
# Sequential NAS CG tile sweep. Individual runs can also be launched in parallel tmux sessions.
set -euo pipefail
GH=/data1/nier/DX100
GBIN=${1:-gem5.opt.ovl_base}
TILES=${2:-"4096 8192 16384"}
MEM_SIZE=${3:-2GB}
RESTORE_TIMEOUT=${4:-${RESTORE_TIMEOUT:-21600}}
CKPT_TIMEOUT=${5:-${CKPT_TIMEOUT:-3600}}
PROG_INTERVAL=${6:-${PROG_INTERVAL:-1000}}
RUNNER=$GH/benchmarks/NAS/cg/run_cg_tile_smoke.sh
for tile in $TILES; do
  echo "[sweep] CG tile=$tile"
  "$RUNNER" "$GBIN" "$tile" "$MEM_SIZE" "$RESTORE_TIMEOUT" "$CKPT_TIMEOUT" "$PROG_INTERVAL" || \
    echo "[sweep] tile=$tile failed rc=$?"
done
