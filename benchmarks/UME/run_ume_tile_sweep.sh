#!/usr/bin/env bash
# run_ume_tile_sweep.sh -- multi-kernel UME tile sweep (checkpoint+restore).
# Usage:
#   run_ume_tile_sweep.sh [gem5_binary] [tiles] [kernels] [n] [mem_size] [restore_timeout] [ckpt_timeout] [prog_interval]
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
RESTORE_TIMEOUT=${6:-${RESTORE_TIMEOUT:-14400}}
CKPT_TIMEOUT=${7:-${CKPT_TIMEOUT:-3600}}
PROG_INTERVAL=${8:-${PROG_INTERVAL:-1000}}

echo "[sweep] gem5=$GBIN tiles=[$TILES] kernels=[$KERNELS] n=$N mem=$MEM_SIZE restore_timeout=${RESTORE_TIMEOUT}s ckpt_timeout=${CKPT_TIMEOUT}s prog_interval=$PROG_INTERVAL"

for k in $KERNELS; do
  for t in $TILES; do
    echo "[sweep] kernel=$k tile=$t start"
    if "$RUNNER" "$GBIN" "$k" "$t" "$N" "$MEM_SIZE" "$RESTORE_TIMEOUT" "$CKPT_TIMEOUT" "$PROG_INTERVAL"; then
      echo "[sweep] kernel=$k tile=$t done rc=0"
    else
      rc=$?
      echo "[sweep] kernel=$k tile=$t done rc=$rc"
    fi
  done
done

echo "[sweep] complete"
