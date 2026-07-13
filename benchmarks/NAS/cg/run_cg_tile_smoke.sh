#!/usr/bin/env bash
# Checkpoint->restore smoke for the shortened NAS CG MAA kernel.
# Usage: run_cg_tile_smoke.sh [gem5_bin] [tile] [mem_size] [restore_timeout] [ckpt_timeout] [prog_interval]
set -euo pipefail

GH=/data1/nier/DX100
CG=$GH/benchmarks/NAS/cg
SE=$GH/configs/deprecated/example/se.py
RAMCFG=$GH/ext/ramulator2/ramulator2/example_gem5_config.yaml
GBIN=${1:-gem5.opt.ovl_base}
TILE=${2:-16384}
MEM_SIZE=${3:-2GB}
RESTORE_TIMEOUT=${4:-${RESTORE_TIMEOUT:-21600}}
CKPT_TIMEOUT=${5:-${CKPT_TIMEOUT:-21600}}
PROG_INTERVAL=${6:-${PROG_INTERVAL:-1000}}
OMP_THREADS=${OMP_THREADS:-4}
BUILD_LOCK=${BUILD_LOCK:-$CG/.build.lock}
GEM5_BIN=$GH/build/X86/$GBIN
TAG=$(basename "$GBIN")
DATE_TAG=$(date +%Y-%m-%d)
CAMPAIGN_ROOT=${CAMPAIGN_ROOT:-$GH/experiments/campaigns/${DATE_TAG}_cg_tile_smoke}
RESULTS=$CAMPAIGN_ROOT/results.tsv

mkdir -p "$CAMPAIGN_ROOT"

# Bash reads scripts incrementally. Use a frozen copy so later edits cannot corrupt a live run.
if [[ "${CG_FROZEN_RUNNER:-0}" != 1 ]]; then
  snapshot="$CAMPAIGN_ROOT/runner_$(date +%Y%m%d_%H%M%S)_$$.sh"
  cp -- "${BASH_SOURCE[0]}" "$snapshot"
  chmod +x "$snapshot"
  exec env CG_FROZEN_RUNNER=1 CAMPAIGN_ROOT="$CAMPAIGN_ROOT" "$snapshot" "$@"
fi

export LD_LIBRARY_PATH="$GH/ext/ramulator2/ramulator2:${LD_LIBRARY_PATH:-}"

tile_suffix() {
  case "$1" in
    1024) echo 1K ;;
    2048) echo 2K ;;
    4096) echo 4K ;;
    8192) echo 8K ;;
    16384) echo 16K ;;
    32768) echo 32K ;;
    65536) echo 64K ;;
    *) echo "unsupported tile size: $1" >&2; return 1 ;;
  esac
}

mem_size_to_hex() {
  case "$1" in
    2GB) echo 0x80000000 ;;
    4GB) echo 0x100000000 ;;
    8GB) echo 0x200000000 ;;
    16GB) echo 0x400000000 ;;
    *) echo "unsupported mem-size: $1" >&2; return 1 ;;
  esac
}

SUF=$(tile_suffix "$TILE")
MAA_MEM_HEX=$(mem_size_to_hex "$MEM_SIZE")
MEM_TAG=$(echo "$MEM_SIZE" | tr -cd '[:alnum:]')
BIN_BASENAME=cg_maa_$SUF
BIN=$CG/$BIN_BASENAME
CKPT=$GH/ckpt_cache/cg_t${TILE}_m${MEM_TAG}
OUT=$CAMPAIGN_ROOT/cg_t${TILE}_m${MEM_TAG}_${TAG}

if [[ ! -f "$RESULTS" ]]; then
  echo -e 'timestamp\tgem5_bin\ttile\trc\tsimTicks\tmaa_cycles_total\toverlap_both_any\twrite_only_over_write\trnorm\tzeta\toutdir' > "$RESULTS"
fi

echo "[build] target=$BIN_BASENAME tile=$TILE mem=$MEM_SIZE maa_mem=$MAA_MEM_HEX"
{
  flock -x 200
  rm -f "$BIN"
  make -C "$CG" GEM5_BUILD=1 RUNTIME_DATA=1 MAA_MEM_SIZE="$MAA_MEM_HEX" "$BIN_BASENAME" \
    > "$CAMPAIGN_ROOT/build_t${TILE}.log" 2>&1
} 200>"$BUILD_LOCK"
[[ -x "$BIN" ]] || { echo "missing binary: $BIN" >&2; exit 3; }

if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
  echo "[ckpt] creating $CKPT"
  rm -rf "$CKPT"
  mkdir -p "$CKPT"
  OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" timeout "$CKPT_TIMEOUT" \
    "$GEM5_BIN" --listener-mode=off --outdir="$CKPT" "$SE" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size "$MEM_SIZE" --max-checkpoints=1 \
    --cmd "$BIN" --options MAA > "$CKPT/ckpt.log" 2>&1
  echo "[ckpt] done"
else
  echo "[ckpt] reusing $CKPT"
fi
ls "$CKPT"/cpt.* >/dev/null 2>&1 || { echo "checkpoint missing: $CKPT" >&2; exit 5; }

rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$CKPT"/cpt.* "$OUT"/
echo "[restore] CG tile=$TILE"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" timeout "$RESTORE_TIMEOUT" \
  "$GEM5_BIN" --listener-mode=off --outdir="$OUT" "$SE" \
  --cpu-type X86O3CPU -r 1 -n 4 --mem-size "$MEM_SIZE" \
  --sys-clock 3.2GHz --cpu-clock 3.2GHz \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
  --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
  --l2cache --l2_size=256kB --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
  --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports 4 --cacheline_size=64 \
  --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2 --maa_ncbus_width 32 \
  --maa --maa_num_maas 1 --maa_num_tile_elements "$TILE" --maa_l2_uncacheable --maa_l3_uncacheable \
  --maa_num_initial_row_table_slices 32 \
  --cmd "$BIN" --options MAA --prog-interval="$PROG_INTERVAL" > "$OUT/run.log" 2>&1
RC=$?
set -e

STATS=$OUT/stats.txt
SIMTICKS=$(awk '$1=="simTicks"{v=$2} END{print v}' "$STATS" 2>/dev/null || true)
MAA_CYCLES=$(awk '$1=="system.maa.cycles_TOTAL"{v=$2} END{print v}' "$STATS" 2>/dev/null || true)
OVERLAP=$(sed -n 's/.*OVERLAP_AUDIT.*both\/any=\([0-9.]*\).*/\1/p' "$OUT/run.log" | tail -1)
WRTAIL=$(sed -n 's/.*WRITE_TAIL_AUDIT.*write_only\/write=\([0-9.]*\).*/\1/p' "$OUT/run.log" | tail -1)
FINGERPRINT=$(awk '/^[[:space:]]+1[[:space:]]/ {r=$2; z=$3} END {print r "\t" z}' "$OUT/run.log")
RNORM=${FINGERPRINT%%$'\t'*}
ZETA=${FINGERPRINT#*$'\t'}
[[ "$FINGERPRINT" == *$'\t'* ]] || { RNORM=; ZETA=; }
TS=$(date +%Y-%m-%dT%H:%M:%S)
echo -e "${TS}\t${GBIN}\t${TILE}\t${RC}\t${SIMTICKS:-}\t${MAA_CYCLES:-}\t${OVERLAP:-}\t${WRTAIL:-}\t${RNORM:-}\t${ZETA:-}\t${OUT}" >> "$RESULTS"

echo "[restore] done rc=$RC ticks=${SIMTICKS:-missing} rnorm=${RNORM:-missing} zeta=${ZETA:-missing}"
rg 'iteration|ROI End|panic|fatal|Error:' "$OUT/run.log" | tail -20 || true
exit "$RC"
