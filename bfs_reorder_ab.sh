#!/bin/bash
# bfs_reorder_ab.sh -- T-B reorder ON vs OFF A/B on scaled-down GAP BFS (real graph kernel).
# Reuses the reorder-INDEPENDENT AtomicSimpleCPU checkpoint (no MAA at ckpt time), then runs
# the ROI twice on X86O3CPU+--maa: ON (default) and OFF (--maa_no_reorder). Sequential to fit
# the ~17GB host (~5GB RSS each). Derives MLP (Little's law) to test latency- vs bw-bound first.
set -u
GH=/home/nier/DX100
GB=$GH/benchmarks/gapbs
N="${1:-16}"
BIN="$GB/bfs_maa"
GRAPH="$GB/serialized_graph_${N}.sg"
RAMCFG="$GH/ext/ramulator2/ramulator2/example_gem5_config.yaml"
OPTS="-f $GRAPH -l -n 1 -v"
export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"
CKPT="$GH/ckpt_cache/bfs_maa_n${N}"

if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
  echo "NO checkpoint at $CKPT -- run bfs_run.sh $N first to build it."; exit 1
fi

run_one(){  # $1=tag  $2=extra gem5 flags
  local tag="$1"; local extra="$2"; local OUT="$GH/bfs_ab_n${N}_${tag}"
  echo "=== [$tag] restore+run (extra flags: '${extra:-none}') -> $OUT ==="
  rm -rf "$OUT"; mkdir -p "$OUT"; cp -r "$CKPT"/cpt.* "$OUT"/
  OMP_PROC_BIND=false OMP_NUM_THREADS=4 timeout 2400 "$GH/build/X86/gem5.opt" --outdir="$OUT" \
    "$GH/configs/deprecated/example/se.py" \
    --cpu-type X86O3CPU -r 1 -n 4 --mem-size 1GB --sys-clock 3.2GHz --cpu-clock 3.2GHz \
    --caches --l1d_size=32kB --l1d_assoc=8 --l1d_mshrs=16 --l1d_write_buffers=8 \
    --l1i_size=32kB --l1i_assoc=8 --l1i_mshrs=16 --l1i_write_buffers=8 \
    --l2cache --l2_size=256kB --l2_assoc=4 --l2_mshrs=32 --l2_write_buffers=16 \
    --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports 4 \
    --cacheline_size=64 \
    --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2 --maa_ncbus_width 32 \
    --maa --maa_num_maas 1 --maa_num_tile_elements 16384 --maa_l2_uncacheable --maa_l3_uncacheable \
    --maa_num_initial_row_table_slices 32 $extra \
    --cmd "$BIN" --options "$OPTS" \
    > "$OUT/run.log" 2>&1
  echo "  gem5 exit=$? ; $(grep -icE 'maa' "$OUT/stats.txt" 2>/dev/null) maa stats"
}

run_one ON  ""
run_one OFF "--maa_no_reorder"

# ---- extract + compare ----
g(){ awk -v k="$1" '$1==k{v=$2} END{print (v==""?"-":v)}' "$2" 2>/dev/null; }
corr(){ grep -iqE "successful|Verification.*PASS|all tests correct" "$1" && echo PASS \
        || { grep -iqE "FAIL|panic|fault|not successful" "$1" && echo FAIL || echo "?"; } ; }
echo; echo "================= BFS reorder A/B (n=2^${N}) ================="
printf "%-26s %14s %14s\n" "metric" "ON(reorder)" "OFF(noreorder)"
for m in numInst_INDRD cycles_INDRD cycles cycles_BUSY cycles_TOTAL \
         I0_IND_NumInsts I0_IND_LoadsMemAccessing I0_IND_LoadsMemAccessingLatency \
         I0_IND_AvgLoadsMemAccessingLatency I0_IND_CyclesFill I0_IND_CyclesBuild \
         I0_IND_CyclesRequest I0_IND_NumRowsInserted I0_IND_NumUniqueRowsInserted \
         I0_IND_AvgRowsPerInst port_mem_RD_BW; do
  on=$(g "system.maa.$m" "$GH/bfs_ab_n${N}_ON/stats.txt")
  off=$(g "system.maa.$m" "$GH/bfs_ab_n${N}_OFF/stats.txt")
  printf "%-26s %14s %14s\n" "$m" "$on" "$off"
done
# end-to-end ROI ticks + derived MLP = total mem-load-latency / request-window cycles
for tag in ON OFF; do
  S="$GH/bfs_ab_n${N}_${tag}/stats.txt"; L="$GH/bfs_ab_n${N}_${tag}/run.log"
  st=$(g simTicks "$S"); lat=$(g system.maa.I0_IND_LoadsMemAccessingLatency "$S")
  req=$(g system.maa.I0_IND_CyclesRequest "$S"); cir=$(g system.maa.cycles_INDRD "$S")
  mlp_req=$(awk -v a="$lat" -v b="$req" 'BEGIN{print (b>0)?a/b:0}')
  mlp_ind=$(awk -v a="$lat" -v b="$cir" 'BEGIN{print (b>0)?a/b:0}')
  printf "[%s] simTicks=%s  MLP(/req)=%.1f  MLP(/INDRD)=%.1f  correctness=%s\n" \
         "$tag" "$st" "$mlp_req" "$mlp_ind" "$(corr "$L")"
done
echo "=== DONE_BFS_AB ==="
