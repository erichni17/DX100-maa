#!/bin/bash
# run_is_smoke.sh -- checkpoint->restore smoke for NAS IS MAA (is_maa_16K).
# Usage: run_is_smoke.sh [gem5_binary]   (default: gem5.opt.ovl_base)
# Produces OVERLAP_AUDIT + WRITE_TAIL_AUDIT lines at ROI end.
set -u
GH=/data1/nier/DX100
TBIN=$GH/benchmarks/NAS/is/is_maa_16K
GBIN=${1:-gem5.opt.ovl_base}
G=$GH/build/X86/$GBIN
RAMCFG=/data1/nier/DX100/ext/ramulator2/ramulator2/example_gem5_config.yaml
export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"
SE=$GH/configs/deprecated/example/se.py
C=$GH/ckpt_cache/is_maa_smoke
TAG=$(basename "$GBIN")
O=$GH/benchmarks/NAS/is/is_smoke_run_${TAG}

# --- step 1: checkpoint (AtomicSimpleCPU) if not present ---
if ! ls "$C"/cpt.* >/dev/null 2>&1; then
  rm -rf "$C"; mkdir -p "$C"
  echo "[ckpt] creating checkpoint in $C ..."
  timeout 900 "$G" --outdir="$C" "$SE" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 16GB --max-checkpoints=1 \
    --cmd "$TBIN" --options "MAA" > "$C/ckpt.log" 2>&1
  echo "[ckpt] done (exit $?)"
fi

# --- step 2: restore (X86O3CPU + caches + Ramulator2 + MAA) ---
rm -rf "$O"; mkdir -p "$O"; cp -r "$C"/cpt.* "$O"/
echo "[restore] running $GBIN ..."
OMP_PROC_BIND=false OMP_NUM_THREADS=4 timeout 1800 "$G" --outdir="$O" "$SE" \
  --cpu-type X86O3CPU -r 1 -n 4 --mem-size 16GB \
  --sys-clock 3.2GHz --cpu-clock 3.2GHz \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
  --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
  --l2cache --l2_size=256kB --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
  --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports 4 --cacheline_size=64 \
  --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2 --maa_ncbus_width 32 \
  --maa --maa_num_maas 1 --maa_num_tile_elements 16384 --maa_l2_uncacheable --maa_l3_uncacheable \
  --maa_num_initial_row_table_slices 32 \
  --cmd "$TBIN" --options "MAA" --prog-interval=1000 > "$O/run.log" 2>&1
echo "[restore] done (exit $?)"
echo "===== results ($GBIN) ====="
grep -E "ROI End|successfull|iteration:" "$O/run.log"
grep -E "OVERLAP_AUDIT|WRITE_TAIL_AUDIT" "$O/run.log"
