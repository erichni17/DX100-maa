#!/usr/bin/env bash
# run_is_tile_sweep.sh -- launch NAS-IS tile sweep via run_is_smoke.sh.
# Usage:
#   run_is_tile_sweep.sh [gem5_binary] [small_class] [tile_list] [restore_timeout] [ckpt_timeout] [prog_interval]
#
# Examples:
#   run_is_tile_sweep.sh
#   run_is_tile_sweep.sh gem5.opt.ovl_base 1 "4096 8192 16384 32768"
#   run_is_tile_sweep.sh gem5.opt.ovl_base 0 "16384 32768 65536"
set -euo pipefail

GH=/data1/nier/DX100
GBIN=${1:-gem5.opt.ovl_base}
SMALL=${2:-1}
TILES=${3:-"4096 8192 16384 32768"}
RESTORE_TIMEOUT=${4:-${RESTORE_TIMEOUT:-1800}}
CKPT_TIMEOUT=${5:-${CKPT_TIMEOUT:-900}}
PROG_INTERVAL=${6:-${PROG_INTERVAL:-1000}}
RUNNER=$GH/benchmarks/NAS/is/run_is_smoke.sh

echo "[sweep] gem5=$GBIN small=$SMALL tiles=[$TILES] restore_timeout=${RESTORE_TIMEOUT}s ckpt_timeout=${CKPT_TIMEOUT}s prog_interval=$PROG_INTERVAL"
for t in $TILES; do
  echo "[sweep] tile=$t start"
  if "$RUNNER" "$GBIN" "$t" "$SMALL" "$RESTORE_TIMEOUT" "$CKPT_TIMEOUT" "$PROG_INTERVAL"; then
    echo "[sweep] tile=$t done rc=0"
  else
    rc=$?
    echo "[sweep] tile=$t done rc=$rc"
  fi
done

echo "[sweep] complete"
