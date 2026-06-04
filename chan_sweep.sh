#!/bin/bash
# chan_sweep.sh -- confirm the gather is DRAM-bandwidth-bound: scale memory channels
# (with the artifact's coupled ncbus_width & row-table slices) and check whether
# cycles_INDRD scales ~inversely. allmiss gather, reuses the cached checkpoint.
GH=/home/nier/DX100
RES="$GH/chan_results.txt"; : > "$RES"
printf "%-4s %-13s %-11s %-11s %s\n" "ch" "cycles_INDRD" "MemLat" "RD_BW" "correct?" | tee "$RES"
# ncbus_width: 32@2ch, 64@4ch, 128@8ch ; slices = ch*16
run_ch(){ local ch=$1 ncb=$2 sl=$3
  bash "$GH/run_test.sh" ch_$ch MAA gather "allmiss 1 100 1 1" 20000 \
    "--mem-channels $ch --maa_ncbus_width $ncb --maa_num_initial_row_table_slices $sl" \
    > "$GH/ch_$ch.driver.log" 2>&1
  S="$GH/ch_$ch/stats.txt"
  g(){ awk -v k="$1" '$1==k{v=$2} END{print v}' "$S" 2>/dev/null; }
  ci=$(g system.maa.cycles_INDRD); ml=$(g system.maa.I0_IND_AvgLoadsMemAccessingLatency); bw=$(g system.maa.port_mem_RD_BW)
  ok=$(grep -q "all tests correct" "$GH/ch_$ch.driver.log" && echo YES || echo "NO/FAIL")
  printf "%-4s %-13s %-11s %-11s %s\n" "$ch" "${ci:-?}" "${ml:-?}" "${bw:-?}" "$ok" | tee -a "$RES"
}
run_ch 2 32 32
run_ch 4 64 64
run_ch 8 128 128
echo "=== CHAN SWEEP DONE ===" | tee -a "$RES"
