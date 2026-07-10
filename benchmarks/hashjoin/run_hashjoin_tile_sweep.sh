#!/usr/bin/env bash
# run_hashjoin_tile_sweep.sh -- multi-kernel hashjoin tile sweep (checkpoint+restore).
# Usage:
#   run_hashjoin_tile_sweep.sh [gem5_binary] [tiles] [kernels] [r_size] [s_size] [mem_size] [restore_timeout] [ckpt_timeout] [prog_interval]
# Example:
#   run_hashjoin_tile_sweep.sh gem5.opt.ovl_base "1024 2048 4096 8192 16384 32768 65536" "PRH PRO" 2000000 2000000 2GB
set -euo pipefail

GH=/data1/nier/DX100
RUNNER=$GH/benchmarks/hashjoin/run_hashjoin_tile_smoke.sh

GBIN=${1:-gem5.opt.ovl_base}
TILES=${2:-"1024 2048 4096 8192 16384 32768 65536"}
KERNELS=${3:-"PRH PRO"}
R_SIZE=${4:-2000000}
S_SIZE=${5:-2000000}
MEM_SIZE=${6:-2GB}
RESTORE_TIMEOUT=${7:-${RESTORE_TIMEOUT:-14400}}
CKPT_TIMEOUT=${8:-${CKPT_TIMEOUT:-3600}}
PROG_INTERVAL=${9:-${PROG_INTERVAL:-1000}}

echo "[sweep] gem5=$GBIN tiles=[$TILES] kernels=[$KERNELS] r_size=$R_SIZE s_size=$S_SIZE mem=$MEM_SIZE restore_timeout=${RESTORE_TIMEOUT}s ckpt_timeout=${CKPT_TIMEOUT}s prog_interval=$PROG_INTERVAL"

for k in $KERNELS; do
  for t in $TILES; do
    echo "[sweep] kernel=$k tile=$t start"
    if "$RUNNER" "$GBIN" "$k" "$t" "$R_SIZE" "$S_SIZE" "$MEM_SIZE" "$RESTORE_TIMEOUT" "$CKPT_TIMEOUT" "$PROG_INTERVAL"; then
      echo "[sweep] kernel=$k tile=$t done rc=0"
    else
      rc=$?
      echo "[sweep] kernel=$k tile=$t done rc=$rc"
    fi
  done
done

echo "[sweep] complete"
