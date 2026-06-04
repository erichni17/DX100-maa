#!/bin/bash
# sweep_rt.sh -- design-space sweep of num_row_table_entries_per_subslice_row on the
# allmiss (high row-locality) gather, to test the "row-table row too small -> re-activation"
# hypothesis. Reuses the cached allmiss checkpoint; varies only the MAA run parameter.
GH=/home/nier/DX100
RES="$GH/sweep_rt_results.txt"; : > "$RES"
printf "%-5s %-13s %-8s %-9s %-9s %-9s %-9s %s\n" \
  "EPR" "cycles_INDRD" "RTFull" "RowsIns" "UniqRows" "Reactiv" "MemLat" "correct?" | tee "$RES"
for EPR in 8 16 32 64; do
  bash "$GH/run_test.sh" sweep_rt_$EPR MAA gather "allmiss 1 100 1 1" 20000 \
    "--maa_num_row_table_entries_per_subslice_row $EPR" > "$GH/sweep_rt_$EPR.driver.log" 2>&1
  S="$GH/sweep_rt_$EPR/stats.txt"
  get(){ awk -v k="$1" '$1==k{v=$2} END{print v}' "$S" 2>/dev/null; }
  ci=$(get system.maa.cycles_INDRD)
  rf=$(get system.maa.I0_IND_NumRTFull)
  ri=$(get system.maa.I0_IND_NumRowsInserted)
  ur=$(get system.maa.I0_IND_NumUniqueRowsInserted)
  ml=$(get system.maa.I0_IND_AvgLoadsMemAccessingLatency)
  re=$(awk -v a="$ri" -v b="$ur" 'BEGIN{if(b>0)printf "%.2f",a/b; else print "-"}')
  ok=$(grep -q "all tests correct" "$GH/sweep_rt_$EPR.driver.log" && echo YES || echo "NO/FAIL")
  printf "%-5s %-13s %-8s %-9s %-9s %-9s %-9s %s\n" "$EPR" "$ci" "$rf" "$ri" "$ur" "$re" "$ml" "$ok" | tee -a "$RES"
done
echo "=== SWEEP DONE ===" | tee -a "$RES"
