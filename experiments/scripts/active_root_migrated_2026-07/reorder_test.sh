#!/bin/bash
# reorder_test.sh -- does the MAA row-table reordering provide value here, or is it
# masked by Ramulator FRFCFS? Compare reorder ON (default) vs OFF (--maa_no_reorder)
# on the DRAM-bound allmiss gather. Same build, same checkpoint.
GH=/home/nier/DX100
RES="$GH/reorder_results.txt"; : > "$RES"
printf "%-14s %-13s %-13s %-11s %-9s %s\n" "config" "cycles_INDRD" "cycles" "MemLat" "RD_BW" "correct?" | tee "$RES"
run_one(){ # $1=tag  $2=extra flags
  bash "$GH/run_test.sh" ro_$1 MAA gather "allmiss 1 100 1 1" 20000 "$2" > "$GH/ro_$1.driver.log" 2>&1
  S="$GH/ro_$1/stats.txt"
  g(){ awk -v k="$1" '$1==k{v=$2} END{print v}' "$S" 2>/dev/null; }
  ci=$(g system.maa.cycles_INDRD); cy=$(g system.maa.cycles)
  ml=$(g system.maa.I0_IND_AvgLoadsMemAccessingLatency); bw=$(g system.maa.port_mem_RD_BW)
  ok=$(grep -q "all tests correct" "$GH/ro_$1.driver.log" && echo YES || echo "NO/FAIL")
  printf "%-14s %-13s %-13s %-11s %-9s %s\n" "$1" "${ci:-?}" "${cy:-?}" "${ml:-?}" "${bw:-?}" "$ok" | tee -a "$RES"
}
run_one reorder_ON  ""
run_one reorder_OFF "--maa_no_reorder"
echo "=== REORDER TEST DONE ===" | tee -a "$RES"
