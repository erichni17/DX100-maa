#!/bin/bash
# queue_sweep.sh -- WHERE does the MAA row table matter? Vary the Ramulator FRFCFS
# controller queue_size (its reorder window) x MAA reorder ON/OFF, allmiss gather n=20000.
# Hypothesis: a smaller controller queue can't reorder for row hits, so the MAA row-table
# reordering should provide value (ON < OFF); at the default 32 it's masked (ON ~ OFF).
# (queue_size=1 is pathological/serialized -> excluded; per-cell timeout guards slow cells.)
GH=/home/nier/DX100
RC=$GH/ext/ramulator2/ramulator2
RES="$GH/queue_results.txt"; : > "$RES"
CAP=720   # per-cell wall-clock cap (s)
printf "%-6s %-9s %-13s %-10s %s\n" "queue" "reorder" "cycles_INDRD" "MemLat" "correct?" | tee "$RES"
cell(){ local q=$1 ro=$2 extra=$3
  timeout $CAP bash "$GH/run_test.sh" q${q}_${ro} MAA gather "allmiss 1 100 1 1" 20000 \
    "--ramulator-config $RC/example_gem5_config_q${q}.yaml $extra" > "$GH/q${q}_${ro}.driver.log" 2>&1
  local rc=$?
  if [ $rc -eq 124 ]; then pkill -9 -f "outdir=$GH/q${q}_${ro}" 2>/dev/null
    printf "%-6s %-9s %-13s %-10s %s\n" "$q" "$ro" "TIMEOUT" "-" "(>${CAP}s)" | tee -a "$RES"; return; fi
  local S="$GH/q${q}_${ro}/stats.txt"
  g(){ awk -v k="$1" '$1==k{v=$2} END{print v}' "$S" 2>/dev/null; }
  local ci=$(g system.maa.cycles_INDRD); local ml=$(g system.maa.I0_IND_AvgLoadsMemAccessingLatency)
  local ok=$(grep -q "all tests correct" "$GH/q${q}_${ro}.driver.log" && echo YES || echo "NO/FAIL")
  printf "%-6s %-9s %-13s %-10s %s\n" "$q" "$ro" "${ci:-?}" "${ml:-?}" "$ok" | tee -a "$RES"
}
for Q in 32 16 8; do      # fast -> slow
  cell $Q ON  ""
  cell $Q OFF "--maa_no_reorder"
done
echo "=== QUEUE SWEEP DONE ===" | tee -a "$RES"
