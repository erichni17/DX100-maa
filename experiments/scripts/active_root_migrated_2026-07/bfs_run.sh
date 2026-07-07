#!/bin/bash
# bfs_run.sh -- scaled-down real GAP BFS (MAA) through gem5 on the 17GB host.
# Toy graph (2^N), 4 cores, 1GB MAA region (bfs_maa built -DMAA_MEM_SIZE=0x40000000),
# checkpoint(AtomicSimpleCPU) -> restore(X86O3CPU + --maa). NOT artifact-scale; a plumbing
# proof that a real graph kernel drives the fixed MAA datapath end-to-end.
set -u
GH=/home/nier/DX100
GB=$GH/benchmarks/gapbs
N="${1:-16}"
OUT="$GH/bfs_out_n${N}"
BIN="$GB/bfs_maa"
GRAPH="$GB/serialized_graph_${N}.sg"
RAMCFG="$GH/ext/ramulator2/ramulator2/example_gem5_config.yaml"
OPTS="-f $GRAPH -l -n 1 -v"
export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

CKPT="$GH/ckpt_cache/bfs_maa_n${N}"
# ---- step 1: checkpoint (AtomicSimpleCPU) at m5_checkpoint() ----
if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
  echo "=== [1/2] checkpoint (AtomicSimpleCPU) -> $CKPT ==="
  rm -rf "$CKPT"; mkdir -p "$CKPT"
  OMP_PROC_BIND=false OMP_NUM_THREADS=4 timeout 900 "$GH/build/X86/gem5.opt" --outdir="$CKPT" \
    "$GH/configs/deprecated/example/se.py" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 1GB --max-checkpoints=1 \
    --cmd "$BIN" --options "$OPTS" > "$CKPT/ckpt.log" 2>&1
  if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
    echo "CHECKPOINT FAILED:"; tail -20 "$CKPT/ckpt.log"; exit 1
  fi
  echo "checkpoint: $(ls -d "$CKPT"/cpt.*)"
else
  echo "=== [1/2] reusing checkpoint $CKPT ==="
fi

# ---- step 2: restore + run ROI with MAA ----
echo "=== [2/2] restore+run X86O3CPU + --maa (4 cores, 1GB) ==="
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
  --maa_num_initial_row_table_slices 32 \
  --cmd "$BIN" --options "$OPTS" \
  > "$OUT/run.log" 2>&1
RC=$?
echo "gem5 exit=$RC"
echo "=== tail of run.log ==="; tail -15 "$OUT/run.log"
echo "=== ROI / correctness markers ==="
grep -iE "Trial Time|Average|Verif|correct|PASS|FAIL|exiting @|m5_exit|panic|fault" "$OUT/run.log" | head
echo "=== MAA activity (did the accelerator run?) ==="
[ -f "$OUT/stats.txt" ] && grep -E "system\.maa\.(cycles |cycles_INDRD|numInst|I0_IND_NumInsts)" "$OUT/stats.txt" | head || echo "no stats.txt"
echo "=== DONE_BFS ==="
