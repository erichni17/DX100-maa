#!/usr/bin/env bash
set -euo pipefail
GH=/data1/nier/DX100
OUT=${1:-$GH/experiments/campaigns/2026-07-12_small_tile_meeting_status.md}
TMP=$OUT.tmp
now=$(date '+%F %T %Z')
{
  echo '# Small-Tile Sweep Status'
  echo
  echo "Updated: $now"
  echo
  echo '## Persistent Sessions'
  echo
  echo '| Session | State | Latest tick | Terminal marker |'
  echo '|---|---:|---:|---|'
  for s in \
    dx100_sssp_1k_np_0712 dx100_sssp_2k_0712 \
    dx100_cg_1k_0712 dx100_cg_2k_0712 dx100_cg_4k_0712 dx100_cg_8k_0712 dx100_cg_16k_0712 \
    dx100_bc_1k_0712 dx100_bc_2k_0712 dx100_bc_4k_0712 dx100_bc_8k_0712 dx100_bc_16k_0712 \
    dx100_xrage_1k_0712 dx100_xrage_2k_0712 dx100_xrage_4k_0712 dx100_xrage_8k_0712 dx100_xrage_16k_0712; do
    if tmux has-session -t "=$s" 2>/dev/null; then state=running; else state=ended; fi
    case "$s" in
      dx100_sssp_1k*) log=$GH/experiments/campaigns/2026-07-12_gapbs_sssp_1k_noprefetch/sssp_s22_t1024_m2GB_gem5.opt.ovl_base/run.log ;;
      dx100_sssp_2k*) log=$GH/experiments/campaigns/2026-07-12_gapbs_sssp_2k_retry/sssp_s22_t2048_m2GB_gem5.opt.ovl_base/run.log ;;
      dx100_cg_*) tile=${s#dx100_cg_}; tile=${tile%%_*}; case $tile in 1k) t=1024;;2k)t=2048;;4k)t=4096;;8k)t=8192;;16k)t=16384;;esac; log=$GH/experiments/campaigns/2026-07-12_cg_tile_smoke/cg_t${t}_m2GB_gem5.opt.ovl_base/run.log ;;
      dx100_bc_*) tile=${s#dx100_bc_}; tile=${tile%%_*}; case $tile in 1k) t=1024;;2k)t=2048;;4k)t=4096;;8k)t=8192;;16k)t=16384;;esac; log=$GH/experiments/campaigns/2026-07-12_gapbs_bc_tile_smoke/bc_s22_t${t}_m2GB_gem5.opt.ovl_base/run.log ;;
      dx100_xrage_*) tile=${s#dx100_xrage_}; tile=${tile%%_*}; case $tile in 1k) t=1024;;2k)t=2048;;4k)t=4096;;8k)t=8192;;16k)t=16384;;esac; log=$GH/experiments/campaigns/2026-07-12_xrage_tile_smoke/xrage_t${t}_m2GB_gem5.opt.ovl_base/run.log ;;
    esac
    tick=$(awk -F: '/^[0-9]+: Event_/ {v=$1} END {print v}' "$log" 2>/dev/null || true)
    marker=$(rg 'panic|fatal|Error:|Exiting @|ROI End' "$log" 2>/dev/null | tail -1 | tr '|' '/' || true)
    echo "| $s | $state | ${tick:-not in ROI} | ${marker:-none} |"
  done
  echo
  echo '## Completed Results'
  echo
  for f in \
    $GH/experiments/campaigns/2026-07-12_xrage_tile_smoke/results.tsv \
    $GH/experiments/campaigns/2026-07-12_cg_tile_smoke/results.tsv \
    $GH/experiments/campaigns/2026-07-12_gapbs_bc_tile_smoke/results.tsv \
    $GH/experiments/campaigns/2026-07-12_gapbs_sssp_1k_noprefetch/results.tsv \
    $GH/experiments/campaigns/2026-07-12_gapbs_sssp_2k_retry/results.tsv; do
    echo "### ${f#$GH/experiments/campaigns/}"
    echo '```tsv'
    cat "$f" 2>/dev/null || echo 'not created'
    echo '```'
  done
  echo
  echo '## Host Health'
  echo '```text'
  free -h
  vmstat 1 2 | tail -1
  echo '```'
} > "$TMP"
mv "$TMP" "$OUT"
