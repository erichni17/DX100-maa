#!/usr/bin/env bash
# run_ume_tile_sweep.sh -- multi-kernel UME tile sweep (checkpoint+restore).
# Usage:
#   run_ume_tile_sweep.sh [gem5_binary] [tiles] [kernels] [n] [mem_size]
# Example:
#   run_ume_tile_sweep.sh gem5.opt.ovl_base "4096 8192 16384 32768" "gradzatz gradzatp" 1000000 2GB
set -euo pipefail

GH=/data1/nier/DX100
RUNNER=$GH/benchmarks/UME/run_ume_tile_smoke.sh

GBIN=${1:-gem5.opt.ovl_base}
TILES=${2:-"4096 8192 16384 32768"}
KERNELS=${3:-"gradzatz gradzatp"}
N=${4:-1000000}
MEM_SIZE=${5:-2GB}

echo "[sweep] gem5=$GBIN tiles=[$TILES] kernels=[$KERNELS] n=$N mem=$MEM_SIZE"

for k in $KERNELS; do
  for t in $TILES; do
    echo "[sweep] kernel=$k tile=$t start"
    if "$RUNNER" "$GBIN" "$k" "$t" "$N" "$MEM_SIZE"; then
      echo "[sweep] kernel=$k tile=$t done rc=0"
    else
      rc=$?
      echo "[sweep] kernel=$k tile=$t done rc=$rc"
    fi
  done
done

echo "[sweep] complete"
