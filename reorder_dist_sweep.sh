#!/bin/bash
# reorder_dist_sweep.sh -- T-B (gather half): does row-table reordering's value depend on the
# index distribution's natural DRAM row-buffer locality? The prior reorder A/B used
# `allmiss 1 100 1 1` = 100% row-buffer HIT -> reordering had nothing to recover, so it looked
# useless. Here we sweep row-buffer-hit% (ROH) from 100 -> 50 -> 0 on both reorder arms.
#   allmiss BAH ROH CHH BGH  (hit %):  1 100 1 1 = all-hit (tame);  0 ROH 0 0 = scattered.
# HYPOTHESIS: reorder ON should pull ahead of OFF as ROH drops (more locality to recover).
# Same n, same MAA config; reuses run_test.sh's per-dist cached checkpoints.
set -u
GH=/home/nier/DX100
N="${1:-20000}"
RES="$GH/reorder_dist_results.txt"; : > "$RES"
printf "%-22s %-7s %-13s %-13s %-11s %-9s %s\n" \
       "dist" "reorder" "cycles_INDRD" "cycles" "MemLat" "RD_BW" "correct?" | tee "$RES"

run_one(){ # $1=label  $2="<dist args>"  $3=reorder_tag(ON|OFF)  $4=extra flags
  local label="$1"; local dist="$2"; local ro="$3"; local extra="$4"
  local tag="${label}_${ro}"
  bash "$GH/run_test.sh" "rd_${tag}" MAA gather "$dist" "$N" "$extra" \
       > "$GH/rd_${tag}.driver.log" 2>&1
  local S="$GH/rd_${tag}/stats.txt"
  g(){ awk -v k="$1" '$1==k{v=$2} END{print v}' "$S" 2>/dev/null; }
  local ci=$(g system.maa.cycles_INDRD); local cy=$(g system.maa.cycles)
  local ml=$(g system.maa.I0_IND_AvgLoadsMemAccessingLatency); local bw=$(g system.maa.port_mem_RD_BW)
  local ok=$(grep -q "all tests correct" "$GH/rd_${tag}.driver.log" && echo YES || echo "NO/FAIL")
  printf "%-22s %-7s %-13s %-13s %-11s %-9s %s\n" \
         "$dist" "$ro" "${ci:-?}" "${cy:-?}" "${ml:-?}" "${bw:-?}" "$ok" | tee -a "$RES"
}

# tame baseline (100% row-buffer hit), medium spread (50%), max spread (0% -> fully scattered)
for spec in "allhit100:allmiss 1 100 1 1" "spread50:allmiss 0 50 0 0" "scattered0:allmiss 0 0 0 0"; do
  label="${spec%%:*}"; dist="${spec#*:}"
  run_one "$label" "$dist" ON  ""
  run_one "$label" "$dist" OFF "--maa_no_reorder"
done
echo "=== REORDER_DIST_SWEEP DONE ===" | tee -a "$RES"
