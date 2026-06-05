#!/bin/bash
# nsweep.sh -- Is there a LATENCY-BOUND gather regime where the MAA row table matters?
# At large n the gather is bandwidth-bound and FRFCFS masks the row table (reorder ON~OFF).
# At small n, fewer requests are in flight; if MLP drops below the controller reorder window,
# the controller can't find row hits on its own but the MAA row table still can -> ON < OFF.
# Sweep n x reorder{ON,OFF}, allmiss gather. Report cycles_INDRD, MemLat, and OFF/ON ratio.
GH=/home/nier/DX100
RES="$GH/nsweep_results.txt"; : > "$RES"
printf "%-7s %-9s %-13s %-10s %s\n" "n" "reorder" "cycles_INDRD" "MemLat" "correct?" | tee "$RES"
g(){ awk -v k="$1" -v f="$2" '$1==k{v=$2} END{print v}' "$f" 2>/dev/null; }
cell(){ local n=$1 ro=$2 extra=$3
  timeout 900 bash "$GH/run_test.sh" n${n}_${ro} MAA gather "allmiss 1 100 1 1" $n \
    "$extra" > "$GH/n${n}_${ro}.driver.log" 2>&1
  local S="$GH/n${n}_${ro}/stats.txt"
  local ci=$(g system.maa.cycles_INDRD "$S")
  local ml=$(g system.maa.I0_IND_AvgLoadsMemAccessingLatency "$S")
  local ok=$(grep -q "all tests correct" "$GH/n${n}_${ro}.driver.log" && echo YES || echo "NO/FAIL")
  printf "%-7s %-9s %-13s %-10s %s\n" "$n" "$ro" "${ci:-?}" "${ml:-?}" "$ok" | tee -a "$RES"
}
for N in 200 1000 4000 20000; do
  cell $N ON  ""
  cell $N OFF "--maa_no_reorder"
done
echo "=== N SWEEP DONE ===" | tee -a "$RES"
