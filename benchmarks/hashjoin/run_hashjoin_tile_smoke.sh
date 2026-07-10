#!/usr/bin/env bash
# run_hashjoin_tile_smoke.sh -- checkpoint->restore tile-size smoke for hashjoin.
# Usage:
#   run_hashjoin_tile_smoke.sh [gem5_binary] [kernel] [tile] [r_size] [s_size] [mem_size] [restore_timeout] [ckpt_timeout] [prog_interval]
# Example:
#   run_hashjoin_tile_smoke.sh gem5.opt.ovl_base PRH 16384 2000000 2000000 2GB
set -euo pipefail

GH=/data1/nier/DX100
HJ=$GH/benchmarks/hashjoin
SE=$GH/configs/deprecated/example/se.py
RAMCFG=$GH/ext/ramulator2/ramulator2/example_gem5_config.yaml

GBIN=${1:-gem5.opt.ovl_base}
KERNEL=${2:-PRH}
TILE=${3:-16384}
R_SIZE=${4:-2000000}
S_SIZE=${5:-2000000}
MEM_SIZE=${6:-2GB}
RESTORE_TIMEOUT=${7:-${RESTORE_TIMEOUT:-14400}}
CKPT_TIMEOUT=${8:-${CKPT_TIMEOUT:-3600}}
PROG_INTERVAL=${9:-${PROG_INTERVAL:-1000}}
OMP_THREADS=${OMP_THREADS:-4}

GEM5_BIN=$GH/build/X86/$GBIN
TAG=$(basename "$GBIN")
DATE_TAG=$(date +%Y-%m-%d)
CAMPAIGN_ROOT=$GH/experiments/campaigns/${DATE_TAG}_hashjoin_tile_smoke
RESULTS=$CAMPAIGN_ROOT/results.tsv

mkdir -p "$CAMPAIGN_ROOT"
export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

tile_suffix() {
  case "$1" in
    1024) echo "1K" ;;
    2048) echo "2K" ;;
    4096) echo "4K" ;;
    8192) echo "8K" ;;
    16384) echo "16K" ;;
    32768) echo "32K" ;;
    65536) echo "64K" ;;
    *)
      echo "unsupported tile size: $1" >&2
      return 1
      ;;
  esac
}

size_label() {
  local n="$1"
  if (( n % 1000000 == 0 )); then
    echo "$((n / 1000000))M"
  elif (( n % 1000 == 0 )); then
    echo "$((n / 1000))K"
  else
    echo "$n"
  fi
}

mem_size_to_hex() {
  case "$1" in
    2GB) echo "0x80000000" ;;
    4GB) echo "0x100000000" ;;
    8GB) echo "0x200000000" ;;
    16GB) echo "0x400000000" ;;
    *)
      echo "unsupported mem-size for MAA_MEM_SIZE define: $1" >&2
      return 1
      ;;
  esac
}

if [[ "$KERNEL" != "PRH" && "$KERNEL" != "PRO" ]]; then
  echo "unsupported kernel: $KERNEL (supported: PRH|PRO)" >&2
  exit 2
fi

SUF=$(tile_suffix "$TILE")
BIN_BASENAME="hj_maa_${SUF}"
BIN="$HJ/src/bin/x86/$BIN_BASENAME"
MAA_MEM_HEX=$(mem_size_to_hex "$MEM_SIZE")
MEM_TAG=$(echo "$MEM_SIZE" | tr -cd '[:alnum:]')

if [[ ! -f "$RESULTS" ]]; then
  echo -e "timestamp\tgem5_bin\tkernel\ttile\tr_size\ts_size\trc\tsimTicks\tmaa_cycles_total\toverlap_both_any\twrite_only_over_write\toutdir" > "$RESULTS"
fi

echo "[build] kernel=$KERNEL target=$BIN_BASENAME tile=$TILE mem=$MEM_SIZE maa_mem=$MAA_MEM_HEX"
echo "[run] omp_threads=$OMP_THREADS ckpt_timeout=${CKPT_TIMEOUT}s restore_timeout=${RESTORE_TIMEOUT}s prog_interval=$PROG_INTERVAL"
BUILD_STAMP="$CAMPAIGN_ROOT/.build_m${MEM_TAG}.done"
NEED_BUILD=0
if [[ ! -f "$BUILD_STAMP" ]]; then
  NEED_BUILD=1
fi
if [[ "${FORCE_REBUILD:-0}" != "0" ]]; then
  NEED_BUILD=1
fi
if [[ "$NEED_BUILD" == "1" ]]; then
  echo "[build] compiling hashjoin binaries for mem=$MEM_SIZE"
  (cd "$HJ" && MAA_MEM_SIZE="$MAA_MEM_HEX" bash ./compile_x86.sh GEM5 LOAD) > "$CAMPAIGN_ROOT/build_t${TILE}.log" 2>&1
  touch "$BUILD_STAMP"
else
  echo "[build] reusing campaign hashjoin binaries for mem=$MEM_SIZE"
fi

if [[ "$TILE" == "16384" && ! -f "$BIN" && -f "$HJ/src/bin/x86/hj_maa" ]]; then
  BIN="$HJ/src/bin/x86/hj_maa"
fi
[[ -f "$BIN" ]] || { echo "missing binary after build: $BIN" >&2; exit 3; }

R_LABEL=$(size_label "$R_SIZE")
S_LABEL=$(size_label "$S_SIZE")
R_FILE="relR_${R_LABEL}.dat"
S_FILE="relS_${S_LABEL}.dat"
for f in "$R_FILE" "$S_FILE"; do
  if [[ ! -f "$GH/$f" ]]; then
    if [[ -f "$HJ/$f" ]]; then
      cp -f "$HJ/$f" "$GH/$f"
    else
      echo "missing relation file: $f (expected in $GH or $HJ)" >&2
      exit 4
    fi
  fi
done

CKPT="$GH/ckpt_cache/hashjoin_${KERNEL}_r${R_SIZE}_s${S_SIZE}_t${TILE}_m${MEM_TAG}"
OUT="$CAMPAIGN_ROOT/${KERNEL}_r${R_SIZE}_s${S_SIZE}_t${TILE}_m${MEM_TAG}_${TAG}"
OPTS="-a $KERNEL -n 4 -r $R_SIZE -s $S_SIZE"

cd "$GH"

# --- step 1: checkpoint ---
if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
  echo "[ckpt] creating checkpoint in $CKPT"
  rm -rf "$CKPT"
  mkdir -p "$CKPT"
  OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" timeout "$CKPT_TIMEOUT" "$GEM5_BIN" --outdir="$CKPT" "$SE" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size "$MEM_SIZE" --max-checkpoints=1 \
    --cmd "$BIN" --options "$OPTS" > "$CKPT/ckpt.log" 2>&1
  echo "[ckpt] done (exit=$?)"
else
  echo "[ckpt] reusing $CKPT"
fi

ls "$CKPT"/cpt.* >/dev/null 2>&1 || { echo "checkpoint missing for $KERNEL t$TILE r$R_SIZE s$S_SIZE" >&2; exit 5; }

# --- step 2: restore ---
rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$CKPT"/cpt.* "$OUT"/
echo "[restore] running $KERNEL tile=$TILE r_size=$R_SIZE s_size=$S_SIZE"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" timeout "$RESTORE_TIMEOUT" "$GEM5_BIN" --outdir="$OUT" "$SE" \
  --cpu-type X86O3CPU -r 1 -n 4 --mem-size "$MEM_SIZE" \
  --sys-clock 3.2GHz --cpu-clock 3.2GHz \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
  --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
  --l2cache --l2_size=256kB --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
  --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports 4 --cacheline_size=64 \
  --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2 --maa_ncbus_width 32 \
  --maa --maa_num_maas 1 --maa_num_tile_elements "$TILE" --maa_l2_uncacheable --maa_l3_uncacheable \
  --maa_num_initial_row_table_slices 32 \
  --cmd "$BIN" --options "$OPTS" --prog-interval="$PROG_INTERVAL" > "$OUT/run.log" 2>&1
RC=$?
set -e
echo "[restore] done (exit=$RC)"

STATS="$OUT/stats.txt"
SIMTICKS=$(awk '$1=="simTicks"{print $2}' "$STATS" 2>/dev/null | tail -1 || true)
MAA_CYCLES=$(awk '$1=="system.maa.cycles_TOTAL"{print $2}' "$STATS" 2>/dev/null | tail -1 || true)
OVERLAP=$(grep 'OVERLAP_AUDIT' "$OUT/run.log" 2>/dev/null | tail -1 | sed -n 's/.*both\/any=\([0-9.]*\).*/\1/p' || true)
WRTAIL=$(grep 'WRITE_TAIL_AUDIT' "$OUT/run.log" 2>/dev/null | tail -1 | sed -n 's/.*write_only\/write=\([0-9.]*\).*/\1/p' || true)
TS=$(date +%Y-%m-%dT%H:%M:%S)

echo -e "${TS}\t${GBIN}\t${KERNEL}\t${TILE}\t${R_SIZE}\t${S_SIZE}\t${RC}\t${SIMTICKS:-}\t${MAA_CYCLES:-}\t${OVERLAP:-}\t${WRTAIL:-}\t${OUT}" >> "$RESULTS"

echo "===== results ($KERNEL, tile=$TILE, r_size=$R_SIZE, s_size=$S_SIZE) ====="
grep -E "Result|result|MATCH|match|Time|time|m5_exit|panic|fatal|ERROR|Error" "$OUT/run.log" | tail -30 || true
grep -E "OVERLAP_AUDIT|WRITE_TAIL_AUDIT" "$OUT/run.log" | tail -8 || true
echo "[results] appended to $RESULTS"

exit "$RC"
