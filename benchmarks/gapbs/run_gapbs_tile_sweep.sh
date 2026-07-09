#!/usr/bin/env bash
# run_gapbs_tile_sweep.sh -- multi-kernel GAPBS tile sweep (checkpoint+restore).
# Usage:
#   run_gapbs_tile_sweep.sh [gem5_binary] [scale] [tiles] [kernels] [iters] [mem_size] [restore_timeout] [ckpt_timeout] [prog_interval]
# Example:
#   run_gapbs_tile_sweep.sh gem5.opt.ovl_base 22 "4096 8192 16384 32768" "bfs pr sssp" 1 2GB
set -euo pipefail

GH=/data1/nier/DX100
RUNNER=$GH/benchmarks/gapbs/run_gapbs_tile_smoke.sh

GBIN=${1:-gem5.opt.ovl_base}
SCALE=${2:-22}
TILES=${3:-"4096 8192 16384 32768"}
KERNELS=${4:-"bfs pr sssp"}
ITERS=${5:-1}
MEM_SIZE=${6:-2GB}
RESTORE_TIMEOUT=${7:-${RESTORE_TIMEOUT:-14400}}
CKPT_TIMEOUT=${8:-${CKPT_TIMEOUT:-3600}}
PROG_INTERVAL=${9:-${PROG_INTERVAL:-1000}}

echo "[sweep] gem5=$GBIN scale=$SCALE tiles=[$TILES] kernels=[$KERNELS] mem=$MEM_SIZE restore_timeout=${RESTORE_TIMEOUT}s ckpt_timeout=${CKPT_TIMEOUT}s prog_interval=$PROG_INTERVAL"

for k in $KERNELS; do
  for t in $TILES; do
    echo "[sweep] kernel=$k tile=$t start"
    if "$RUNNER" "$GBIN" "$k" "$t" "$SCALE" "$ITERS" "$MEM_SIZE" "$RESTORE_TIMEOUT" "$CKPT_TIMEOUT" "$PROG_INTERVAL"; then
      echo "[sweep] kernel=$k tile=$t done rc=0"
    else
      rc=$?
      echo "[sweep] kernel=$k tile=$t done rc=$rc"
    fi
  done
done

echo "[sweep] complete"
