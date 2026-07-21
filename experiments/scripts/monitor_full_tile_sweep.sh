#!/usr/bin/env bash
set -euo pipefail

STATE_ROOT=${1:?state root required}
RUN_ROOT=${2:?run root required}
WORKFLOW=${3:-dx100-full-tile-sweep-20260720}

while true; do
  printf '\033[2J\033[H'
  date
  echo
  dx-runtime --state-root "$STATE_ROOT" workflow status "$WORKFLOW" || true
  echo
  free -h
  echo
  ps -eo pid,stat,etime,%cpu,rss,cmd | \
    awk -v root="$RUN_ROOT" 'NR == 1 || /gem5.opt.ovl_base/ || index($0, root)'
  sleep 30
done
