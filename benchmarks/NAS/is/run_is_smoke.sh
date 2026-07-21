#!/usr/bin/env bash
# run_is_smoke.sh -- checkpoint->restore smoke for NAS IS MAA with parameterized tile size.
# Usage: run_is_smoke.sh [gem5_binary] [tile_elements] [small_class] [restore_timeout] [ckpt_timeout] [progress_frequency_hz]
#   gem5_binary   : default gem5.opt.ovl_base
#   tile_elements : default 16384 (supported: 1024,2048,4096,8192,16384,32768,65536)
#   small_class   : 1(default) builds with SMALL=1 for quicker timing smoke, 0 for default class
#
# Writes per-run logs/stats under experiments/campaigns/<date>_is_tile_smoke/
# and appends a TSV row to results.tsv.
set -euo pipefail

GH=${DX100_SOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}
RUNTIME_ROOT=${DX100_RUNTIME_ROOT:-$GH}
GBIN=${1:-gem5.opt.ovl_base}
TILE=${2:-16384}
SMALL=${3:-1}
RESTORE_TIMEOUT=${4:-${RESTORE_TIMEOUT:-1800}}
CKPT_TIMEOUT=${5:-${CKPT_TIMEOUT:-900}}
PROG_INTERVAL=${6:-${PROG_INTERVAL:-1000}}
OMP_THREADS=${OMP_THREADS:-4}
BUILD_LOCK=${BUILD_LOCK:-$GH/benchmarks/NAS/is/.build.lock}
G=${DX100_GEM5_BIN:-$RUNTIME_ROOT/build/X86/$GBIN}
RAMCFG=${DX100_RAMULATOR_CONFIG:-$RUNTIME_ROOT/ext/ramulator2/ramulator2/example_gem5_config.yaml}
SE=${DX100_SE_CONFIG:-$RUNTIME_ROOT/configs/deprecated/example/se.py}
IS_DIR=$GH/benchmarks/NAS/is
TAG=$(basename "$GBIN")
DATE_TAG=$(date +%Y-%m-%d)
CAMPAIGN_ROOT=${CAMPAIGN_ROOT:-$RUNTIME_ROOT/experiments/campaigns/${DATE_TAG}_is_tile_smoke}
CHECKPOINT_ROOT=${CHECKPOINT_ROOT:-$RUNTIME_ROOT/ckpt_cache}
RESULTS=$CAMPAIGN_ROOT/results.tsv

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

SUF=$(tile_suffix "$TILE")
TBIN_BASENAME="is_maa_${SUF}"
TBIN=$IS_DIR/$TBIN_BASENAME
SMALL_TAG=""
MAKE_SMALL=()
if [[ "$SMALL" != "0" ]]; then
  SMALL_TAG="_small"
  MAKE_SMALL=(SMALL=1)
fi

C=$CHECKPOINT_ROOT/is_maa_smoke_t${TILE}${SMALL_TAG}
O=$CAMPAIGN_ROOT/t${TILE}_${TAG}${SMALL_TAG}

mkdir -p "$CAMPAIGN_ROOT"
if [[ ! -f "$RESULTS" ]]; then
  echo -e "timestamp\tgem5_bin\ttile\tsmall\trc\tsimTicks\tmaa_cycles_total\toverlap_both_any\twrite_only_over_write\toutdir" > "$RESULTS"
fi

reuse_completed_run() {
  local stats="$O/stats.txt"
  [[ -s "$O/run.log" && -s "$stats" ]] || return 1
  grep -Eq '^IS_VERIFY .*result=PASS$' "$O/run.log" || return 1
  grep -Fqx -- 'IS_ROI_EXIT_POLICY dump_stats_verify_m5_exit' "$O/run.log" || return 1
  ! grep -Eq 'IS_VERIFY .*result=FAIL|panic:|fatal:' "$O/run.log" || return 1
  grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$O/run.log" || return 1
  local simticks maa_cycles overlap wrtail timestamp
  simticks=$(awk '$1=="simTicks"{print $2; exit}' "$stats")
  [[ -n "$simticks" ]] || return 1
  maa_cycles=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$stats")
  overlap=$(grep 'OVERLAP_AUDIT' "$O/run.log" | tail -1 | sed -n 's/.*both\/any=\([0-9.]*\).*/\1/p')
  wrtail=$(grep 'WRITE_TAIL_AUDIT' "$O/run.log" | tail -1 | sed -n 's/.*write_only\/write=\([0-9.]*\).*/\1/p')
  timestamp=$(date +%Y-%m-%dT%H:%M:%S)
  echo -e "${timestamp}\t${GBIN}\t${TILE}\t${SMALL}\t0\t${simticks}\t${maa_cycles:-}\t${overlap:-}\t${wrtail:-}\t${O}" >> "$RESULTS"
  echo "[reuse] accepted existing correctness-complete run: $O"
}

if reuse_completed_run; then
  exit 0
fi

echo "[build] target=$TBIN_BASENAME tile=$TILE small=$SMALL"
echo "[run] omp_threads=$OMP_THREADS ckpt_timeout=${CKPT_TIMEOUT}s restore_timeout=${RESTORE_TIMEOUT}s progress_frequency_hz=$PROG_INTERVAL"
{
  flock -x 200
  rm -f "$TBIN"
  make -C "$IS_DIR" GEM5_BUILD=1 VERIFY=1 "${MAKE_SMALL[@]}" "$TBIN_BASENAME" \
    > "$CAMPAIGN_ROOT/build_t${TILE}${SMALL_TAG}.log" 2>&1
} 200>"$BUILD_LOCK"

# --- step 1: checkpoint (AtomicSimpleCPU) if not present ---
if ! ls "$C"/cpt.* >/dev/null 2>&1; then
  rm -rf "$C"
  mkdir -p "$C"
  echo "[ckpt] creating checkpoint in $C ..."
  run_with_optional_timeout "$CKPT_TIMEOUT" "$G" --outdir="$C" "$SE" \
    --cpu-type AtomicSimpleCPU -n 4 --mem-size 16GB --max-checkpoints=1 \
    --cmd "$TBIN" --options "MAA" > "$C/ckpt.log" 2>&1
  echo "[ckpt] done (exit=$?)"
else
  echo "[ckpt] reusing $C"
fi

# --- step 2: restore (X86O3CPU + caches + Ramulator2 + MAA) ---
rm -rf "$O"
mkdir -p "$O"
cp -r "$C"/cpt.* "$O"/
PROGRESS_ARGS=()
if [[ "$PROG_INTERVAL" != 0 ]]; then
  PROGRESS_ARGS=(--prog-interval="$PROG_INTERVAL")
fi
echo "[restore] running $GBIN, tile=$TILE, small=$SMALL ..."
set +e
OMP_PROC_BIND=false OMP_NUM_THREADS="$OMP_THREADS" run_with_optional_timeout "$RESTORE_TIMEOUT" "$G" --outdir="$O" "$SE" \
  --cpu-type X86O3CPU -r 1 -n 4 --mem-size 16GB \
  --sys-clock 3.2GHz --cpu-clock 3.2GHz \
  --caches --l1d_size=32kB --l1d_assoc=8 --l1d-hwp-type=StridePrefetcher --l1d_mshrs=16 --l1d_write_buffers=8 \
  --l1i_size=32kB --l1i_assoc=8 --l1i-hwp-type=StridePrefetcher --l1i_mshrs=16 --l1i_write_buffers=8 \
  --l2cache --l2_size=256kB --l2_assoc=4 --l2-hwp-type=StridePrefetcher --l2_mshrs=32 --l2_write_buffers=16 \
  --l3cache --l3_size=8MB --l3_assoc=16 --l3_mshrs=256 --l3_write_buffers=128 --l3_ports 4 --cacheline_size=64 \
  --mem-type Ramulator2 --ramulator-config "$RAMCFG" --mem-channels 2 --maa_ncbus_width 32 \
  --maa --maa_num_maas 1 --maa_num_tile_elements "$TILE" --maa_l2_uncacheable --maa_l3_uncacheable \
  --maa_num_initial_row_table_slices 32 \
  --cmd "$TBIN" --options "MAA" "${PROGRESS_ARGS[@]}" > "$O/run.log" 2>&1
RC=$?
set -e
echo "[restore] done (exit=$RC)"

if [[ "$RC" == 0 ]]; then
  grep -Eq '^IS_VERIFY .*result=PASS$' "$O/run.log" || RC=90
fi
if [[ "$RC" == 0 ]]; then
  grep -Eq '^IS_ROI_EXIT_POLICY dump_stats_verify_m5_exit$' "$O/run.log" || RC=93
fi

STATS=$O/stats.txt
SIMTICKS=$(awk '$1=="simTicks"{print $2; exit}' "$STATS" 2>/dev/null || true)
MAA_CYCLES=$(awk '$1=="system.maa.cycles_TOTAL"{print $2; exit}' "$STATS" 2>/dev/null || true)
OVERLAP=$(grep 'OVERLAP_AUDIT' "$O/run.log" 2>/dev/null | tail -1 | sed -n 's/.*both\/any=\([0-9.]*\).*/\1/p' || true)
WRTAIL=$(grep 'WRITE_TAIL_AUDIT' "$O/run.log" 2>/dev/null | tail -1 | sed -n 's/.*write_only\/write=\([0-9.]*\).*/\1/p' || true)
TS=$(date +%Y-%m-%dT%H:%M:%S)

[[ -n "$SIMTICKS" ]] || { [[ "$RC" != 0 ]] || RC=91; }
grep -Eq 'Exiting @ tick .*m5_exit instruction encountered' "$O/run.log" || { [[ "$RC" != 0 ]] || RC=92; }

echo -e "${TS}\t${GBIN}\t${TILE}\t${SMALL}\t${RC}\t${SIMTICKS:-}\t${MAA_CYCLES:-}\t${OVERLAP:-}\t${WRTAIL:-}\t${O}" >> "$RESULTS"

echo "===== results ($GBIN, tile=$TILE, small=$SMALL) ====="
grep -E "ROI End|successfull|iteration:" "$O/run.log" || true
grep -E "OVERLAP_AUDIT|WRITE_TAIL_AUDIT" "$O/run.log" || true
echo "[results] appended to $RESULTS"

exit "$RC"
