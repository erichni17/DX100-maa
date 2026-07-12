#!/usr/bin/env bash
set -euo pipefail
GH=/data1/nier/DX100
GBIN=${1:-gem5.opt.ovl_base}
TILES=${2:-"1024 2048 4096 8192 16384"}
MEM_SIZE=${3:-2GB}
RESTORE_TIMEOUT=${4:-${RESTORE_TIMEOUT:-43200}}
CKPT_TIMEOUT=${5:-${CKPT_TIMEOUT:-7200}}
PROG_INTERVAL=${6:-${PROG_INTERVAL:-1000}}
for tile in $TILES; do
  "$GH/benchmarks/spatter/run_xrage_tile_smoke.sh" "$GBIN" "$tile" "$MEM_SIZE" \
    "$RESTORE_TIMEOUT" "$CKPT_TIMEOUT" "$PROG_INTERVAL" || echo "XRAGE tile=$tile failed rc=$?"
done
