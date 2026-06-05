#!/bin/bash
# latbound.sh -- The definitive "where does the MAA row table matter?" test.
# The n-sweep proved the MAA stays high-MLP (~55-76) even at n=200, so shrinking the
# problem never makes it latency-bound. The only way to force latency-bound is to choke
# the MEMORY side: shrink the FRFCFS controller queue (its reorder window AND its MLP).
# At queue_size=1 the controller cannot reorder at all -> if the MAA row-table reordering
# is ever useful, ON must beat OFF here. Small n keeps queue=1 (serialized) runs fast.
GH=/home/nier/DX100
RC=$GH/ext/ramulator2/ramulator2
RES="$GH/latbound_results.txt"; : > "$RES"
# Self-contained: generate the shallow-queue Ramulator configs from the base config
# (these are gitignored scratch; regenerated on demand so the harness runs from a clean tree).
for Q in 1 2 4; do
  sed "s/queue_size: 32/queue_size: $Q/" "$RC/example_gem5_config.yaml" > "$RC/example_gem5_config_q${Q}.yaml"
done
printf "%-6s %-6s %-9s %-13s %-10s %s\n" "n" "queue" "reorder" "cycles_INDRD" "MemLat" "correct?" | tee "$RES"
cell(){ local n=$1 q=$2 ro=$3 noreorder=$4
  local tag=n${n}_q${q}_${ro}
  timeout 900 bash "$GH/run_test.sh" "$tag" MAA gather "allmiss 1 100 1 1" $n \
    "--ramulator-config $RC/example_gem5_config_q${q}.yaml $noreorder" > "$GH/$tag.driver.log" 2>&1
  local S="$GH/$tag/stats.txt"
  local ci=$(awk '$1=="system.maa.cycles_INDRD"{v=$2} END{print v}' "$S" 2>/dev/null)
  local ml=$(awk '$1=="system.maa.I0_IND_AvgLoadsMemAccessingLatency"{v=$2} END{print v}' "$S" 2>/dev/null)
  local ok=$(grep -q "all tests correct" "$GH/$tag.driver.log" && echo YES || echo "NO/FAIL")
  printf "%-6s %-6s %-9s %-13s %-10s %s\n" "$n" "$q" "$ro" "${ci:-?}" "${ml:-?}" "$ok" | tee -a "$RES"
}
for N in 200 1000; do
  for Q in 1 2 4; do
    cell $N $Q ON  ""
    cell $N $Q OFF "--maa_no_reorder"
  done
done
echo "=== LATBOUND DONE ===" | tee -a "$RES"
