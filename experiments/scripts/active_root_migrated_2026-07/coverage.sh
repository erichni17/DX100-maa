#!/bin/bash
# coverage.sh -- exercise MAA instruction types beyond gather (scatter=INDIR_ST,
# rmw=INDIR_RMW) on the current build, to validate the codebase (esp. the O(1)
# RequestTable) across op types and surface any latent bugs.
GH=/home/nier/DX100
RES="$GH/coverage_results.txt"; : > "$RES"
printf "%-10s %-8s %-13s %-12s %s\n" "kernel" "exit" "maa.cycles" "maa.numInst" "correct?" | tee "$RES"
for K in scatter rmw; do
  bash "$GH/run_test.sh" cov_$K MAA "$K" allhit 20000 > "$GH/cov_$K.driver.log" 2>&1
  rc=$(grep -oE "gem5 exit=[0-9]+" "$GH/cov_$K.driver.log" | grep -oE "[0-9]+$" | tail -1)
  S="$GH/cov_$K/stats.txt"
  cyc=$(awk '$1=="system.maa.cycles"{v=$2} END{print v}' "$S" 2>/dev/null)
  ni=$(awk '$1=="system.maa.numInst"{v=$2} END{print v}' "$S" 2>/dev/null)
  ok=$(grep -q "all tests correct" "$GH/cov_$K.driver.log" && echo YES || echo "NO/FAIL")
  printf "%-10s %-8s %-13s %-12s %s\n" "$K" "${rc:-?}" "${cyc:-?}" "${ni:-?}" "$ok" | tee -a "$RES"
  # keep a baseline copy if it passed
  [ "$ok" = "YES" ] && cp "$S" "$GH/baselines/MAA_${K}_allhit_20000.stats.txt" 2>/dev/null
done
echo "=== COVERAGE DONE ===" | tee -a "$RES"
