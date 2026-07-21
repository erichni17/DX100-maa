#!/usr/bin/env bash
# run_gapbs_tile_smoke.sh -- checkpoint->restore tile-size smoke for GAPBS kernels.
# Usage:
#   run_gapbs_tile_smoke.sh [gem5_binary] [kernel] [tile] [scale] [iters] [mem_size] [restore_timeout] [ckpt_timeout] [prog_interval]
# Examples:
#   run_gapbs_tile_smoke.sh gem5.opt.ovl_base bfs 4096 22 1 2GB
#   run_gapbs_tile_smoke.sh gem5.opt.ovl_base pr  16384 22 1 2GB
#   run_gapbs_tile_smoke.sh gem5.opt.ovl_base sssp 8192 22 1 2GB
set -euo pipefail

GH=${DX100_SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
RUNTIME_ROOT=${DX100_RUNTIME_ROOT:-$GH}
GAP=$GH/benchmarks/gapbs
SE=${DX100_SE_CONFIG:-$RUNTIME_ROOT/configs/deprecated/example/se.py}
RAMCFG=${DX100_RAMULATOR_CONFIG:-$RUNTIME_ROOT/ext/ramulator2/ramulator2/example_gem5_config.yaml}

GBIN=${1:-gem5.opt.ovl_base}
KERNEL=${2:-bfs}
TILE=${3:-16384}
SCALE=${4:-22}
ITERS=${5:-1}
MEM_SIZE=${6:-2GB}
RESTORE_TIMEOUT=${7:-${RESTORE_TIMEOUT:-14400}}
CKPT_TIMEOUT=${8:-${CKPT_TIMEOUT:-3600}}
PROG_INTERVAL=${9:-${PROG_INTERVAL:-1000}}
OMP_THREADS=${OMP_THREADS:-4}
BUILD_LOCK=${BUILD_LOCK:-$GAP/.build.lock}

GEM5_BIN=${DX100_GEM5_BIN:-$RUNTIME_ROOT/build/X86/$GBIN}
TAG=$(basename "$GBIN")
DATE_TAG=$(date +%Y-%m-%d)
CAMPAIGN_ROOT=${CAMPAIGN_ROOT:-$RUNTIME_ROOT/experiments/campaigns/${DATE_TAG}_gapbs_tile_smoke}
CHECKPOINT_ROOT=${CHECKPOINT_ROOT:-$RUNTIME_ROOT/ckpt_cache}
RESULTS=$CAMPAIGN_ROOT/results.tsv

mkdir -p "$CAMPAIGN_ROOT"

# Long simulations keep reading shell input as they progress. Run an immutable
# campaign-local snapshot so edits to this source cannot corrupt an active job.
if [[ "${GAPBS_FROZEN_RUNNER:-0}" != 1 ]]; then
  RUNNER_SNAPSHOT="$CAMPAIGN_ROOT/runner_$(date +%Y%m%d_%H%M%S)_$$.sh"
  cp -- "${BASH_SOURCE[0]}" "$RUNNER_SNAPSHOT"
  chmod +x "$RUNNER_SNAPSHOT"
  exec env GAPBS_FROZEN_RUNNER=1 "$RUNNER_SNAPSHOT" "$@"
fi

export LD_LIBRARY_PATH="${DX100_RAMULATOR_LIBDIR:-$RUNTIME_ROOT/ext/ramulator2/ramulator2}:${LD_LIBRARY_PATH:-}"

run_with_optional_timeout() {
  local seconds=$1
  shift
  if [[ "$seconds" == 0 ]]; then
    "$@"
  else
    timeout "$seconds" "$@"
  fi
}

prepare_graph() {
  local target=$1
  shift
  local temporary="${target}.tmp.$$"
  if [[ -s "$target" ]]; then
    return 0
  fi
  echo "[prep] generating graph: $target"
  if "$GAP/converter" -u "$SCALE" "$@" -b "$temporary"; then
    mv -f "$temporary" "$target"
  else
    local rc=$?
    rm -f "$temporary"
    return "$rc"
  fi
}

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

SUF=$(tile_suffix "$TILE")
BIN_BASENAME="${KERNEL}_maa_${SUF}"
BIN="$GAP/$BIN_BASENAME"
MAA_MEM_HEX=$(mem_size_to_hex "$MEM_SIZE")
MEM_TAG=$(echo "$MEM_SIZE" | tr -cd '[:alnum:]')

GRAPH_SG="$GAP/serialized_graph_${SCALE}.sg"
GRAPH_WSG="$GAP/serialized_graph_${SCALE}.wsg"
OPTS=""
VERIFY_FLAGS=""

case "$KERNEL" in
  bc)
    OPTS="-f $GRAPH_SG -n 1 -i $ITERS -v"
    VERIFY_FLAGS="-DBC_VERIFY_AFTER_ROI=1"
    ;;
  bfs)
    OPTS="-f $GRAPH_SG -l -n 1 -v"
    VERIFY_FLAGS="-DBFS_FP_ENABLE=1"
    ;;
  pr)
    OPTS="-f $GRAPH_SG -n 1 -i $ITERS -v"
    VERIFY_FLAGS="-DPR_FP_ENABLE=1"
    ;;
  sssp)
    OPTS="-f $GRAPH_WSG -n 1 -v"
    VERIFY_FLAGS="-DSSSP_FP_ENABLE=1"
    ;;
  *)
    echo "unsupported kernel: $KERNEL (supported: bc|bfs|pr|sssp)" >&2
    exit 2
    ;;
esac

CKPT="$CHECKPOINT_ROOT/gapbs_${KERNEL}_s${SCALE}_t${TILE}_m${MEM_TAG}"
OUT="$CAMPAIGN_ROOT/${KERNEL}_s${SCALE}_t${TILE}_m${MEM_TAG}_${TAG}"

if [[ ! -f "$RESULTS" ]]; then
  echo -e "timestamp\tgem5_bin\tkernel\ttile\tscale\titers\trc\tsimTicks\tmaa_cycles_total\toverlap_both_any\twrite_only_over_write\toutdir" > "$RESULTS"
fi

correctness_marker_present() {
  local pattern
  case "$KERNEL" in
    bc) pattern='^BC_VALIDATION_END result=PASS$' ;;
    bfs) pattern='^BFS_FP .*invalid_chains=0 ' ;;
    pr) pattern='^PR_FP .*nonfinite=0 unquantizable=0$' ;;
    sssp) pattern='^SSSP_FINGERPRINT .*result=PASS$' ;;
  esac
  [[ $(grep -Ec "$pattern" "$OUT/run.log" || true) == 1 ]]
}

reuse_completed_run() {
  local stats="$OUT/stats.txt"
  [[ -s "$OUT/run.log" && -s "$stats" ]] || return 1
  correctness_marker_present || return 1
  ! grep -Eq 'panic:|fatal:' "$OUT/run.log" || return 1
  grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$OUT/run.log" || return 1
  local simticks maa_cycles overlap wrtail timestamp
  simticks=$(awk '$1=="simTicks"{print $2; exit}' "$stats")
  [[ -n "$simticks" ]] || return 1
  maa_cycles=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$stats")
  overlap=$(grep 'OVERLAP_AUDIT' "$OUT/run.log" | tail -1 | sed -n 's/.*both\/any=\([0-9.]*\).*/\1/p')
  wrtail=$(grep 'WRITE_TAIL_AUDIT' "$OUT/run.log" | tail -1 | sed -n 's/.*write_only\/write=\([0-9.]*\).*/\1/p')
  timestamp=$(date +%Y-%m-%dT%H:%M:%S)
  echo -e "${timestamp}\t${GBIN}\t${KERNEL}\t${TILE}\t${SCALE}\t${ITERS}\t0\t${simticks}\t${maa_cycles:-}\t${overlap:-}\t${wrtail:-}\t${OUT}" >> "$RESULTS"
  echo "[reuse] accepted existing correctness-complete run: $OUT"
}

if reuse_completed_run; then
  exit 0
fi

echo "[build] kernel=$KERNEL target=$BIN_BASENAME tile=$TILE mem=$MEM_SIZE maa_mem=$MAA_MEM_HEX"
echo "[run] omp_threads=$OMP_THREADS ckpt_timeout=${CKPT_TIMEOUT}s restore_timeout=${RESTORE_TIMEOUT}s prog_interval=$PROG_INTERVAL"
echo "[build] waiting for lock: $BUILD_LOCK"
{
  flock -x 200
  rm -f "$BIN"
  make -C "$GAP" GEM5_BUILD=1 MAA_MEM_SIZE="$MAA_MEM_HEX" \
    EXTRA_CXX_FLAGS="$VERIFY_FLAGS" "$BIN_BASENAME" converter \
    > "$CAMPAIGN_ROOT/build_${KERNEL}_t${TILE}.log" 2>&1
  if [[ "$KERNEL" == sssp ]]; then
    prepare_graph "$GRAPH_WSG" -w
  else
    prepare_graph "$GRAPH_SG"
  fi
} 200>"$BUILD_LOCK"

[[ -f "$BIN" ]] || { echo "missing binary after build: $BIN" >&2; exit 3; }

# --- step 1: checkpoint ---
if ! ls "$CKPT"/cpt.* >/dev/null 2>&1; then
  echo "[ckpt] creating checkpoint in $CKPT"
  rm -rf "$CKPT"
  mkdir -p "$CKPT"
  OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" run_with_optional_timeout "$CKPT_TIMEOUT" "$GEM5_BIN" --listener-mode=off --outdir="$CKPT" "$SE" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size "$MEM_SIZE" --max-checkpoints=1 \
    --cmd "$BIN" --options "$OPTS" > "$CKPT/ckpt.log" 2>&1
  echo "[ckpt] done (exit=$?)"
else
  echo "[ckpt] reusing $CKPT"
fi

ls "$CKPT"/cpt.* >/dev/null 2>&1 || { echo "checkpoint missing for $KERNEL t$TILE" >&2; exit 5; }

# --- step 2: restore ---
rm -rf "$OUT"
mkdir -p "$OUT"
cp -r "$CKPT"/cpt.* "$OUT"/
echo "[restore] running $KERNEL tile=$TILE scale=$SCALE"
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" run_with_optional_timeout "$RESTORE_TIMEOUT" "$GEM5_BIN" --listener-mode=off --outdir="$OUT" "$SE" \
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

if [[ "$RC" == 0 ]]; then
  case "$KERNEL" in
    bc) grep -Eq '^BC_VALIDATION_END result=PASS$' "$OUT/run.log" || RC=90 ;;
    bfs) grep -Eq '^BFS_FP .*invalid_chains=0 ' "$OUT/run.log" || RC=90 ;;
    pr) grep -Eq '^PR_FP .*nonfinite=0 unquantizable=0$' "$OUT/run.log" || RC=90 ;;
    sssp) grep -Eq '^SSSP_FINGERPRINT .*result=PASS$' "$OUT/run.log" || RC=90 ;;
  esac
fi

STATS="$OUT/stats.txt"
SIMTICKS=$(awk '$1=="simTicks"{print $2; exit}' "$STATS" 2>/dev/null || true)
MAA_CYCLES=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$STATS" 2>/dev/null || true)
OVERLAP=$(grep 'OVERLAP_AUDIT' "$OUT/run.log" 2>/dev/null | tail -1 | sed -n 's/.*both\/any=\([0-9.]*\).*/\1/p' || true)
WRTAIL=$(grep 'WRITE_TAIL_AUDIT' "$OUT/run.log" 2>/dev/null | tail -1 | sed -n 's/.*write_only\/write=\([0-9.]*\).*/\1/p' || true)
TS=$(date +%Y-%m-%dT%H:%M:%S)

[[ -n "$SIMTICKS" ]] || { [[ "$RC" != 0 ]] || RC=91; }
grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$OUT/run.log" || { [[ "$RC" != 0 ]] || RC=92; }

echo -e "${TS}\t${GBIN}\t${KERNEL}\t${TILE}\t${SCALE}\t${ITERS}\t${RC}\t${SIMTICKS:-}\t${MAA_CYCLES:-}\t${OVERLAP:-}\t${WRTAIL:-}\t${OUT}" >> "$RESULTS"

echo "===== results ($KERNEL, tile=$TILE) ====="
grep -E "ROI End|iteration:|Verif|correct|PASS|FAIL|m5_exit|panic|fatal" "$OUT/run.log" | tail -30 || true
grep -E "OVERLAP_AUDIT|WRITE_TAIL_AUDIT" "$OUT/run.log" | tail -8 || true
echo "[results] appended to $RESULTS"

exit "$RC"
