#!/usr/bin/env bash
# run_gapbs_tile_sweep.sh -- multi-kernel GAPBS tile sweep (checkpoint+restore).
# Usage:
#   run_gapbs_tile_sweep.sh [gem5_binary] [scale] [tiles] [kernels] [iters] [mem_size]
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

echo "[sweep] gem5=$GBIN scale=$SCALE tiles=[$TILES] kernels=[$KERNELS] mem=$MEM_SIZE"

for k in $KERNELS; do
  for t in $TILES; do
    echo "[sweep] kernel=$k tile=$t start"
    if "$RUNNER" "$GBIN" "$k" "$t" "$SCALE" "$ITERS" "$MEM_SIZE"; then
      echo "[sweep] kernel=$k tile=$t done rc=0"
    else
      rc=$?
      echo "[sweep] kernel=$k tile=$t done rc=$rc"
    fi
  done
done

echo "[sweep] complete"
